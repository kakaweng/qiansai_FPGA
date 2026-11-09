"""Single-window presentation of RK video and RK-owned decisions.

HTTP runs off the GUI thread. This module never computes driving permissions.
Client-side expiry only prevents stale board state being presented as live.
"""
import copy
import json
import os
from pathlib import Path
import queue
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import build_opener, ProxyHandler, Request

STATE_NAMES = {
    'IDLE': '等待开始', 'PREPARING': '视频源回到起点', 'WAIT_READY': '等待两路首帧',
    'STARTING': '正在开始播放', 'WAIT_FRAMES': '等待播放帧配对', 'RUNNING': '检测中',
    'SYNC_LOST': '同步丢失，暂停联合判断', 'ENDED': '录像结束',
    'STOPPING': '正在停止', 'STOPPED': '已停止', 'ERROR': '控制服务故障',
}
FEEDBACK = {
    'FRONT_VEHICLE_REVIEW': ('\u524d\u65b9\u68c0\u6d4b\u5230\u8f66\u8f86\uff0c\u8bf7\u89c2\u5bdf', '\u524d\u89c6\u68c0\u6d4b\u5230\u8f66\u8f86\uff1b\u4fa7\u65b9\u548c\u540e\u65b9\u5b89\u5168\u672a\u9a8c\u8bc1\u3002'),
    'IDLE': ('等待变道请求', '点击左 / 右变道，或按 A / D；请求持续反馈10秒。'),
    'CANCELLED': ('请求已取消', '转向意图已清除。'),
    'EXPIRED': ('请求已结束', '本次10秒观察时间已到，可以重新请求。'),
    'NOT_RUNNING': ('当前无法判断', '请先开始检测，确认视频配对正常。'),
    'LANE_STALE': ('暂缓变道：车道结果失效', '缺少有效车道证据，请继续观察。'),
    'SOLID_BLOCK': ('禁止跨越实线变道', '所选一侧为实线：请保持当前车道。'),
    'LINE_UNKNOWN': ('暂缓变道：边界待确认', '当前不能确认所选一侧为虚线。'),
    'SIDE_STALE': ('边界为虚线，侧方证据不足', '侧方检测缺失或过期，请检查侧方视频。'),
    'LEFT_VEHICLE_MANUAL': ('左侧检测到车辆，请人工观察', '虚线；左侧同步未确认、区域未标定，不能判断冲突。'),
    'LEFT_MANUAL': ('左侧需人工确认', '虚线；未检测到车辆，左侧同步未确认、区域未标定。'),
    'SIDE_TIME_MISMATCH': ('右侧识别结果时间不匹配', '虚线；右侧结果与当前前方源时间差超过250ms。'),
    'SIDE_VEHICLE_REVIEW': ('右侧检测到车辆，请观察', '虚线；区域未标定，不能确认是否占用目标车道。'),
    'DASHED_REVIEW': ('边界为虚线，请确认右侧环境', '未检测到车辆；区域未标定，仍需人工观察。'),
}
# Coordinates are in the displayed image, as supplied by HighGUI mouse events.
BUTTONS = (
    ('开始 / 重播 [空格]', 'start'), ('停止检测 [X]', 'stop'), ('取消请求 [K]', 'cancel'),
    ('向左变道 [A]', 'left'), ('向右变道 [D]', 'right'), ('模拟制动 [B]', 'brake'),
    ('模拟减速 [-]', 'slower'), ('模拟加速 [+]', 'faster'), ('P挡 [P]', 'P'),
    ('N挡 [N]', 'N'), ('D挡 [1]', 'D'), ('R挡 [2]', 'R'),
)
KEY_ACTIONS = {32: 'start', ord('x'): 'stop', ord('k'): 'cancel', ord('a'): 'left',
               ord('d'): 'right', ord('b'): 'brake', ord('-'): 'slower', ord('+'): 'faster',
               ord('='): 'faster', ord('p'): 'P', ord('n'): 'N', ord('1'): 'D', ord('2'): 'R'}


def key_action(key):
    if ord('A') <= key <= ord('Z'):
        key += 32
    return KEY_ACTIONS.get(key)


class DriveClient(threading.Thread):
    def __init__(self, address='http://127.0.0.1:9011', interval=.1, timeout=.7):
        super().__init__(daemon=True)
        parsed = urlsplit(address)
        if parsed.scheme != 'http' or not parsed.hostname or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('控制地址应为 http://主机:端口，例如 http://127.0.0.1:9011')
        self.address = address.rstrip('/')
        self.interval, self.timeout = interval, timeout
        self.opener = build_opener(ProxyHandler({}))
        self.lock, self.stop_event = threading.Lock(), threading.Event()
        self.commands = queue.Queue(maxsize=8)
        self.state, self.received_at, self.connected = None, 0., False
        self.notice, self.revision = '', 0

    def request(self, route, post=False):
        req = Request(self.address + route, data=b'{}' if post else None,
                      headers={'X-Sync-Control': '1', 'Content-Type': 'application/json'})
        with self.opener.open(req, timeout=self.timeout) as response:
            return json.load(response)

    def submit(self, action):
        if not self.allowed_action(action):
            return False
        try:
            self.commands.put_nowait(action)
        except queue.Full:
            with self.lock:
                self.notice = '操作队列已满，请等待当前请求反馈。'
                self.revision += 1
            return False
        with self.lock:
            self.notice = '请求已提交，等待板端反馈…'
            self.revision += 1
        return True

    def allowed_action(self, action):
        return action in {item[1] for item in BUTTONS}

    def command_route(self, action):
        return '/api/' + action if action in ('start', 'stop') else '/api/drive?' + urlencode({'action': action})

    def snapshot(self):
        with self.lock:
            return {'state': copy.deepcopy(self.state), 'received_at': self.received_at,
                    'connected': self.connected, 'notice': self.notice, 'revision': self.revision}

    def run(self):
        next_poll = 0.
        while not self.stop_event.is_set():
            try:
                action = self.commands.get(timeout=max(.01, min(.1, next_poll - time.monotonic())))
            except queue.Empty:
                action = None
            if action:
                try:
                    route = self.command_route(action)
                    self.request(route, post=True)
                    with self.lock:
                        if action in ('start', 'stop'):
                            self.state = None
                        self.notice = '板端已接收操作。'
                        self.revision += 1
                except HTTPError as error:
                    try:
                        detail = json.load(error).get('error', '板端拒绝了该操作。')
                    except (OSError, ValueError, AttributeError):
                        detail = '板端拒绝了该操作。'
                    with self.lock:
                        self.notice = str(detail)
                        self.revision += 1
                except (OSError, ValueError) as error:
                    with self.lock:
                        self.notice = '操作结果未确认，请检查连接后重试。'
                        self.revision += 1
                    print('驾驶面板操作失败：' + str(error), flush=True)
                next_poll = 0.
            if time.monotonic() >= next_poll:
                try:
                    state = self.request('/api/status')
                    if not isinstance(state, dict) or not isinstance(state.get('state'), str):
                        raise ValueError('板端状态格式无效')
                    with self.lock:
                        self.state, self.received_at, self.connected = state, time.monotonic(), True
                        self.revision += 1
                except (OSError, ValueError):
                    with self.lock:
                        self.connected = False
                        self.revision += 1
                next_poll = time.monotonic() + self.interval

    def stop(self):
        self.stop_event.set()


def presentation(snapshot, video_fresh, now=None):
    """Normalize display state; never retain a positive stale board verdict."""
    now = time.monotonic() if now is None else now
    state = snapshot.get('state') or {}
    elapsed = now - snapshot.get('received_at', 0)
    live = bool(snapshot.get('connected') and 0 <= elapsed <= 1 and video_fresh)
    drive = copy.deepcopy(state.get('drive') or {})
    if not isinstance(drive, dict):
        drive = {}
    if drive:
        arrays = (('line', 2), ('vehicles', 3), ('object_age_ms', 3), ('object_valid', 3))
        if any(not isinstance(drive.get(name), list) or len(drive[name]) != length for name, length in arrays):
            drive = {}
        elif any(type(x) is not int or x not in (0, 1, 2) for x in drive['line']) or drive.get('signal') not in (0, 1, 2, 3) or drive.get('request') not in (0, 1, 2):
            drive = {}
    model = {'live': live, 'status': STATE_NAMES.get(state.get('state'), '等待板端状态'),
             'notice': snapshot.get('notice', ''), 'session': state.get('session', 0),
             'pair_age': state.get('pair_age_ms', -1), 'request': drive.get('request', 0),
             'line': [0, 0], 'signal': 0, 'counts': [None] * 3, 'ages': [-1] * 3,
             'lane_age': -1, 'lane_stale': False, 'speed': None, 'gear': '—', 'alarm': False,
             'people': None, 'front_only': bool(drive.get('front_only', False))}
    if not live:
        model['status'] = '视频已中断 / 待刷新' if not video_fresh else '控制连接已中断 / 待刷新'
        model['request'] = 0
        model['title'], model['detail'] = '当前证据不可用', '暂停依据旧画面和旧状态判断，等待连接恢复。'
        return model
    if not drive:
        model['title'], model['detail'] = '等待驾驶辅助服务', '当前板端服务没有驾驶状态，请先完成第六版部署。'
        return model
    model['speed'], model['gear'] = drive.get('speed_kmh'), drive.get('gear', '—')
    running = state.get('state') == 'RUNNING'
    add_age = max(0, int(elapsed * 1000))
    lane_age = drive.get('lane_age_ms', -1)
    model['lane_age'] = lane_age + add_age if lane_age >= 0 else -1
    model['lane_stale'] = running and model['lane_age'] > 750
    lane_valid = running and 0 <= model['lane_age'] <= 750
    if lane_valid:
        model['line'] = drive.get('line', [0, 0])
    valid = drive.get('object_valid', [False] * 3)
    ages, counts = drive.get('object_age_ms', [-1] * 3), drive.get('vehicles', [0] * 3)
    for index in range(3):
        age = ages[index] + add_age if ages[index] >= 0 else -1
        model['ages'][index] = age
        if running and valid[index] and 0 <= age <= 750:
            model['counts'][index] = counts[index]
    if model['counts'][0] is not None:
        model['signal'], model['people'] = drive.get('signal', 0), drive.get('people', 0)
    feedback = drive.get('feedback', 'IDLE')
    model['title'], model['detail'] = FEEDBACK.get(feedback, ('等待板端反馈', '正在读取板端状态。'))
    model['alarm'] = bool(drive.get('solid_alarm') and lane_valid)
    if model['request'] and running:
        side = model['request']
        if not lane_valid or (not model['front_only'] and feedback in ('LEFT_MANUAL', 'LEFT_VEHICLE_MANUAL', 'SIDE_VEHICLE_REVIEW', 'DASHED_REVIEW') and model['counts'][side] is None):
            model['alarm'] = False
            model['title'], model['detail'] = '感知证据已过期，等待更新', '继续观察，板端新结果到达后恢复反馈。'
    return model


class DriveDashboard:
    WIDTH, VIDEO_HEIGHT, PANEL_HEIGHT = 1280, 480, 340

    def __init__(self, client, font_path=None, front_only=False):
        from PIL import ImageFont
        self.client, self.cache_key, self.cached = client, None, None
        self.front_only = bool(front_only)
        root = Path(__file__).resolve().parent
        candidates = [font_path, os.environ.get('PANGO_CJK_FONT'),
                      root / 'preview_fonts/PangoPreviewSans.ttf',
                      '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                      '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
                      '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
                      'C:/Windows/Fonts/msyh.ttc']
        selected = next((Path(p) for p in candidates if p and Path(p).is_file()), None)
        if selected is None:
            raise ValueError('找不到中文字体；请同步 preview_fonts 目录，或通过 --panel-font 指定中文字体。')
        self.font_path = selected
        self.fonts = {size: ImageFont.truetype(str(selected), size) for size in (16, 18, 20, 24, 28)}
        self.buttons = []
        for index, (label, action) in enumerate(BUTTONS):
            x, y = 795 + index % 3 * 156, 58 + index // 3 * 50
            self.buttons.append(((x, y, x + 146, y + 42), label, action))
        self.last_model = None

    def action_at(self, x, y):
        y -= self.VIDEO_HEIGHT
        for (left, top, right, bottom), _, action in self.buttons:
            if left <= x < right and top <= y < bottom:
                return action
        return None

    def click(self, event, x, y, flags=None, parameter=None):
        if event == 1:  # cv2.EVENT_LBUTTONDOWN; no GUI import needed for tests.
            action = self.action_at(x, y)
            if action:
                self.client.submit(action)

    @staticmethod
    def age(value):
        return '无结果' if value < 0 else str(value) + 'ms'

    def render_panel(self, model):
        import numpy as np
        from PIL import Image, ImageDraw
        image = Image.new('RGB', (self.WIDTH, self.PANEL_HEIGHT), '#101a2b')
        draw = ImageDraw.Draw(image)
        def measured(value, size):
            if hasattr(draw, 'textbbox'):
                box = draw.textbbox((0, 0), value, font=self.fonts[size])
                return box[2] - box[0]
            return draw.textsize(value, font=self.fonts[size])[0]
        def text(x, y, value, size=20, color='#edf2fa'):
            draw.text((x, y), str(value), font=self.fonts[size], fill=color)
        def fitted(x, y, value, limit, size=18, color='#afc0d6'):
            line = ''
            for character in str(value):
                candidate = line + character
                # Pillow 7 compatibility: getlength/textbbox are not required.
                if measured(candidate, size) > limit:
                    text(x, y, line, size, color); y += size + 7; line = character
                else:
                    line = candidate
            if line:
                text(x, y, line, size, color)
        text(18, 10, '智能辅助驾驶', 24)
        text(205, 16, model['status'], 18, '#91c8ff' if model['live'] else '#ffb3a7')
        text(795, 14, '同窗操作 · 判断由RK3568执行', 18)
        draw.line((18, 48, 1262, 48), fill='#34445c')
        names = ['已过期' if model.get('lane_stale') else '待确认', '实线', '虚线']
        text(20, 61, '左侧 ' + names[model['line'][0]] + '    右侧 ' + names[model['line'][1]], 24)
        colors = ['#afc0d6', '#ff8b80', '#ffda7f', '#80dfad']
        text(370, 61, '灯色：' + ['待确认', '红灯', '黄灯', '绿灯'][model['signal']], 24, colors[model['signal']])
        speed = '待确认' if model['speed'] is None else str(model['speed']) + ' km/h · ' + model['gear'] + '挡'
        text(20, 103, '模拟车辆：' + speed, 20)
        text(370, 106, '转向意图：' + ['无', '左', '右'][model['request']], 20)
        color = '#42232b' if model['alarm'] else '#1b2c43'
        draw.rectangle((18, 143, 767, 229), fill=color, outline='#ed756b' if model['alarm'] else '#415879', width=2)
        text(30, 151, model['title'], 24, '#ffaaa0' if model['alarm'] else '#f4d38c')
        fitted(30, 190, model['detail'], 725, 16)
        rows = []
        for index, name in enumerate(('前方', '左侧', '右侧')):
            value = '待确认' if model['counts'][index] is None else str(model['counts'][index]) + '辆'
            rows.append(name + ' ' + value + ' / ' + self.age(model['ages'][index]))
        text(20, 239, '   '.join(rows), 18)
        text(20, 269, '车道年龄 ' + self.age(model['lane_age']) + ' · 左侧手动对齐 · 侧方未标定', 16)
        text(20, 298, '行人仅展示；灯色不代表转弯许可。S截图，Q退出。', 16)
        for (left, top, right, bottom), label, action in self.buttons:
            fill = '#24578a' if action in ('start', 'left', 'right') else '#713a39' if action == 'stop' else '#233750'
            draw.rectangle((left, top, right, bottom), fill=fill, outline='#4a6282')
            size = 16 if measured(label, 18) > right - left - 8 else 18
            width = measured(label, size)
            text(left + max(4, (right - left - width) // 2), top + 8, label, size)
        fitted(795, 269, model['notice'], 457, 16, '#c1d3e9')
        return np.array(image)[:, :, ::-1].copy()

    def compose(self, video, video_fresh, snapshot=None, now=None):
        import numpy as np
        now = time.monotonic() if now is None else now
        snapshot = self.client.snapshot() if snapshot is None else snapshot
        model = presentation(snapshot, video_fresh, now)
        # Age counters refresh at 5 Hz; an expiry or changed verdict redraws
        # immediately, including transitions inside the same 200ms text tick.
        validity = (model['live'], model['alarm'], tuple(model['line']), model['signal'],
                    tuple(x is not None for x in model['counts']), model['title'])
        key = (int(now * 5), snapshot.get('revision'), video_fresh, validity)
        if key != self.cache_key:
            self.cached, self.cache_key = self.render_panel(model), key
        self.last_model = model
        if video.shape[:2] != (self.VIDEO_HEIGHT, self.WIDTH):
            if not self.front_only:
                raise ValueError('统一驾驶预览需要1280×480的三路拼接画面，请使用板端 --source all')
            canvas = np.zeros((self.VIDEO_HEIGHT, self.WIDTH, 3), dtype=video.dtype)
            height, width = video.shape[:2]
            if height <= 0 or width <= 0:
                raise ValueError('前方 HDMI 画面尺寸无效')
            scale = min(self.WIDTH / width, self.VIDEO_HEIGHT / height)
            fitted_width = max(1, min(self.WIDTH, int(round(width * scale))))
            fitted_height = max(1, min(self.VIDEO_HEIGHT, int(round(height * scale))))
            import cv2
            fitted = cv2.resize(video, (fitted_width, fitted_height))
            offset_x = (self.WIDTH - fitted_width) // 2
            offset_y = (self.VIDEO_HEIGHT - fitted_height) // 2
            canvas[offset_y:offset_y + fitted_height, offset_x:offset_x + fitted_width] = fitted
            video = canvas
        return np.concatenate((video, self.cached), axis=0)
