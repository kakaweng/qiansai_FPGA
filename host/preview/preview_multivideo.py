import argparse
from pathlib import Path
import socket
import threading
import time

from frame_to_png import checksum_backend
from multivideo_stream import read_frame


class Receiver(threading.Thread):
    def __init__(self, host, port, timeout=60):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.timeout = timeout
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.connection = None
        self.latest = None
        self.connected = False
        self.message = "Connecting"
        self.received = 0
        self.fps = 0.0
        self.received_at = 0.0

    def run(self):
        while not self.stop_event.is_set():
            connection = None
            try:
                connection = socket.create_connection((self.host, self.port), timeout=5)
                connection.settimeout(self.timeout)
                with self.lock:
                    self.connection = connection
                previous_sequence = None
                previous_source = None
                rate_started, rate_count = time.monotonic(), 0
                while not self.stop_event.is_set():
                    frame = read_frame(connection)
                    if previous_source == frame.source and previous_sequence is not None:
                        if frame.sequence <= previous_sequence:
                            raise ValueError("视频帧序号没有递增")
                    previous_sequence = frame.sequence
                    previous_source = frame.source
                    rate_count += 1
                    elapsed = time.monotonic() - rate_started
                    with self.lock:
                        self.latest = frame
                        self.connected = True
                        self.message = "Connected"
                        self.received += 1
                        self.received_at = time.monotonic()
                        if elapsed >= 1:
                            self.fps = rate_count / elapsed
                    if elapsed >= 1:
                        rate_started, rate_count = time.monotonic(), 0
            except (OSError, EOFError, ValueError) as error:
                with self.lock:
                    self.connected = False
                    self.message = str(error)
                if not self.stop_event.is_set():
                    print(f"接收中断：{error}；2 秒后重连", flush=True)
            finally:
                if connection is not None:
                    connection.close()
                with self.lock:
                    self.connection = None
            self.stop_event.wait(2)

    def stop(self):
        self.stop_event.set()
        with self.lock:
            connection = self.connection
            if connection is not None:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


def to_bgr(frame, numpy):
    packed = numpy.frombuffer(frame.pixels, dtype="<u2").reshape(frame.height, frame.width)
    image = numpy.empty((frame.height, frame.width, 3), dtype=numpy.uint8)
    red = (packed >> 11) & 31
    green = (packed >> 5) & 63
    blue = packed & 31
    image[:, :, 2] = (red << 3) | (red >> 2)
    image[:, :, 1] = (green << 2) | (green >> 4)
    image[:, :, 0] = (blue << 3) | (blue >> 2)
    return image


def run_headless(arguments):
    with socket.create_connection((arguments.host, arguments.port), timeout=5) as connection:
        connection.settimeout(arguments.timeout)
        previous_sequence = None
        previous_source = None
        for index in range(arguments.count):
            frame = read_frame(connection)
            if frame.source == previous_source and previous_sequence is not None:
                if frame.sequence <= previous_sequence:
                    raise ValueError("视频帧序号没有递增")
            previous_sequence = frame.sequence
            previous_source = frame.source
            if frame.source in (0xFFFFFFFF, 0xFFFFFFFE) and frame.hdmi_timestamp_ns and frame.camera_timestamp_ns:
                gap = f"{abs(frame.hdmi_timestamp_ns - frame.camera_timestamp_ns) / 1e6:.1f}"
            else:
                gap = "N/A"
            print(f"frame={index + 1} source={frame.source} size={frame.width}x{frame.height} "
                  f"sequence={frame.sequence} bytes={len(frame.pixels)} "
                  f"capture_gap_ms={gap}", flush=True)
    print(f"PASS: 已接收并校验 {arguments.count} 帧")


def run_window(arguments):
    import cv2
    import numpy

    receiver = Receiver(arguments.host, arguments.port, arguments.timeout)
    dashboard = client = keys_client = feedback_client = None
    if arguments.drive_panel or arguments.front_only:
        if arguments.front_p1:
            from front_live_preview import LiveClient as DriveClient, LiveDashboard as DriveDashboard
        else:
            from drive_preview import DriveClient, DriveDashboard
        client = DriveClient(arguments.control_url)
        dashboard = DriveDashboard(client, arguments.panel_font, front_only=arguments.front_only)
        client.start()
    if arguments.fpga_keys_port:
        from fpga_junction_keys import FPGAKeyClient
        print('FPGA_KEYS_ENABLED host=%s port=%d KEY5=left KEY6=straight KEY7=right'%(
            arguments.host,arguments.fpga_keys_port),flush=True)
        keys_client=FPGAKeyClient(arguments.host,arguments.fpga_keys_port,
                                  report=lambda message:print(message,flush=True))
        keys_client.start()
    if arguments.fpga_feedback_port:
        if not arguments.front_p1:
            raise ValueError('FPGA反馈灯需要当前HDMI P1界面')
        from fpga_drive_feedback import FPGAFeedbackClient
        feedback_client=FPGAFeedbackClient(arguments.host,arguments.fpga_feedback_port,arguments.fpga_led_active_low)
        feedback_client.start()
    receiver.start()
    window = ("RK3568 HDMI Front Assist" if arguments.front_only else
              "RK3568 Driving Assist + Three Camera Preview" if dashboard else
              "RK3568 HDMI + OV5640 + Ethernet")
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    width, height = getattr(dashboard, 'WINDOW_SIZE', (1152, 738)) if dashboard else (1280, 480)
    cv2.resizeWindow(window, width, height)
    if dashboard:
        cv2.setMouseCallback(window, dashboard.click)
    current_frame = None
    current_image = None
    has_shown = False
    try:
        while True:
            if keys_client:
                action=keys_client.take()
                if action:
                    applied=dashboard.handle_fpga_route(action)
                    print('FPGA_KEY_%s action=%s freeze=%s calibration=%s'%(
                        'APPLIED' if applied else 'IGNORED',action,dashboard.freeze_mode,dashboard.calibration),flush=True)
            with receiver.lock:
                frame = receiver.latest
                connected = receiver.connected
                received = receiver.received
                fps = receiver.fps
                received_at = receiver.received_at
            video_fresh = connected and 0 <= time.monotonic() - received_at <= 1
            if frame is not None and frame is not current_frame:
                current_frame = frame
                current_image = to_bgr(frame, numpy)
            if arguments.front_p1:
                # P1 compose only reads the frame and draws its own stale/FPS overlays.
                source = current_image if current_image is not None else numpy.zeros((480, 1280, 3), dtype=numpy.uint8)
                display = dashboard.compose(source, video_fresh, stats=(fps, received),
                                            video_ms=current_frame.timestamp_ns // 1000000 if current_frame is not None else None)
            else:
                if current_image is None:
                    display = numpy.zeros((480, 1280, 3), dtype=numpy.uint8)
                else:
                    display = current_image.copy()
                if dashboard and current_frame is not None and not video_fresh:
                    display //= 2
                    cv2.putText(display, "VIDEO STALE - WAITING FOR NEW FRAME", (245, 245),
                                cv2.FONT_HERSHEY_SIMPLEX, .85, (80, 110, 255), 2)
                if current_frame is not None and current_frame.source == 0xFFFFFFFF:
                    if arguments.front_only:
                        cv2.putText(display, "HDMI FRONT", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                    else:
                        cv2.putText(display, "HDMI", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                        cv2.putText(display, "OV5640", (652, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                elif current_frame is not None and current_frame.source == 0xFFFFFFFE:
                    for label, position in (("HDMI", (12, 28)), ("OV5640", (802, 28)), ("Ethernet", (802, 268))):
                        cv2.putText(display, label, position, cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                status = f"Preview {fps:.1f} FPS  Received {received}" if video_fresh else "VIDEO STALE / Connecting"
                color = (0, 255, 0) if video_fresh else (0, 0, 255)
                cv2.putText(display, status, (12, display.shape[0] - 12),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
                if dashboard:
                    display = dashboard.compose(display, video_fresh)
            if feedback_client:
                feedback_client.submit(dashboard)
            cv2.imshow(window, display)
            key = cv2.waitKey(30 if dashboard else 10) & 0xFF
            if key in (27, ord("q"), ord("Q")):
                break
            if dashboard:
                if arguments.front_p1:
                    dashboard.handle_key(key)
                else:
                    from drive_preview import key_action
                    action = key_action(key)
                    if action:
                        client.submit(action)
            if key in (ord("s"), ord("S")) and current_image is not None:
                directory = Path("multivideo_snapshots")
                directory.mkdir(exist_ok=True)
                path = directory / f"video_{time.time_ns()}.png"
                if not cv2.imwrite(str(path), display if dashboard else current_image):
                    raise OSError(f"截图保存失败：{path}")
                print(f"截图已保存：{path}")
            try:
                visible = cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE)
                if visible >= 1:
                    has_shown = True
                elif has_shown and visible < 1:
                    break
            except cv2.error:
                pass
    finally:
        if feedback_client:
            feedback_client.stop();feedback_client.join(timeout=2)
        if keys_client:
            keys_client.stop();keys_client.join(timeout=1)
        receiver.stop()
        receiver.join(timeout=2)
        if client:
            client.stop()
            client.join(timeout=2)
        cv2.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser(description="显示 RK3568 的 HDMI / OV5640 / Ethernet 视频")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9002)
    parser.add_argument("--headless", action="store_true", help="只接收并校验，不打开窗口")
    parser.add_argument("--count", type=int, default=10, help="无窗口检查的帧数")
    parser.add_argument("--timeout", type=float, default=60, help="接收无进展超时，单位秒，默认 60")
    parser.add_argument('--fpga-feedback-port',type=int,default=0,help='FPGA反馈灯服务转发端口，独立后台更新')
    parser.add_argument('--fpga-led-active-low',action='store_true',help='低电平点亮的LED配置，仅改变输出极性')
    parser.add_argument("--drive-panel", action="store_true", help="在同一预览窗口显示中文驾驶状态、按钮和反馈")
    parser.add_argument("--front-only", action="store_true", help="仅显示 HDMI 前方视频和单路辅助驾驶面板")
    parser.add_argument("--front-p1", action="store_true", help="HDMI P1：手动选灯、路口动作、模拟行驶和变道反馈")
    parser.add_argument("--control-url", default="http://127.0.0.1:9011", help="RK3568控制器的HDC转发地址")
    parser.add_argument("--panel-font", help="可选：中文字体文件路径")
    parser.add_argument("--fpga-keys-port",type=int,default=0,help="可选：FPGA方向键转发端口，0为关闭")
    arguments = parser.parse_args()
    if arguments.front_p1:
        arguments.front_only = True
    if arguments.front_only:
        arguments.drive_panel = True
    if not 1 <= arguments.port <= 65535 or not 1 <= arguments.count <= 1000000:
        parser.error("端口或帧数超出范围")
    if not 1 <= arguments.timeout <= 600:
        parser.error("接收超时必须在 1～600 秒之间")
    if not 0<=arguments.fpga_keys_port<=65535 or (arguments.fpga_keys_port and not arguments.front_p1):
        parser.error("FPGA方向键需要 --front-p1，端口为1～65535，0为关闭")
    print(f"校验后端：{checksum_backend()}")
    try:
        if arguments.headless:
            run_headless(arguments)
        else:
            run_window(arguments)
    except KeyboardInterrupt:
        pass
    except (OSError, EOFError, ValueError, ImportError) as error:
        parser.exit(1, f"视频预览失败：{error}\n")


if __name__ == "__main__":
    main()
