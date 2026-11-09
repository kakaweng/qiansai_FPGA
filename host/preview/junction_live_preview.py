"""Waymo junction demonstration: RK inference and rules, live three-source display."""
import argparse
import copy
import json
from pathlib import Path
import queue
import socket
import threading
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from preview_multivideo import Receiver
from preview_three_video import RGB565Converter, snapshot
from fpga_drive_feedback import FPGAFeedbackClient, UNKNOWN
from fpga_junction_keys import FPGAKeyClient

ROOT = Path(__file__).resolve().parent
PHASES = {'WAIT_PERCEPTION': '等待双路识别', 'WAIT_SIGNAL': '等待信号确认', 'STOP_RED': '红灯停车',
          'YIELD': '礼让横穿行人', 'GO': '绿灯模拟通行', 'CRUISE': '路口通过 · 持续观察',
          'PAUSED': '模拟车辆已停车', 'FORCED': '强制启动 · 违规报警'}
DIRECTIONS = {1: '左转', 2: '直行', 3: '右转'}
COLORS = {'text': (233, 229, 218), 'muted': (155, 144, 128), 'cyan': (214, 207, 81),
          'red': (98, 103, 247), 'green': (157, 218, 111), 'amber': (81, 188, 247)}


class JunctionClient(threading.Thread):
    def __init__(self, host='127.0.0.1', port=9021):
        super().__init__(daemon=True)
        self.host, self.port = host, port
        self.lock = threading.Lock()
        self.finished = threading.Event()
        self.commands = queue.Queue(8)
        self.state, self.received_at, self.notice = None, 0., ''
        self.action_notice, self.action_notice_until = '', 0.

    def submit(self, action):
        if action not in ('left', 'straight', 'right', 'go', 'force', 'brake', 'reset'):
            return False
        try:
            self.commands.put_nowait(action)
            return True
        except queue.Full:
            return False

    def request(self, action):
        with socket.create_connection((self.host, self.port), timeout=.4) as link:
            link.settimeout(.4)
            link.sendall((action + '\n').encode('ascii'))
            data = bytearray()
            deadline = time.monotonic() + .5
            while b'\n' not in data:
                if len(data) > 30000 or time.monotonic() > deadline:
                    raise ValueError('junction response deadline/size')
                block = link.recv(4096)
                if not block:
                    raise OSError('junction state incomplete')
                data.extend(block)
        state = json.loads(data)
        if state.get('profile') != 'waymo-119873-live-v1' or state.get('version') != 1:
            raise ValueError('junction service contract mismatch')
        return state

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state), self.received_at, self.notice

    def run(self):
        while not self.finished.is_set():
            try:
                action = self.commands.get_nowait()
            except queue.Empty:
                action = 'state'
            try:
                state = self.request(action)
                with self.lock:
                    self.state, self.received_at = state, time.monotonic()
                    if not state.get('accepted'):
                        self.action_notice = '感知未就绪，暂不能强制启动'
                        self.action_notice_until = self.received_at + 2
                    elif action != 'state':
                        self.action_notice = '板端已接收操作'
                        self.action_notice_until = self.received_at + 1
                    self.notice = self.action_notice if self.received_at < self.action_notice_until else ''
            except (OSError, ValueError) as error:
                with self.lock:
                    self.notice = '板端状态连接中：' + str(error)
            self.finished.wait(.1)

    def stop(self):
        self.finished.set()


class LiveTracks:
    def __init__(self):
        self.tracks = {}
        self.sequences = {}
        self.identity = None
        self.next_id = 1

    def update(self, state, now):
        identity = state.get('producer'), state.get('epoch')
        if identity != self.identity:
            self.tracks.clear(); self.sequences.clear(); self.identity = identity
        for source_index, source in enumerate(state.get('sources', [])):
            sequence = source.get('sequence', 0)
            if not source.get('valid'):
                self.tracks = {key: track for key, track in self.tracks.items() if track['source'] != source_index}
                continue
            if self.sequences.get(source_index) == sequence:
                continue
            self.sequences[source_index] = sequence
            used = set()
            stamp = source['timestamp_ms'] / 1000
            for entry in source.get('boxes', []):
                category, score, left, top, right, bottom, contact = entry
                if category == 9 or score < (.4 if category == 0 else .38):
                    continue
                box = np.array([left / source['width'], top / source['height'], right / source['width'], bottom / source['height']])
                if box[3] - box[1] < .035:
                    continue
                center = np.array([(box[0] + box[2]) / 2, box[3]])
                matches = []
                for key, track in self.tracks.items():
                    if key in used or track['source'] != source_index or track['category'] != category or now - track['seen'] > 1.3:
                        continue
                    distance = float(np.linalg.norm(center - track['position']))
                    if distance < .11:
                        matches.append((distance, key))
                if matches:
                    key = min(matches)[1]; track = self.tracks[key]
                    delta = stamp - track['stamp']
                    if .04 < delta < 1.8:
                        velocity = np.clip((center - track['position']) / delta, -.3, .3)
                        track['velocity'] = .55 * track['velocity'] + .45 * velocity
                    track['position'] = .65 * center + .35 * track['position']
                    track['box'] = .65 * box + .35 * track['box']
                    track['hits'] += 1
                else:
                    key = self.next_id; self.next_id += 1
                    track = {'id': key, 'source': source_index, 'category': category, 'position': center,
                             'velocity': np.zeros(2), 'box': box, 'hits': 1}
                    self.tracks[key] = track
                if contact:
                    track['contact_until'] = now + .9
                track.update(seen=now - max(0, source.get('age_ms', 0)) / 1000,
                             stamp=stamp, contact=bool(contact or now < track.get('contact_until', 0)), score=score)
                used.add(key)
        self.tracks = {key: track for key, track in self.tracks.items() if now - track['seen'] <= 1.3}

    def visible(self, now):
        result = []
        for track in self.tracks.values():
            if track['category'] == 0 and track['hits'] < 2:
                continue
            current = dict(track)
            elapsed = min(.6, max(0, now - track['seen']))
            current['position'] = np.clip(track['position'] + track['velocity'] * elapsed, 0, 1)
            current['held'] = now - track['seen'] > .65
            result.append(current)
        return result


class TextCache:
    def __init__(self):
        candidates = [ROOT / 'preview_fonts/NotoSansSC-Regular.ttf', ROOT / 'preview_fonts/NotoSansSC-VF.ttf',
                      Path('C:/Windows/Fonts/msyh.ttc'), Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')]
        candidates += list((ROOT / 'preview_fonts').glob('*.ttf'))
        self.path = next((path for path in candidates if path.is_file()), None)
        if self.path is None:
            raise FileNotFoundError('需要 preview_fonts 中的中文字体')
        self.fonts, self.cache = {}, {}

    def draw(self, canvas, text, position, size=20, color=COLORS['text']):
        key = text, size, color
        if key not in self.cache:
            if size not in self.fonts:
                self.fonts[size] = ImageFont.truetype(str(self.path), size)
            font = self.fonts[size]
            bounds = font.getbbox(text) if hasattr(font, 'getbbox') else (0, 0, *font.getsize(text))
            patch = Image.new('RGBA', (max(1, bounds[2] - bounds[0] + 4), size * 2), (0, 0, 0, 0))
            ImageDraw.Draw(patch).text((2 - bounds[0], -bounds[1]), text, font=font, fill=(*color[::-1], 255))
            self.cache[key] = np.asarray(patch)
            if len(self.cache) > 400:
                self.cache = {key: self.cache[key]}
        patch = self.cache[key]
        left, top = position
        height, width = min(patch.shape[0], canvas.shape[0] - top), min(patch.shape[1], canvas.shape[1] - left)
        if height <= 0 or width <= 0:
            return
        alpha = patch[:height, :width, 3:4].astype(np.float32) / 255
        canvas[top:top + height, left:left + width] = (
            canvas[top:top + height, left:left + width] * (1 - alpha) + patch[:height, :width, :3][:, :, ::-1] * alpha).astype(np.uint8)


class JunctionView:
    BUTTONS = [('左转 / KEY5', 'left'), ('直行 / KEY6', 'straight'), ('右转 / KEY7', 'right'),
               ('请求通行 G', 'go'), ('强制启动 F', 'force'), ('停车 / 清警 B', 'brake'), ('重置场景 R', 'reset')]

    def __init__(self):
        self.text = TextCache()
        self.tracker = LiveTracks()
        self.buttons = []
        self.base = np.full((900, 1600, 3), (25, 20, 16), np.uint8)
        self.text.draw(self.base, '多路路口感知与交互演示', (24, 18), 29)
        self.text.draw(self.base, '前视 / 左视感知 · 三路并行传输', (24, 57), 18, COLORS['muted'])
        for rectangle in ((20, 100, 800, 550), (20, 575, 440, 865), (460, 575, 800, 865), (820, 100, 1580, 865)):
            cv2.rectangle(self.base, rectangle[:2], rectangle[2:], (40, 32, 25), -1)

    @staticmethod
    def point(lateral, forward):
        depth = min(35, max(0, forward))
        scale = 24 - depth * .38
        return int(1200 + lateral * scale), int(778 - depth * 14)

    def project(self, track):
        horizontal, vertical = track['position']
        if track['source'] == 0:
            if track['contact']:
                return (horizontal - .5) * 15, 10 if vertical > .65 else 25
            return (horizontal - .5) * 15, min(29, max(3, (.97 - vertical) * 47))
        if track['contact']:
            return -6.5, 12 + horizontal * 10
        return -12 + horizontal * 10, min(27, max(5, (.98 - vertical) * 37 + 3))

    def road(self, canvas, state, tracks, now):
        cv2.fillPoly(canvas, [np.array([self.point(-5, 0), self.point(5, 0), self.point(5, 34), self.point(-5, 34)])], (61, 57, 52))
        cv2.fillPoly(canvas, [np.array([self.point(-15, 12), self.point(15, 12), self.point(15, 22), self.point(-15, 22)])], (61, 57, 52))
        for lateral in (-5, 5):
            cv2.line(canvas, self.point(lateral, 0), self.point(lateral, 34), (157, 153, 143), 2, cv2.LINE_AA)
        for distance in range(0, 34, 4):
            if not 10 < distance < 24:
                cv2.line(canvas, self.point(0, distance), self.point(0, distance + 2), (196, 186, 158), 2, cv2.LINE_AA)
        for lateral in np.arange(-4.5, 5, .9):
            for forward in (9, 24):
                polygon = [self.point(lateral, forward), self.point(lateral + .48, forward), self.point(lateral + .48, forward + 2.2), self.point(lateral, forward + 2.2)]
                cv2.fillPoly(canvas, [np.array(polygon)], (149, 187, 203))
        for forward in np.arange(12, 22, .9):
            cv2.fillPoly(canvas, [np.array([self.point(-7.6, forward), self.point(-5.4, forward),
                         self.point(-5.4, forward + .48), self.point(-7.6, forward + .48)])], (149, 187, 203))
        direction = state.get('direction', 2)
        route = []
        for step in range(31):
            distance = step * .8
            lateral = 0 if direction == 2 else (1 if direction == 3 else -1) * max(0, distance - 10) * .65
            route.append(self.point(lateral, distance))
        cv2.polylines(canvas, [np.array(route)], False, COLORS['cyan'], 3, cv2.LINE_AA)
        if state.get('speed', 0) > 0:
            progress = (state.get('distance', 0) % 24) / .8
            cv2.circle(canvas, route[min(30, round(progress))], 7, COLORS['text'], -1, cv2.LINE_AA)
        displayed = []
        for track in sorted(tracks, key=lambda item: (item['contact'], item['score']), reverse=True)[:22]:
            lateral, forward = self.project(track)
            if any(other[2] == track['category'] and other[3] != track['source'] and abs(other[0] - lateral) < 1.3 and abs(other[1] - forward) < 2.3 for other in displayed):
                continue
            displayed.append((lateral, forward, track['category'], track['source']))
            point = self.point(lateral, forward)
            color = COLORS['amber'] if track['contact'] else COLORS['cyan'] if track['source'] else COLORS['green']
            if track['held']:
                color = COLORS['muted']
            if track['category'] == 0:
                cv2.circle(canvas, (point[0], point[1] - 20), 5, color, -1, cv2.LINE_AA)
                cv2.line(canvas, (point[0], point[1] - 15), (point[0], point[1] - 5), color, 3, cv2.LINE_AA)
                for side in (-1, 1):
                    cv2.line(canvas, (point[0], point[1] - 5), (point[0] + side * 5, point[1] + 3), color, 2, cv2.LINE_AA)
                    cv2.line(canvas, (point[0], point[1] - 12), (point[0] + side * 7, point[1] - 7), color, 2, cv2.LINE_AA)
                if track['hits'] >= 3:
                    predicted = dict(track)
                    predicted['position'] = np.clip(track['position'] + track['velocity'] * 1.2, 0, 1)
                    target = self.point(*self.project(predicted))
                    if abs(target[0] - point[0]) + abs(target[1] - point[1]) > 6:
                        cv2.arrowedLine(canvas, point, target, color, 1, cv2.LINE_AA, tipLength=.25)
                cv2.ellipse(canvas, point, (12, 5), 0, 0, 360, color, 1, cv2.LINE_AA)
            else:
                cv2.rectangle(canvas, (point[0] - 10, point[1] - 22), (point[0] + 10, point[1] + 2), color, 1, cv2.LINE_AA)
                cv2.rectangle(canvas, (point[0] - 7, point[1] - 18), (point[0] + 7, point[1] - 11), color, -1)
        ego = self.point(0, 1)
        cv2.rectangle(canvas, (ego[0] - 15, ego[1] - 33), (ego[0] + 15, ego[1] + 6), COLORS['cyan'], -1)
        cv2.rectangle(canvas, (ego[0] - 10, ego[1] - 26), (ego[0] + 10, ego[1] - 15), (50, 53, 45), -1)
        self.text.draw(canvas, '动态 2.5D · 当前感知 + 短时预测', (840, 116), 22)
        self.text.draw(canvas, '本车固定视角  |  路口模板 / 位置含估计', (840, 150), 16, COLORS['muted'])
        self.text.draw(canvas, '绿：前视目标   青：左视目标   橙：横道附近行人', (840, 823), 16, COLORS['muted'])

    def render(self, mosaic, state, fresh, now, rx_fps=0, notice=''):
        canvas = self.base.copy()
        if not fresh:
            state = dict(state or {}, phase='WAIT_PERCEPTION', led=5, speed=0, signal=0, sources=[])
        state = state or {}
        self.tracker.update(state, now)
        tracks = self.tracker.visible(now) if fresh else []
        self.road(canvas, state, tracks, now)
        tiles = [(mosaic[60:420, :640], (30, 145, 760, 390), 0),
                 (mosaic[240:480, 800:1120], (30, 620, 400, 235), 1),
                 (mosaic[:240, 800:1120], (470, 620, 320, 235), None)]
        for image, (left, top, width, height), source in tiles:
            ratio = min(width / image.shape[1], height / image.shape[0])
            fitted_width, fitted_height = round(image.shape[1] * ratio), round(image.shape[0] * ratio)
            left += (width - fitted_width) // 2
            top += (height - fitted_height) // 2
            width, height = fitted_width, fitted_height
            canvas[top:top + height, left:left + width] = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
            if source is not None:
                for track in tracks:
                    if track['source'] != source:
                        continue
                    box = track['box']
                    first = int(left + box[0] * width), int(top + box[1] * height)
                    last = int(left + box[2] * width), int(top + box[3] * height)
                    color = COLORS['amber'] if track['contact'] else COLORS['green']
                    cv2.rectangle(canvas, first, last, color, 2, cv2.LINE_AA)
                    label = ('P' if track['category'] == 0 else 'V') + str(track['id'])
                    cv2.putText(canvas, label, (first[0], max(top + 12, first[1] - 4)), cv2.FONT_HERSHEY_SIMPLEX, .45, color, 1, cv2.LINE_AA)
        self.text.draw(canvas, 'HDMI · 正前方分析', (34, 112), 21)
        self.text.draw(canvas, 'ETHERNET · 左视分析', (34, 587), 19)
        self.text.draw(canvas, 'OV5640 · 仅视频', (474, 587), 19, COLORS['muted'])
        if fresh and len(state.get('sources', [])) == 2:
            self.text.draw(canvas, '横道行人' if state['sources'][1].get('risk') else '持续观察',
                           (300, 589), 16, COLORS['amber'] if state['sources'][1].get('risk') else COLORS['muted'])
        phase = state.get('phase', 'WAIT_PERCEPTION')
        color = COLORS['red'] if state.get('led') == 6 else COLORS['amber'] if phase in ('YIELD', 'STOP_RED') else COLORS['cyan']
        phase_text = '模拟强制通行' if phase == 'FORCED' and not state.get('alarm') else PHASES.get(phase, phase)
        self.text.draw(canvas, phase_text, (840, 187), 26, color)
        self.text.draw(canvas, '%s  ·  模拟 %.0f km/h  ·  视频 %.1f FPS' % (DIRECTIONS.get(state.get('direction', 2)), state.get('speed', 0), rx_fps), (840, 225), 19)
        signal = state.get('signal', 0)
        for index, lamp_color in enumerate((COLORS['red'], COLORS['amber'], COLORS['green']), 1):
            cv2.circle(canvas, (1490 + (index - 1) * 25, 203), 8, lamp_color if signal == index else (72, 65, 58), -1, cv2.LINE_AA)
        self.buttons = []
        for index, (label, action) in enumerate(self.BUTTONS):
            left = 505 + index * 151
            rectangle = (left, 26, left + 143, 77)
            selected = action == {1: 'left', 2: 'straight', 3: 'right'}.get(state.get('direction'))
            cv2.rectangle(canvas, rectangle[:2], rectangle[2:], (75, 65, 39) if selected else (52, 42, 32), -1)
            self.text.draw(canvas, label, (left + 8, 43), 16, COLORS['red'] if action == 'force' else COLORS['text'])
            self.buttons.append((rectangle, action))
        ages = state.get('sources', [])
        if len(ages) == 2:
            self.text.draw(canvas, 'RK识别年龄 前 %.0f / 左 %.0f ms' % (ages[0]['age_ms'], ages[1]['age_ms']), (30, 866), 16, COLORS['muted'])
        self.text.draw(canvas, notice or 'F 强制启动报警 · B 停车清警 · G 恢复自动通行 · R 重置模拟 · Esc 退出', (610, 871), 16, COLORS['muted'])
        return canvas


def run(arguments):
    cv2.setNumThreads(1)
    receiver = Receiver(arguments.host, arguments.port, 10)
    client = JunctionClient(arguments.host, arguments.state_port)
    keys = FPGAKeyClient(arguments.host, arguments.key_port, print) if not arguments.no_hardware else None
    feedback = FPGAFeedbackClient(arguments.host, arguments.feedback_port) if not arguments.no_hardware else None
    converter, view = RGB565Converter(), JunctionView()
    mosaic = np.zeros((480, 1280, 3), np.uint8)
    current, rendered, last_paint = None, None, 0
    window_seen = False
    title = 'Waymo Live Junction - RK3568'
    def mouse(event, column, row, flags, parameter):
        if event == cv2.EVENT_LBUTTONUP:
            for (left, top, right, bottom), action in view.buttons:
                if left <= column < right and top <= row < bottom:
                    client.submit(action)
    cv2.namedWindow(title, cv2.WINDOW_NORMAL); cv2.resizeWindow(title, 1440, 810); cv2.setMouseCallback(title, mouse)
    receiver.start(); client.start()
    if keys: keys.start()
    if feedback: feedback.start()
    print('JUNCTION_PREVIEW_READY dual_inference=RK3568 hdmi=front ethernet=left ov5640=display_only', flush=True)
    try:
        while True:
            now = time.monotonic()
            frame, connected, received, fps, received_at = snapshot(receiver)
            if frame is not None and frame is not current:
                if frame.source != 0xfffffffe:
                    raise ValueError('需要三路拼接输出')
                mosaic = converter.convert(frame); current = frame
            state, state_at, notice = client.snapshot()
            fresh = bool(state and connected and now - received_at < 1 and now - state_at < .75)
            if keys:
                action = keys.take(now)
                if action: client.submit({'turn_left': 'left', 'straight': 'straight', 'turn_right': 'right'}[action])
            if feedback:
                mode = state.get('led', UNKNOWN) if fresh else UNKNOWN
                direction = state.get('direction', 0) if state else 0
                with feedback.lock:
                    feedback.latest = (mode, direction, True, 'junction_' + (state or {}).get('phase', 'STALE'))
                    feedback.updated = now
            if now - last_paint >= .05:
                rendered = view.render(mosaic, state, fresh, now, fps, notice)
                cv2.imshow(title, rendered); last_paint = now
            key = cv2.waitKey(5) & 255
            if key in (27, ord('q')): break
            action = {ord('j'): 'left', ord('i'): 'straight', ord('l'): 'right', ord('g'): 'go', ord('f'): 'force', ord('b'): 'brake', ord('r'): 'reset'}.get(key)
            if action: client.submit(action)
            visible = cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE)
            if visible >= 1: window_seen = True
            elif window_seen: break
    finally:
        receiver.stop(); client.stop()
        if keys: keys.stop()
        if feedback: feedback.stop()
        receiver.join(2); client.join(2)
        if keys: keys.join(1)
        if feedback: feedback.join(2)
        cv2.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=9004)
    parser.add_argument('--state-port', type=int, default=9021)
    parser.add_argument('--key-port', type=int, default=9016)
    parser.add_argument('--feedback-port', type=int, default=9017)
    parser.add_argument('--no-hardware', action='store_true')
    run(parser.parse_args())


if __name__ == '__main__':
    main()
