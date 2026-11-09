"""三路纯传输预览：完整校验，最新帧显示，不加载模型或驾驶面板。"""
import argparse
from pathlib import Path
import time

from frame_to_png import checksum_backend
from preview_multivideo import Receiver


class RGB565Converter:
    """Use OpenCV SIMD conversion, retaining the old bit-replicated RGB values."""
    def __init__(self):
        import cv2
        import numpy as np
        self.cv2, self.np = cv2, np
        values = np.arange(256, dtype=np.uint8)
        self.lut = np.empty((256, 1, 3), np.uint8)
        for channel, shift in enumerate((5, 6, 5)):
            self.lut[:, 0, channel] = values | (values >> shift)

    def convert(self, frame):
        packed = self.np.frombuffer(frame.pixels, self.np.uint8).reshape(frame.height, frame.width, 2)
        image = self.cv2.cvtColor(packed, self.cv2.COLOR_BGR5652BGR)
        return self.cv2.LUT(image, self.lut)


class DisplayCounter:
    """Count received-but-not-drawn frames; never use source sequence as UI FPS."""
    def __init__(self, now):
        self.last_received = 0
        self.drawn = self.skipped = self.interval_drawn = 0
        self.started = now
        self.fps = 0.0

    def draw(self, received):
        if received <= self.last_received:
            return False
        self.skipped += max(0, received - self.last_received - 1)
        self.last_received = received
        self.drawn += 1
        self.interval_drawn += 1
        return True

    def tick(self, now):
        elapsed = now - self.started
        if elapsed >= 1:
            self.fps = self.interval_drawn / elapsed
            self.started, self.interval_drawn = now, 0


def snapshot(receiver):
    with receiver.lock:
        return (receiver.latest, receiver.connected, receiver.received,
                receiver.fps, receiver.received_at)


def run(arguments):
    import cv2
    import numpy as np

    cv2.setNumThreads(1)
    converter = RGB565Converter()
    receiver = Receiver(arguments.host, arguments.port, arguments.timeout)
    counter = DisplayCounter(time.monotonic())
    # Cache all static decoration. No font discovery, resize or dashboard polling per frame.
    base = np.full((584, 1280, 3), (28, 21, 15), np.uint8)
    cv2.putText(base, 'RK3568  /  THREE VIDEO INPUTS', (18, 33),
                cv2.FONT_HERSHEY_SIMPLEX, .75, (238, 227, 208), 1, cv2.LINE_AA)
    cv2.putText(base, 'FPGA CAPTURE  >  RK3568 MOSAIC   |   TRANSPORT ONLY', (642, 31),
                cv2.FONT_HERSHEY_SIMPLEX, .48, (179, 161, 134), 1, cv2.LINE_AA)
    labels = ((12, 64, 'HDMI / FRONT'), (802, 64, 'OV5640 / LEFT'), (802, 304, 'ETHERNET / RIGHT'))
    waiting = np.zeros((480, 1280, 3), np.uint8)
    cv2.putText(waiting, 'WAITING FOR VERIFIED THREE-INPUT FRAMES', (225, 245),
                cv2.FONT_HERSHEY_SIMPLEX, .8, (172, 161, 144), 1, cv2.LINE_AA)
    image = waiting
    rendered = None
    current = None
    last_paint = last_log = 0.0
    fullscreen = arguments.fullscreen
    title = 'RK3568 Three Inputs - Transport Only'
    window_seen = False
    if not arguments.headless:
        cv2.namedWindow(title, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(title, 1280, 584)
        if fullscreen:
            cv2.setWindowProperty(title, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    receiver.start()
    started = time.monotonic()
    print('THREE_VIDEO_PREVIEW_VERSION transport-v1', flush=True)
    print('只接收纯三路拼接；S截图，F全屏，Q退出。LOCAL AGE是本机接收后的等待时间，不是端到端延迟。', flush=True)
    print('以太网停流可能保留最后一帧；当前协议不提供第三路独立新鲜度。', flush=True)
    try:
        while True:
            now = time.monotonic()
            frame, connected, received, rx_fps, received_at = snapshot(receiver)
            new_frame = frame is not None and frame is not current
            if new_frame:
                if frame.source != 0xFFFFFFFE:
                    raise ValueError('当前服务不是纯三路输出，请先运行 deploy_three_video_rk3568.sh start')
                current = frame
                image = converter.convert(frame)
                counter.draw(received)
            counter.tick(now)
            age_ms = max(0, (now - received_at) * 1000) if received_at else -1
            fresh = bool(connected and 0 <= age_ms <= 1000)
            # When the source stalls, update status at 4Hz without repeating video conversion.
            if not arguments.headless and (new_frame or now - last_paint >= .25):
                rendered = base.copy()
                rendered[52:532] = image
                for x, y, label in labels:
                    cv2.rectangle(rendered, (x - 4, y - 9), (x + 184, y + 14), (39, 29, 20), -1)
                    cv2.putText(rendered, label, (x, y + 7), cv2.FONT_HERSHEY_SIMPLEX,
                                .48, (232, 223, 201), 1, cv2.LINE_AA)
                color = (158, 219, 108) if fresh else (84, 157, 245)
                status = ('LIVE' if fresh else 'STALE / CONNECTING')
                text = '%s   RX %.1f FPS   DRAW %.1f FPS   SKIP %d   LOCAL AGE %.0f ms' % (
                    status, rx_fps, counter.fps, counter.skipped, age_ms)
                cv2.putText(rendered, text, (18, 555), cv2.FONT_HERSHEY_SIMPLEX, .51, color, 1, cv2.LINE_AA)
                cv2.putText(rendered, 'S snapshot  /  F fullscreen  /  Q exit    |    Latest verified frame only; no AI inference',
                            (18, 577), cv2.FONT_HERSHEY_SIMPLEX, .43, (167, 148, 124), 1, cv2.LINE_AA)
                cv2.imshow(title, rendered)
                last_paint = now
            if now - last_log >= 5:
                print('three_video received=%d drawn=%d rx_fps=%.2f draw_fps=%.2f gui_skipped=%d local_age_ms=%.1f connected=%d' % (
                    received, counter.drawn, rx_fps, counter.fps, counter.skipped, age_ms, connected), flush=True)
                last_log = now
            if arguments.seconds and now - started >= arguments.seconds:
                if current is None:
                    raise TimeoutError('检查期间未收到完整三路帧')
                if not fresh:
                    raise TimeoutError('检查结束时视频已断开或过期，请查看板端日志')
                break
            if arguments.headless:
                time.sleep(.005)
                continue
            key = cv2.waitKey(5) & 0xff
            if key in (27, ord('q'), ord('Q')):
                break
            if key in (ord('f'), ord('F')):
                fullscreen = not fullscreen
                cv2.setWindowProperty(title, cv2.WND_PROP_FULLSCREEN,
                                      cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL)
            if key in (ord('s'), ord('S')) and rendered is not None:
                arguments.snapshots.mkdir(parents=True, exist_ok=True)
                path = arguments.snapshots / ('three_%d.png' % time.time_ns())
                if not cv2.imwrite(str(path), rendered):
                    raise OSError('截图保存失败：' + str(path))
                print('已保存：' + str(path), flush=True)
            visible = cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE)
            if visible >= 1:
                window_seen = True
            elif window_seen:
                break
    finally:
        receiver.stop()
        receiver.join(timeout=2)
        if not arguments.headless:
            cv2.destroyAllWindows()
    print('THREE_VIDEO_PREVIEW_END received=%d drawn=%d gui_skipped=%d' % (
        receiver.received, counter.drawn, counter.skipped), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=9004)
    parser.add_argument('--timeout', type=float, default=5)
    parser.add_argument('--fullscreen', action='store_true')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--seconds', type=float, default=0)
    parser.add_argument('--snapshots', type=Path, default=Path('three_video_snapshots'))
    parser.add_argument('--allow-python-checksum', action='store_true', help='仅供开发测试；Ubuntu演示要求C校验')
    arguments = parser.parse_args()
    if arguments.timeout <= 0 or arguments.seconds < 0:
        parser.error('timeout必须大于0，seconds不能小于0')
    if arguments.headless and arguments.seconds == 0:
        parser.error('--headless需要指定--seconds，避免检查无限等待')
    backend = checksum_backend()
    print('校验后端：' + backend, flush=True)
    if backend != 'C/FNV-1a' and not arguments.allow_python_checksum:
        raise SystemExit('缺少C校验加速；请通过 preview_three_video.sh 启动，不能关闭校验换取帧率。')
    run(arguments)


if __name__ == '__main__':
    main()
