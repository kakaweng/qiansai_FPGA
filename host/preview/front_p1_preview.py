"""HDMI P1 controls and presentation; all driving decisions come from RK3568."""
import time
from pathlib import Path
from urllib.parse import urlencode

from drive_preview import DriveClient, DriveDashboard, presentation

# (label, action). Labels are drawn on the buttons; the key hint is drawn separately.
P1_BUTTONS = (
    ('开始检测', 'start'), ('停止检测', 'stop'), ('点选交通灯', 'bind'),
    ('左转', 'turn_left'), ('直行', 'straight'), ('右转', 'turn_right'),
    ('确认灯并起步', 'go'), ('模拟停车', 'brake'), ('强行行驶演示', 'force_go'),
    ('向左变道', 'left'), ('向右变道', 'right'), ('取消变道', 'cancel'),
    ('恢复自动', 'unbind'),
)
P1_KEYS = {32: 'start', ord('x'): 'stop', ord('t'): 'bind', ord('j'): 'turn_left',
           ord('i'): 'straight', ord('l'): 'turn_right', ord('g'): 'go', ord('b'): 'brake',
           ord('f'): 'force_go', ord('a'): 'left', ord('d'): 'right', ord('k'): 'cancel', ord('c'): 'unbind'}
KEY_HINTS = {'start': '空格', 'stop': 'X', 'bind': 'T', 'turn_left': 'J', 'straight': 'I',
             'turn_right': 'L', 'go': 'G', 'brake': 'B', 'force_go': 'F', 'left': 'A',
             'right': 'D', 'cancel': 'K', 'unbind': 'C'}
REASONS = {
    'SELECT_DIRECTION': '先选择路口方向：左转、直行或右转',
    'BIND_LIGHT': '请点击该方向对应的交通灯（LIGHT 框）',
    'WAIT_SIGNAL': '灯色待确认，保持模拟停车',
    'MULTIPLE_LIGHTS': '框内有多盏灯，请直接点击其中一盏',
    'NOT_RUNNING': '等待检测运行和新鲜前视画面',
    'RED_STOP': '红灯停车；起步请求等待灯色变化',
    'YELLOW_WAIT': '黄灯等待，保持模拟停车',
    'GREEN_READY': '绿灯已确认，按 G 模拟起步',
    'GREEN_GO': '对应灯为绿灯，模拟车辆通行',
    'AUTO_GREEN_READY': '已识别绿灯；按 G 确认当前灯并模拟起步',
    'CHOOSE_LIGHT': '已识别绿灯；点击本方向对应的灯再起步',
    'COLOR_CONFLICT': '多个交通灯颜色不同，请点击对应的灯',
    'SIGNAL_HOLD': '灯色短时保持，等待新证据后恢复模拟通行',
    'FORCED_MOVE': '强行行驶演示中，2 秒后自动停车',
    'WAIT_PEDESTRIAN': '检测到行人，请礼让行人',
    'WAIT_PERCEPTION': '等待有效行人检测，暂缓右转',
    'RIGHT_READY': '当前未检测到行人，可右转；按 G 开始演示',
    'RIGHT_GO': '当前未检测到行人，可右转',
}
REASON_KINDS = {
    'SELECT_DIRECTION': 'action', 'BIND_LIGHT': 'action', 'MULTIPLE_LIGHTS': 'action',
    'GREEN_READY': 'action', 'AUTO_GREEN_READY': 'action', 'CHOOSE_LIGHT': 'action',
    'COLOR_CONFLICT': 'action', 'WAIT_SIGNAL': 'pending', 'NOT_RUNNING': 'pending',
    'RED_STOP': 'ok', 'YELLOW_WAIT': 'ok', 'GREEN_GO': 'ok', 'SIGNAL_HOLD': 'hold',
    'FORCED_MOVE': 'alarm',
    'WAIT_PEDESTRIAN': 'observe', 'WAIT_PERCEPTION': 'pending',
    'RIGHT_READY': 'action', 'RIGHT_GO': 'ok',
}
# Display categories: (legend label, accent colour). Every use pairs colour with text.
KINDS = {
    'ok': ('正常', '#34d399'), 'observe': ('需人工观察', '#38bdf8'), 'alarm': ('报警', '#f87171'),
    'hold': ('结果保持', '#fbbf24'), 'pending': ('待确认', '#94a3b8'),
    'action': ('待操作', '#60a5fa'), 'stale': ('断流', '#a78bfa'),
}
SIGNAL_NAMES = ('待确认', '红灯', '黄灯', '绿灯')
SIGNAL_COLORS = ('#94a3b8', '#ff5a52', '#ffc531', '#2ee68a')
LINE_NAMES = ('待确认', '实线', '虚线')
BG, HEADER, CARD, BORDER = '#0a0f1a', '#0e1628', '#131c2e', '#24324d'
TEXT, MUTED, DIM = '#e8eef8', '#93a3bb', '#5d6c86'


class P1Layout:
    """Single source of window geometry: video mapping, black-border exclusion and hit tests."""
    WIDTH, HEIGHT = 1600, 900
    # Effective HDMI content inside the board's 1280x480 canvas.
    SOURCE_X, SOURCE_W, SOURCE_H = 213, 853, 480
    VIDEO_X, VIDEO_Y, VIDEO_W, VIDEO_H = 16, 68, 1152, 648
    SIDE_X0, SIDE_X1 = 1184, 1584
    BANNER = (16, 726, 1168, 772)
    CARDS = {'light': (1184, 68, 1584, 250), 'direction': (1184, 260, 1584, 410),
             'speed': (1184, 420, 1584, 520), 'lane': (1184, 530, 1584, 800),
             'legend': (1184, 810, 1584, 890)}
    GROUP_LABEL_Y = 791
    GROUPS = ((16, '检测'), (180, '路口通行'), (724, '变道请求'), (1000, '报警演示'))
    SEPARATORS = (168, 712, 988)
    BUTTONS = {
        'start': (16, 804, 156, 844), 'stop': (16, 850, 156, 890),
        'turn_left': (180, 804, 284, 844), 'straight': (292, 804, 396, 844),
        'turn_right': (404, 804, 508, 844), 'bind': (180, 850, 340, 890),
        'unbind': (348, 850, 508, 890), 'go': (524, 804, 700, 844), 'brake': (524, 850, 700, 890),
        'left': (724, 804, 846, 844), 'right': (854, 804, 976, 844), 'cancel': (724, 850, 976, 890),
        'force_go': (1000, 804, 1168, 890),
    }

    def video_rect(self):
        return (self.VIDEO_X, self.VIDEO_Y, self.VIDEO_X + self.VIDEO_W, self.VIDEO_Y + self.VIDEO_H)

    def inside_video(self, x, y):
        return (self.VIDEO_X <= x < self.VIDEO_X + self.VIDEO_W and
                self.VIDEO_Y <= y < self.VIDEO_Y + self.VIDEO_H)

    def clamp(self, x, y):
        return (max(self.VIDEO_X, min(self.VIDEO_X + self.VIDEO_W, x)),
                max(self.VIDEO_Y, min(self.VIDEO_Y + self.VIDEO_H, y)))

    def to_norm(self, x, y):
        """Window pixel -> backend 0..10000 coordinates of the effective HDMI content."""
        x, y = self.clamp(x, y)
        return (int(round(float((x - self.VIDEO_X) * 10000 / self.VIDEO_W))),
                int(round(float((y - self.VIDEO_Y) * 10000 / self.VIDEO_H))))

    def from_norm(self, nx, ny):
        return (self.VIDEO_X + int(round(float(nx * self.VIDEO_W / 10000))),
                self.VIDEO_Y + int(round(float(ny * self.VIDEO_H / 10000))))


LAYOUT = P1Layout()


def normalized_roi(start, end, layout=LAYOUT):
    """Drag rectangle in window pixels -> backend ROI, clipped to the effective video."""
    (x0, y0), (x1, y1) = layout.clamp(*start), layout.clamp(*end)
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    if right - left < 4 or bottom - top < 4:
        return None
    return layout.to_norm(left, top) + layout.to_norm(right, bottom)


class FrontP1Client(DriveClient):
    def allowed_action(self, action):
        if isinstance(action, tuple):
            if len(action) == 3 and action[0] == 'pick':
                return all(type(value) is int and 0 <= value <= 10000 for value in action[1:])
            return (len(action) == 5 and action[0] == 'bind' and
                    all(type(value) is int and 0 <= value <= 10000 for value in action[1:]) and
                    action[1] < action[3] and action[2] < action[4])
        return action in {item[1] for item in P1_BUTTONS if item[1] != 'bind'}

    def command_route(self, action):
        if isinstance(action, tuple):
            if action[0] == 'pick':
                return '/api/pick?' + urlencode({'x': action[1], 'y': action[2]})
            return '/api/bind?' + urlencode(dict(zip(('x0', 'y0', 'x1', 'y1'), action[1:])))
        return super().command_route(action)


def p1_presentation(snapshot, video_fresh, now):
    model = presentation(snapshot, video_fresh, now)
    state = snapshot.get('state') or {}
    drive = state.get('drive') or {}
    p1 = drive.get('p1') if isinstance(drive, dict) else None
    model['p1'] = {}
    model['line_held'] = [False, False]
    if not isinstance(p1, dict) or p1.get('version') not in (1, 2):
        model['p1_reason'] = '请启动 front_p1 板端服务'
        return model
    integer_fields = {'direction': range(4), 'signal': range(4), 'led': range(4), 'speed_kmh': range(121)}
    if any(type(p1.get(key)) is not int or p1[key] not in values for key, values in integer_fields.items()):
        model['p1_reason'] = '板端P1状态无效，等待刷新'
        return model
    p1 = dict(p1)
    elapsed = now - snapshot.get('received_at', 0)
    elapsed_ms = max(0, int(elapsed * 1000))
    running = model['live'] and state.get('state') == 'RUNNING'
    age = p1.get('signal_age_ms', -1)
    hold_ms = 1500 if p1.get('version') == 2 else 500
    live_ms = 1000 if p1.get('version') == 2 else 500
    valid = running and type(age) is int and age >= 0 and p1['signal'] in (1, 2, 3) and 0 <= age + elapsed_ms <= hold_ms
    ready = valid and not p1.get('signal_held', False) and age + elapsed_ms <= live_ms
    forced_live = running and elapsed <= .5 and p1.get('reason') == 'FORCED_MOVE'
    people_age = p1.get('people_age_ms', -1)
    people = p1.get('people')
    people_fresh = (running and 0 <= elapsed <= .5 and p1.get('people_valid') is True and
                    type(people_age) is int and people_age >= 0 and 0 <= people_age + elapsed_ms <= 750 and
                    type(people) is int and 0 <= people <= 10000)
    right_turn = p1['direction'] == 3
    right_live = right_turn and people_fresh and people == 0 and p1.get('reason') in ('RIGHT_READY', 'RIGHT_GO')
    if not valid:
        p1.update(signal=0, led=p1['led'] if right_live else 0, signal_held=False)
        if not forced_live and not right_live:
            p1['speed_kmh'] = 0
        if not model['live'] or state.get('state') != 'RUNNING':
            p1['reason'] = 'NOT_RUNNING'
        elif p1.get('bound') and not forced_live and not right_turn:
            p1['reason'] = 'WAIT_SIGNAL'
    elif not ready:
        p1.update(led=p1['led'] if right_live else 0, signal_held=True)
        if not forced_live and not right_live:
            p1['speed_kmh'] = 0
            if p1.get('reason') in ('GREEN_GO', 'GREEN_READY'):
                p1['reason'] = 'SIGNAL_HOLD'
    if right_turn and not right_live:
        p1.update(speed_kmh=0, led=0)
        p1['reason'] = 'WAIT_PEDESTRIAN' if people_fresh and people > 0 else 'WAIT_PERCEPTION'
    if right_turn:
        p1['people_valid'] = people_fresh
        if p1.get('people_basis')=='crosswalk_contact' and (not running or type(people_age) is not int or
                people_age<0 or people_age+elapsed_ms>750):p1['people_state']='STALE'
    if not model['live']:
        p1.update(alarm=False, bound=False)
    model['p1'] = p1
    model['p1_reason'] = REASONS.get(p1.get('reason'), '等待板端反馈')
    if p1.get('version') == 2:
        lines, ages, held = drive.get('stable_line'), drive.get('line_age_ms'), drive.get('line_held')
        model['line'] = [0, 0]
        if (isinstance(lines, list) and len(lines) == 2 and isinstance(ages, list) and len(ages) == 2 and
                isinstance(held, list) and len(held) == 2):
            for side in range(2):
                if (running and type(lines[side]) is int and lines[side] in (1, 2) and
                        type(ages[side]) is int and 0 <= ages[side] + elapsed_ms <= 1500):
                    model['line'][side] = lines[side]
                    model['line_held'][side] = bool(held[side]) or ages[side] + elapsed_ms > 750
        if model['request'] and running:
            side = model['request'] - 1
            line = model['line'][side]
            model['alarm'] = line == 1
            if line == 1:
                model['title'] = '禁止跨越实线变道'
                model['detail'] = ('实线判断短时保持，继续观察；请保持当前车道。' if model['line_held'][side] else
                                   '所选一侧已确认实线，请保持当前车道。')
            elif line == 2:
                model['title'] = '该侧为虚线，请人工确认侧后方'
                model['detail'] = ('虚线判断短时保持，等待更新；仍需人工观察侧后方。' if model['line_held'][side] else
                                   '线型条件满足，侧后方安全仍需人工确认。')
            else:
                model['title'], model['detail'] = '暂缓变道：边界待确认', '保持时间已到或尚无可靠结果，继续观察。'
        if not model['request'] and running:
            model['title'], model['detail'] = '可请求左 / 右变道', '按 A / D 获取对应侧反馈；短时漏检保持最多1.5秒。'
        return model
    if model['front_only'] and drive.get('feedback') == 'DASHED_REVIEW':
        model['title'], model['detail'] = '该侧为虚线，线型条件满足', '侧后方安全未验证，请人工观察后决定。'
    if not model['request']:
        model['title'], model['detail'] = '等待变道请求', 'A / D 请求左 / 右变道；实线阻止，未知暂缓。'
    return model


PEDESTRIAN_STATES={
    'WAITING':('等待检测','等待新鲜的行人检测结果','pending'),
    'STALE':('等待更新','行人结果已过期，暂缓演示','pending'),
    'UNALIGNED':('关联待确认','行人与斑马线未对齐，暂缓演示','pending'),
    'CONFIRMING':('正在确认','连续观测确认中，暂缓演示','pending'),
    'YIELD':('礼让行人','确认行人接触斑马线，暂停右转演示','hold'),
    'YIELD_HOLD':('短时保持','刚才确认的行人关联暂失，短时保持礼让','hold'),
    'OBSERVE':('持续观察','未确认横道冲突，低速观察演示','observe'),
    'CLEAR':('可继续演示','当前未见确认的横道行人冲突','ok'),
}


def p1_view(model, snapshot, video_fresh, selecting=False, notice=''):
    """Flatten everything the panel draws into hashable values; also the panel cache key."""
    p1 = model.get('p1') or {}
    live = model['live']
    board_state = (snapshot.get('state') or {}).get('state')
    running = live and board_state == 'RUNNING'
    if not live:
        badge = ('stale', '视频断流' if not video_fresh else '控制未连接')
        stale_text = ('视频断流 · 暂停依据旧画面判断' if not video_fresh else
                      '控制连接中断 · 等待板端状态恢复')
    elif board_state == 'RUNNING':
        badge, stale_text = ('ok', '检测运行中'), ''
    elif board_state == 'HDMI_STALE':
        badge, stale_text = ('stale', 'HDMI 输入中断'), ''
    elif board_state in ('STOPPED', 'IDLE'):
        badge, stale_text = ('pending', model['status']), ''
    else:
        badge, stale_text = ('hold', model['status']), ''
    signal, held = p1.get('signal', 0), bool(p1.get('signal_held')) and p1.get('signal', 0) != 0
    reason = p1.get('reason')
    conflict = running and reason == 'COLOR_CONFLICT'
    if not live:
        light_tag = ('stale', '断流')
    elif not running:
        light_tag = ('pending', '未运行')
    elif conflict:
        light_tag = ('action', '请选灯')
    elif not signal:
        light_tag = ('pending', '待确认')
    elif held:
        light_tag = ('hold', '保持')
    else:
        light_tag = ('ok', '已确认')
    if conflict:
        light_note = '多盏灯颜色不同：请点击本方向对应的 LIGHT'
    elif held:
        light_note = '短时保持显示，不作为继续通行的依据'
    elif running and not signal:
        light_note = '等待灯色证据，保持模拟停车'
    elif running:
        light_note = '颜色来自板端实时识别结果'
    else:
        light_note = '开始检测后自动识别前方灯色'
    if p1.get('direction') == 3:
        light_note = '右转无需选灯；有行人时礼让，无人可右转'
    bound = bool(p1.get('bound'))
    light_hint = ('蓝框跟随所选灯 · C 恢复自动识别' if bound else
                  '点击画面中的 LIGHT 可指定灯 · T 显示提示')
    if p1.get('direction') == 3:
        light_hint = '右转无需选灯 · 按实时行人结果反馈'
    speed = p1.get('speed_kmh')
    alarm_p1 = bool(p1.get('alarm'))
    if not live:
        speed_tag = ('stale', '断流')
    elif alarm_p1:
        speed_tag = ('alarm', '强行报警')
    elif reason == 'FORCED_MOVE' and running:
        speed_tag = ('hold', '强行演示')
    elif reason == 'WAIT_PEDESTRIAN' and running:
        speed_tag = ('observe', '礼让行人')
    elif type(speed) is int and speed > 0:
        speed_tag = ('ok', '模拟通行')
    else:
        speed_tag = ('pending', '模拟停车')
    lines = tuple(model['line'])
    line_held = tuple(bool(x) for x in model['line_held'])
    request = model['request'] if running else 0
    lane_alarm = bool(model['alarm']) and running
    if not live:
        lane_tag = ('stale', '断流')
    elif not running:
        lane_tag = ('pending', '未运行')
    elif not request:
        lane_tag = ('pending', '无请求')
    elif lane_alarm:
        lane_tag = ('alarm', '报警')
    elif lines[request - 1] == 2:
        lane_tag = ('observe', '需人工观察')
    else:
        lane_tag = ('pending', '待确认')
    if not live:
        banner = ('stale', stale_text)
    elif alarm_p1:
        banner = ('alarm', '强行行驶报警：灯色禁止或未确认，按 B 停车清除')
    elif lane_alarm:
        banner = ('alarm', model.get('title', '') + '：请保持当前车道，K 取消请求')
    elif not p1:
        banner = ('pending', model.get('p1_reason', '等待板端反馈'))
    else:
        banner = (REASON_KINDS.get(reason, 'pending'), model.get('p1_reason', '等待板端反馈'))
    direction = p1.get('direction', 0)
    spatial=p1.get('people_basis')=='crosswalk_contact'
    people_state=p1.get('people_state','WAITING')
    people_age=p1.get('people_age_ms',-1)
    if not running or type(people_age) is not int or people_age<0 or people_age>750:people_state='STALE'
    people_feedback=PEDESTRIAN_STATES.get(people_state,PEDESTRIAN_STATES['WAITING'])
    raw_people=(snapshot.get('state') or {}).get('pedestrian') or {}
    people_total=raw_people.get('total') if isinstance(raw_people,dict) else None
    if type(people_total) is not int or not 0<=people_total<=128:people_total=None
    if spatial and direction==3 and running and not lane_alarm and not alarm_p1:
        banner=(people_feedback[2],people_feedback[1])
    buttons = []
    for _, action in P1_BUTTONS:
        state = ''
        if action == 'start' and not running:
            state = 'primary'
        elif action == 'stop' and running:
            state = 'armed'
        elif action in ('turn_left', 'straight', 'turn_right') and direction == ('', 'turn_left', 'straight', 'turn_right').index(action):
            state = 'selected'
        elif action == 'bind' and (selecting or bound):
            state = 'selected'
        elif action == 'go':
            state = 'selected' if p1.get('armed') else 'primary'
        elif action in ('left', 'right') and request == ('', 'left', 'right').index(action):
            state = 'alarm' if lane_alarm else 'selected'
        buttons.append((action, state))
    return {
        'live': live, 'running': running, 'badge': badge, 'stale_text': stale_text,
        'signal': signal, 'signal_held': held, 'bound': bound, 'light_tag': light_tag, 'conflict': conflict,
        'light_note': light_note, 'light_hint': light_hint,
        'direction': direction, 'led': p1.get('led', 0), 'direction_known': bool(p1),
        'speed': speed if type(speed) is int else None, 'armed': bool(p1.get('armed')),
        'speed_tag': speed_tag, 'lines': lines, 'line_held': line_held, 'request': request,
        'lane_alarm': lane_alarm, 'lane_tag': lane_tag, 'lane_title': model.get('title', ''),
        'lane_detail': model.get('detail', ''), 'banner': banner, 'notice': notice,
        'steps': (direction != 0, bound, bool(p1.get('armed'))), 'buttons': tuple(buttons),
        'alarm': running and (lane_alarm or alarm_p1), 'selecting': bool(selecting),
        'people_spatial':spatial,'people_state':people_state,'people_feedback':people_feedback,
        'people_total':people_total,'people_relevant':p1.get('people',0),
    }


def _mix(color, base, amount):
    a, b = color.lstrip('#'), base.lstrip('#')
    return '#' + ''.join('%02x' % round(int(a[i:i+2], 16) * amount + int(b[i:i+2], 16) * (1 - amount))
                         for i in (0, 2, 4))


def _bgr(color):
    value = color.lstrip('#')
    return int(value[4:6], 16), int(value[2:4], 16), int(value[0:2], 16)


class FrontP1Dashboard(DriveDashboard):
    WIDTH, HEIGHT = LAYOUT.WIDTH, LAYOUT.HEIGHT
    WINDOW_SIZE = (1440, 810)
    FLASH_SECONDS = .25

    def __init__(self, client, font_path=None, front_only=True):
        super().__init__(client, font_path, front_only=True)
        from PIL import ImageFont
        self.layout = LAYOUT
        labels = dict((action, label) for label, action in P1_BUTTONS)
        self.buttons = [(LAYOUT.BUTTONS[action], labels[action], action) for _, action in P1_BUTTONS]
        regular = Path(self.font_path)
        bold = regular.with_name('PangoPreviewSans-Bold.ttf')
        if regular.name != 'PangoPreviewSans.ttf' or not bold.is_file():
            bold = regular
        # Fonts are loaded once; per-frame work never touches Pillow except on panel changes.
        self.regular = {size: ImageFont.truetype(str(regular), size) for size in (13, 14, 15, 16, 18, 20)}
        self.bold = {size: ImageFont.truetype(str(bold), size) for size in (13, 14, 15, 16, 18, 20, 22, 24, 40, 56)}
        self.selecting = False
        self.drag_start = self.drag_end = None
        self.local_notice = ''
        self.flash = None
        self.patches = {}
        self.truncated = []
        self.last_view = None

    # ---- interaction -------------------------------------------------------------------------
    def action_at(self, x, y):
        for (left, top, right, bottom), _, action in self.buttons:
            if left <= x < right and top <= y < bottom:
                return action
        return None

    def handle_action(self, action):
        self.flash = (action, time.monotonic())
        if action == 'bind':
            if not (self.last_model or {}).get('live'):
                self.local_notice = '请先等待视频和板端状态恢复。'
                return
            self.selecting = True
            self.drag_start = self.drag_end = None
            self.local_notice = '直接点击对应的黄色 LIGHT 框；也可以拖框选择。'
        else:
            self.selecting = False
            self.drag_start = self.drag_end = None
            self.local_notice = ''
            self.client.submit(action)

    def handle_key(self, key):
        if ord('A') <= key <= ord('Z'):
            key += 32
        action = P1_KEYS.get(key)
        if action:
            self.handle_action(action)

    def click(self, event, x, y, flags=None, parameter=None):
        if event == 1:
            action = self.action_at(x, y)
            if action:
                self.handle_action(action)
                return
            if (self.last_model or {}).get('live') and self.layout.inside_video(x, y):
                self.drag_start = self.drag_end = (x, y)
        elif event == 0 and self.drag_start is not None:
            self.drag_end = self.layout.clamp(x, y)
        elif event == 4 and self.drag_start is not None:
            if max(abs(x-self.drag_start[0]), abs(y-self.drag_start[1])) < 4:
                self.client.submit(('pick',) + self.layout.to_norm(*self.drag_start))
                self.local_notice = '已请求选中此灯；选中后会跟随检测框移动。'
                self.selecting = False
                self.drag_start = self.drag_end = None
                return
            roi = normalized_roi(self.drag_start, (x, y), self.layout)
            if roi:
                self.client.submit(('bind',) + roi)
                self.local_notice = '选灯已提交；选中后跟随目标，C 恢复自动识别。'
                self.selecting = False
            else:
                self.local_notice = '选框太小，请重新拖框。'
            self.drag_start = self.drag_end = None

    # ---- drawing helpers ---------------------------------------------------------------------
    @staticmethod
    def _width(font, value):
        if hasattr(font, 'getbbox'):
            return font.getbbox(value)[2] if value else 0
        return font.getsize(value)[0]

    @staticmethod
    def _middle(font):
        # Offset from the draw origin to the visual middle of CJK glyphs (Pillow 7 fallback).
        if hasattr(font, 'getbbox'):
            box = font.getbbox('国')
            return (box[1] + box[3]) / 2
        ascent, descent = font.getmetrics()
        return ascent * .62

    def _fit(self, value, font, limit):
        value = str(value)
        if self._width(font, value) <= limit:
            return value
        self.truncated.append(value)
        while value and self._width(font, value + '…') > limit:
            value = value[:-1]
        return value + '…'

    def _wrap(self, value, font, limit, max_lines):
        lines, line = [], ''
        for character in str(value):
            if self._width(font, line + character) > limit and line:
                lines.append(line)
                line = character.lstrip()
            else:
                line += character
        if line:
            lines.append(line)
        if len(lines) > max_lines:
            self.truncated.append(value)
            lines, last = lines[:max_lines], lines[max_lines - 1]
            while last and self._width(font, last + '…') > limit:
                last = last[:-1]
            lines[-1] = last + '…'
        return lines

    def _text(self, draw, x, cy, value, font, fill=TEXT, align='left', limit=None):
        value = self._fit(value, font, limit) if limit else str(value)
        width = self._width(font, value)
        if align == 'center':
            x -= width / 2
        elif align == 'right':
            x -= width
        draw.text((x, cy - self._middle(font)), value, font=font, fill=fill)
        return width

    @staticmethod
    def _rrect(draw, box, radius, fill=None, outline=None, width=1):
        if hasattr(draw, 'rounded_rectangle'):
            draw.rounded_rectangle(box, radius, fill=fill, outline=outline, width=width)
        else:
            draw.rectangle(box, fill=fill, outline=outline, width=width)

    def _pill(self, draw, right, cy, kind, label, font=None, align='right'):
        font = font or self.bold[14]
        color = KINDS[kind][1]
        width = self._width(font, label) + 26
        left = right - width if align == 'right' else right
        self._rrect(draw, (left, cy - 12, left + width, cy + 12), 12, fill=_mix(color, CARD, .18), outline=color)
        draw.ellipse((left + 8, cy - 4, left + 16, cy + 4), fill=color)
        self._text(draw, left + 19, cy, label, font, color)
        return left, left + width

    def _card(self, draw, name, title, tag):
        left, top, right, bottom = self.layout.CARDS[name]
        self._rrect(draw, (left, top, right, bottom), 10, fill=CARD, outline=BORDER)
        self._text(draw, left + 16, top + 20, title, self.bold[16], MUTED)
        if tag:
            self._pill(draw, right - 14, top + 20, *tag)
        return left, top, right, bottom

    @staticmethod
    def _arrow(draw, cx, cy, size, direction, fill):
        s = size
        shapes = {
            'up': [(0, -s), (s * .8, -s * .1), (s * .3, -s * .1), (s * .3, s), (-s * .3, s),
                   (-s * .3, -s * .1), (-s * .8, -s * .1)],
        }
        points = shapes['up']
        if direction == 'left':
            points = [(y, -x) for x, y in points]
        elif direction == 'right':
            points = [(-y, x) for x, y in points]
        draw.polygon([(cx + x, cy + y) for x, y in points], fill=fill)

    # ---- panel -------------------------------------------------------------------------------
    def render_panel(self, model, view=None):
        import numpy as np
        from PIL import Image, ImageDraw
        view = view or p1_view(model, {}, model['live'], self.selecting, self.local_notice)
        self.truncated = []
        L = self.layout
        image = Image.new('RGB', (L.WIDTH, L.HEIGHT), BG)
        draw = ImageDraw.Draw(image)
        self._draw_header(draw, view)
        vx0, vy0, vx1, vy1 = L.video_rect()
        draw.rectangle((vx0 - 1, vy0 - 1, vx1, vy1), outline=BORDER)
        self._draw_banner(draw, view)
        self._draw_buttons(draw, view)
        self._draw_light(draw, view)
        self._draw_direction(draw, view)
        self._draw_speed(draw, view)
        self._draw_lane(draw, view)
        self._draw_legend(draw)
        return np.array(image)[:, :, ::-1].copy()

    def _draw_header(self, draw, view):
        draw.rectangle((0, 0, self.layout.WIDTH, 56), fill=HEADER)
        draw.line((0, 56, self.layout.WIDTH, 56), fill=BORDER)
        width = self._text(draw, 24, 28, 'HDMI 单路 · 智能辅助驾驶 P1', self.bold[24])
        _, end = self._pill(draw, 24 + width + 22, 28, view['badge'][0], view['badge'][1], self.bold[16], 'left')
        self._text(draw, end + 20, 28, '前视 HDMI · 识别与判定由 RK3568 执行 · 车辆运动为模拟', self.regular[15], MUTED)
        self._text(draw, self.layout.SIDE_X1, 28, 'S 截图 · Q / Esc 退出', self.regular[15], MUTED, 'right')

    def _draw_banner(self, draw, view):
        left, top, right, bottom = self.layout.BANNER
        kind, message = view['banner']
        label, color = KINDS[kind]
        if (kind=='hold' and view.get('people_spatial') and view.get('direction')==3
                and view.get('people_state')=='YIELD'):
            label='礼让行人'
        cy = (top + bottom) // 2
        self._rrect(draw, (left, top, right, bottom), 8, fill=_mix(color, BG, .16), outline=_mix(color, BG, .55))
        draw.rectangle((left, top + 6, left + 5, bottom - 6), fill=color)
        draw.ellipse((left + 18, cy - 11, left + 40, cy + 11), fill=color)
        self._text(draw, left + 29, cy, '!' if kind in ('alarm', 'hold', 'action') else '?' if kind in ('pending', 'stale') else 'i',
                   self.bold[16], BG, 'center')
        label_width = self._text(draw, left + 52, cy, label, self.bold[16], color)
        notice_limit = 400
        notice = view['notice']
        notice_width = self._width(self.regular[15], self._fit(notice, self.regular[15], notice_limit)) if notice else 0
        start = left + 66 + label_width
        self._text(draw, start, cy, message, self.bold[20], TEXT, limit=right - 24 - notice_width - start - 20)
        if notice:
            self._text(draw, right - 16, cy, notice, self.regular[15], '#c7d5ea', 'right', notice_limit)

    def _draw_buttons(self, draw, view):
        L = self.layout
        for x, label in L.GROUPS:
            color = KINDS['alarm'][1] if label == '报警演示' else MUTED
            width = self._text(draw, x, L.GROUP_LABEL_Y, label, self.bold[15], color)
            if label == '路口通行':
                cursor = x + width + 16
                for index, (name, done) in enumerate(zip(('方向', '选灯', '起步'), view['steps'])):
                    fill = KINDS['ok'][1] if done else DIM
                    draw.ellipse((cursor, L.GROUP_LABEL_Y - 9, cursor + 18, L.GROUP_LABEL_Y + 9), fill=fill)
                    self._text(draw, cursor + 9, L.GROUP_LABEL_Y, str(index + 1), self.bold[14], BG, 'center')
                    cursor += 24 + self._text(draw, cursor + 24, L.GROUP_LABEL_Y, name, self.regular[14],
                                              TEXT if done else MUTED)
                    if index < 2:
                        cursor += 8 + self._text(draw, cursor + 8, L.GROUP_LABEL_Y, '→', self.regular[14], DIM) + 8
                self._text(draw, 700, L.GROUP_LABEL_Y, '无需每次框选', self.regular[13], DIM, 'right')
            elif label == '报警演示':
                self._text(draw, 1168, L.GROUP_LABEL_Y, '与常规操作分开', self.regular[13], DIM, 'right')
        for x in L.SEPARATORS:
            draw.line((x, L.GROUP_LABEL_Y - 10, x, 890), fill=BORDER)
        states = dict(view['buttons'])
        for (left, top, right, bottom), label, action in self.buttons:
            state = states.get(action, '')
            fill, outline, ink = '#1a2538', '#2f405f', TEXT
            if state == 'primary':
                fill, outline = ('#14532d', '#22c55e') if action == 'start' else ('#1e3a6a', '#3b82f6')
            elif state == 'selected':
                fill, outline = '#1d4ed8', '#93c5fd'
            elif state == 'armed':
                fill, outline = '#2b3446', '#94a3b8'
            elif state == 'alarm':
                fill, outline = '#7f1d1d', '#f87171'
            if action == 'force_go':
                fill, outline = '#3b1219', '#f87171'
            self._rrect(draw, (left, top, right, bottom), 8, fill=fill, outline=outline, width=2 if state or action == 'force_go' else 1)
            hint = KEY_HINTS[action]
            hint_font = self.bold[13] if hint == '空格' else self.bold[14]
            hint_width = self._width(hint_font, hint) + 14
            if action == 'force_go':
                cy = top + 26
                self._text(draw, left + 14, cy, label, self.bold[18], '#fecaca', limit=right - left - hint_width - 32)
                self._rrect(draw, (right - 12 - hint_width, cy - 12, right - 12, cy + 12), 5, fill='#7f1d1d')
                self._text(draw, right - 12 - hint_width / 2, cy, hint, hint_font, '#fee2e2', 'center')
                self._text(draw, left + 14, top + 52, '红 / 黄 / 未知灯色时报警', self.regular[13], '#fca5a5', limit=right - left - 24)
                self._text(draw, left + 14, top + 71, '最长 2 秒 · B 停车清除', self.regular[13], '#fca5a5', limit=right - left - 24)
                continue
            cy = (top + bottom) // 2
            self._text(draw, left + 12, cy, label, self.bold[16], ink, limit=right - left - hint_width - 28)
            self._rrect(draw, (right - 10 - hint_width, cy - 11, right - 10, cy + 11), 5,
                        fill=_mix('#ffffff', fill, .14))
            self._text(draw, right - 10 - hint_width / 2, cy, hint, hint_font, '#dbe6f5', 'center')

    def _draw_light(self, draw, view):
        if view.get('people_spatial') and view['direction']==3:
            title,detail,kind=view['people_feedback']
            left,top,right,bottom=self._card(draw,'light','行人通行观察',(kind,title))
            self._text(draw,left+18,top+70,title,self.bold[24],KINDS[kind][1])
            total=view['people_total'] if view['people_state']!='STALE' else None
            self._text(draw,left+18,top+105,'检测行人 %s  ·  确认相关 %d'%('--' if total is None else total,view['people_relevant']),
                       self.regular[16],TEXT)
            self._text(draw,left+18,top+140,detail,self.regular[15],MUTED,limit=right-left-36)
            self._text(draw,left+18,top+164,'灰色仅观察 · 紫色为确认关联',self.regular[14],DIM)
            return
        left, top, right, bottom = self._card(draw, 'light', '交通灯', view['light_tag'])
        housing = (left + 16, top + 44, left + 196, top + 112)
        self._rrect(draw, housing, 14, fill='#05080f', outline='#334155', width=2)
        for index in (1, 2, 3):
            cx, cy = left + 16 + 30 + (index - 1) * 60, top + 78
            lit = view['signal'] == index
            color = SIGNAL_COLORS[index] if lit else _mix(SIGNAL_COLORS[index], '#05080f', .16)
            if lit and view['signal_held']:
                color = _mix(SIGNAL_COLORS[index], '#05080f', .6)
            draw.ellipse((cx - 23, cy - 23, cx + 23, cy + 23), fill=color)
            if lit and view['signal_held']:
                draw.ellipse((cx - 27, cy - 27, cx + 27, cy + 27), outline=KINDS['hold'][1], width=2)
        name = SIGNAL_NAMES[view['signal']] + ('（保持）' if view['signal_held'] else '')
        color = SIGNAL_COLORS[view['signal']]
        if view['conflict'] and not view['signal']:
            name, color = '颜色冲突', KINDS['action'][1]
        self._text(draw, left + 212, top + 66, name, self.bold[24] if view['signal_held'] else self.bold[40],
                   color, limit=right - left - 224)
        source = ('action', '已选定灯') if view['bound'] else ('pending', '自动识别')
        if view['direction'] == 3:
            source = ('observe', '行人检测')
        self._pill(draw, left + 212, top + 102, source[0], source[1], self.bold[14], 'left')
        self._text(draw, left + 16, top + 138, view['light_note'], self.regular[16],
                   KINDS['hold'][1] if view['signal_held'] else '#c7d5ea', limit=right - left - 32)
        self._text(draw, left + 16, top + 164, view['light_hint'], self.regular[14], MUTED, limit=right - left - 32)

    def _draw_direction(self, draw, view):
        tag = (('stale', '断流') if not view['live'] else
               ('action', '未选择') if not view['direction'] else ('ok', '已选择'))
        left, top, right, bottom = self._card(draw, 'direction', '路口方向 · 虚拟 LED', tag)
        width = (right - left - 32 - 20) // 3
        for index, (name, arrow) in enumerate((('左转', 'left'), ('直行', 'up'), ('右转', 'right')), 1):
            x0 = left + 16 + (index - 1) * (width + 10)
            box = (x0, top + 40, x0 + width, bottom - 12)
            selected, lit = view['direction'] == index, view['led'] == index
            fill = '#13284d' if selected else '#0f1727'
            self._rrect(draw, box, 8, fill=fill, outline='#60a5fa' if selected else '#2a3a57', width=3 if selected else 1)
            color = KINDS['ok'][1] if lit else ('#bfdbfe' if selected else '#46556f')
            if lit:
                draw.ellipse((x0 + width // 2 - 25, top + 62, x0 + width // 2 + 25, top + 112), fill=_mix(KINDS['ok'][1], fill, .25))
            self._arrow(draw, x0 + width // 2, top + 87, 18, arrow, color)
            self._text(draw, x0 + width // 2, bottom - 24, name + ' ' + KEY_HINTS[('', 'turn_left', 'straight', 'turn_right')[index]],
                       self.bold[15], TEXT if selected else MUTED, 'center')
            led = 'LED%d' % (index - 1)
            draw.ellipse((x0 + 8, top + 48, x0 + 16, top + 56), fill=KINDS['ok'][1] if lit else '#334155')
            self._text(draw, x0 + 20, top + 52, led + (' 亮' if lit else ''), self.regular[13], KINDS['ok'][1] if lit else DIM)

    def _draw_speed(self, draw, view):
        left, top, right, bottom = self._card(draw, 'speed', '模拟车速', view['speed_tag'])
        value = '—' if view['speed'] is None else str(view['speed'])
        color = KINDS['alarm'][1] if view['speed_tag'][0] == 'alarm' else TEXT
        width = self._text(draw, left + 18, top + 64, value, self.bold[56], color)
        self._text(draw, left + 26 + width, top + 76, 'km/h', self.regular[18], MUTED)
        self._text(draw, left + 170, top + 54, '起步请求：' + ('已提交' if view['armed'] else '未提交'), self.bold[16],
                   KINDS['action'][1] if view['armed'] else MUTED)
        self._text(draw, left + 170, top + 80, '模拟值，不改变视频运动', self.regular[14], DIM)

    def _draw_lane(self, draw, view):
        left, top, right, bottom = self._card(draw, 'lane', '车道边界 · 变道请求', view['lane_tag'])
        road = (left + 16, top + 38, right - 16, top + 154)
        self._rrect(draw, road, 8, fill='#0c121e', outline='#1f2a40')
        xs = (left + 78, right - 78)
        y0, y1 = road[1] + 8, road[3] - 8
        request, lines, held = view['request'], view['lines'], view['line_held']
        for side in range(2):
            x, line = xs[side], lines[side]
            color = '#e8eef8'
            if held[side]:
                color = KINDS['hold'][1]
            if view['lane_alarm'] and request == side + 1:
                color = KINDS['alarm'][1]
            if line == 1:
                draw.line((x, y0, x, y1), fill=color, width=7)
            elif line == 2:
                for y in range(y0, y1, 26):
                    draw.line((x, y, x, min(y1, y + 15)), fill=color, width=7)
            else:
                for y in range(y0, y1, 12):
                    draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill='#55657f')
                draw.ellipse((x - 13, (y0 + y1) // 2 - 13, x + 13, (y0 + y1) // 2 + 13), fill='#1e293b', outline='#64748b')
                self._text(draw, x, (y0 + y1) // 2, '?', self.bold[16], '#cbd5e1', 'center')
        cx, cy = (left + right) // 2, (y0 + y1) // 2
        self._rrect(draw, (cx - 24, cy - 40, cx + 24, cy + 40), 12, fill='#334a6e', outline='#7da2d6', width=2)
        draw.rectangle((cx - 16, cy - 28, cx + 16, cy - 14), fill='#8fb3e6')
        self._text(draw, cx, cy + 16, '本车', self.bold[14], TEXT, 'center')
        if request:
            kind = view['lane_tag'][0]
            color = KINDS[kind][1]
            sign = -1 if request == 1 else 1
            start, end = cx + sign * 32, xs[request - 1] - sign * 14
            draw.line((start, cy - 10, end, cy - 10), fill=color, width=5)
            tip = end + sign * 2
            draw.polygon([(tip, cy - 10), (tip - sign * 14, cy - 20), (tip - sign * 14, cy)], fill=color)
            if view['lane_alarm']:
                mid = (start + end) // 2
                draw.line((mid - 12, cy - 22, mid + 12, cy + 2), fill=color, width=4)
                draw.line((mid - 12, cy + 2, mid + 12, cy - 22), fill=color, width=4)
        label_y = road[3] + 20
        for side, align, x in ((0, 'left', left + 16), (1, 'right', right - 16)):
            name = ('左侧 ', '右侧 ')[side] + LINE_NAMES[lines[side]]
            color = TEXT if lines[side] else MUTED
            width = self._text(draw, x, label_y, name, self.bold[18], color, align)
            if held[side]:
                if align == 'left':
                    self._pill(draw, x + width + 8, label_y, 'hold', '保持', self.bold[14], 'left')
                else:
                    self._pill(draw, x - width - 8, label_y, 'hold', '保持', self.bold[14], 'right')
        kind = view['lane_tag'][0]
        title_color = {'alarm': KINDS['alarm'][1], 'observe': KINDS['observe'][1], 'stale': KINDS['stale'][1]}.get(kind, '#f4d38c' if view['running'] else MUTED)
        self._text(draw, left + 16, label_y + 34, view['lane_title'], self.bold[20], title_color, limit=right - left - 32)
        for index, line in enumerate(self._wrap(view['lane_detail'], self.regular[15], right - left - 32, 2)):
            self._text(draw, left + 16, label_y + 62 + index * 22, line, self.regular[15], '#b6c4d8')

    def _draw_legend(self, draw):
        left, top, right, bottom = self.layout.CARDS['legend']
        self._rrect(draw, (left, top, right, bottom), 10, fill=CARD, outline=BORDER)
        self._text(draw, left + 16, top + 18, '状态图例', self.bold[14], MUTED)
        rows = (('ok', 'observe', 'alarm', 'hold'), ('pending', 'action', 'stale'))
        for row, kinds in enumerate(rows):
            x = left + 16
            for kind in kinds:
                label, color = KINDS[kind]
                y = top + 44 + row * 24
                draw.rectangle((x, y - 6, x + 12, y + 6), fill=color)
                x += 18 + self._text(draw, x + 18, y, label, self.regular[14], TEXT) + 16

    # ---- per-frame overlays ------------------------------------------------------------------
    def _patch(self, text, size, fg, bg):
        """Small cached Chinese label rendered once and pasted with numpy per frame."""
        key = (text, size, fg, bg)
        if key not in self.patches:
            import numpy as np
            from PIL import Image, ImageDraw
            font = self.bold[size]
            width = self._width(font, text) + 20
            height = size + 16
            image = Image.new('RGB', (width, height), bg)
            self._text(ImageDraw.Draw(image), 10, height / 2, text, font, fg)
            self.patches[key] = np.array(image)[:, :, ::-1].copy()
        return self.patches[key]

    def _paste(self, canvas, patch, x, y):
        L = self.layout
        x = int(max(L.VIDEO_X, min(L.VIDEO_X + L.VIDEO_W - patch.shape[1], x)))
        y = int(max(L.VIDEO_Y, min(L.VIDEO_Y + L.VIDEO_H - patch.shape[0], y)))
        canvas[y:y + patch.shape[0], x:x + patch.shape[1]] = patch

    def compose(self, video, video_fresh, snapshot=None, now=None, stats=None):
        import cv2
        if video.shape[:2] != (480, 1280):
            raise ValueError('P1需要front_p1服务输出的1280×480前视画面')
        L = self.layout
        now = time.monotonic() if now is None else now
        snapshot = self.client.snapshot() if snapshot is None else snapshot
        model = p1_presentation(snapshot, video_fresh, now)
        p1 = model['p1']
        view = p1_view(model, snapshot, video_fresh, self.selecting, self.local_notice or model.get('notice', ''))
        # The view holds every value the panel draws, so any visible change redraws immediately
        # and an unchanged state costs nothing beyond one copy.
        key = repr(sorted(view.items()))
        if key != self.cache_key:
            self.cached, self.cache_key = self.render_panel(model, view), key
        self.last_model, self.last_view = model, view
        canvas = self.cached.copy()
        x0, y0, x1, y1 = L.video_rect()
        source = video[:L.SOURCE_H, L.SOURCE_X:L.SOURCE_X + L.SOURCE_W]
        canvas[y0:y1, x0:x1] = cv2.resize(source, (L.VIDEO_W, L.VIDEO_H), interpolation=cv2.INTER_LINEAR)
        region = canvas[y0:y1, x0:x1]
        if not view['live']:
            region //= 2
            patch = self._patch(view['stale_text'], 22, '#ede9fe', '#3b2a63')
            self._paste(canvas, patch, (x0 + x1 - patch.shape[1]) // 2, (y0 + y1 - patch.shape[0]) // 2)
        roi = p1.get('roi')
        if (p1.get('bound') and isinstance(roi, list) and len(roi) == 4 and
                all(type(value) is int and 0 <= value <= 10000 for value in roi)):
            first, last = L.from_norm(roi[0], roi[1]), L.from_norm(roi[2], roi[3])
            cv2.rectangle(canvas, first, last, (255, 180, 0), 3)
            patch = self._patch('已选灯', 14, '#0a0f1a', '#00b4ff')
            self._paste(canvas, patch, first[0] - 1, first[1] - patch.shape[0] - 2)
        if self.selecting:
            cv2.rectangle(canvas, (x0 + 2, y0 + 2), (x1 - 3, y1 - 3), (255, 230, 0), 3)
            patch = self._patch('选灯模式：点击黄色 LIGHT 框，或按住拖框选择 · C 恢复自动识别', 18, '#0a0f1a', '#00e6ff')
            self._paste(canvas, patch, (x0 + x1 - patch.shape[1]) // 2, y0 + 12)
        if self.drag_start is not None:
            cv2.rectangle(canvas, self.drag_start, self.drag_end, (0, 255, 255), 2)
        if view['alarm']:
            thickness = 8 if int(now * 4) % 2 == 0 else 3
            cv2.rectangle(canvas, (x0, y0), (x1 - 1, y1 - 1), _bgr(KINDS['alarm'][1]), thickness)
        if self.flash and now - self.flash[1] <= self.FLASH_SECONDS and self.flash[0] in L.BUTTONS:
            left, top, right, bottom = L.BUTTONS[self.flash[0]]
            cv2.rectangle(canvas, (left, top), (right - 1, bottom - 1), (255, 255, 255), 3)
        if stats is not None:
            fps, _ = stats
            color = (153, 211, 52) if video_fresh else (250, 139, 167)
            cv2.putText(canvas, '%.1f FPS' % fps if video_fresh else 'NO VIDEO', (L.SIDE_X0 - 190, 34),
                        cv2.FONT_HERSHEY_SIMPLEX, .6, color, 1, cv2.LINE_AA)
        return canvas
