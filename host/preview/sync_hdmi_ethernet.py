"""Windows front-display / right-EVF1 player, using one source timeline."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import queue
import sys
import time
from dataclasses import replace

from sync_video_core import (PairPlan, PairWorker, PlaybackClock, load_timeline, write_manifest)


def monitors():
    if sys.platform != "win32":
        return []
    import ctypes as ct
    from ctypes import wintypes as wt
    result = []
    callback_type = ct.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ct.POINTER(wt.RECT), wt.LPARAM)

    def visit(handle, dc, rectangle, data):
        rect = rectangle.contents
        result.append((rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top))
        return True

    callback = callback_type(visit)
    if not ct.windll.user32.EnumDisplayMonitors(None, None, callback, 0):
        raise OSError("无法枚举显示器")
    return result


def enable_dpi_awareness():
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()


def select_video_sources(front=None, right=None):
    """Keep native file dialogs above terminal/VM windows and name cancellations."""
    import tkinter as tk
    from tkinter import filedialog
    chooser = tk.Tk()
    chooser.title("选择双路视频源")
    chooser.geometry("480x100+60+60")
    chooser.attributes("-topmost", True)
    tk.Label(chooser, text="请依次选择正前方视频和对应右侧视频。\n选择完成后才会生成板端控制连接配置。",
             padx=12, pady=18).pack()
    chooser.update()
    try:
        for role in ("front", "right"):
            if (front if role == "front" else right):
                continue
            title = "选择正前方视频（HDMI）" if role == "front" else "选择同一场景的右侧视频（以太网）"
            print("SOURCE_SELECT: " + title + "；请在Windows文件选择窗口操作。", flush=True)
            selected = filedialog.askopenfilename(parent=chooser, title=title,
                filetypes=[("视频文件", "*.mp4 *.avi *.mkv *.mov *.m4v *.wmv *.webm"), ("所有文件", "*.*")])
            if not selected:
                raise ValueError(title + "已取消；视频源没有启动，未生成新的连接配置")
            if role == "front":
                front = selected
            else:
                right = selected
            print(f"SOURCE_SELECTED {role}: {selected}", flush=True)
        return front, right
    finally:
        chooser.destroy()


class Player:
    def __init__(self, root, plan, worker, args, log_directory):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.root, self.plan, self.worker = tk, root, plan, worker
        self.args, self.directory = args, log_directory
        self.side_name = '左视' if getattr(args, 'side_label', 'right') == 'left' else '右视'
        self.offset_ns = round(args.right_offset_ms * 1e6)
        self.start, self.end = plan.bounds(self.offset_ns)
        self.clock = PlaybackClock(max(self.start, min(self.end - 1, round(args.start * 1e9))))
        self.clock.set_speed(args.speed)
        self.generation, self.last_request, self.result = 0, None, None
        self.control = None
        self.control_session, self.control_phase = 0, 1
        self.control_future = None
        self.last_refresh = 0
        if getattr(args, "controlled", False):
            import secrets
            from sync_source_control import SourceControl
            token = secrets.token_hex(24)
            self.control = SourceControl(args.control_host, args.control_port, token)
            config = {"host": args.control_host, "port": self.control.server.server_port, "token": token,
                      "started": datetime.now().isoformat(), "network": worker.network}
            (log_directory.parent / "source_control.json").write_text(
                json.dumps(config, indent=2) + "\n", encoding="utf-8")
            print(f"SOURCE_CONTROL_READY {args.control_host}:{config['port']}; waiting for RK3568; "
                  f"connection file={log_directory.parent / 'source_control.json'}", flush=True)
        self.closed, self.dragging, self.fullscreen = False, False, False
        self.display_size = None
        self.displays = monitors() or [(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())]
        if not 0 <= args.monitor < len(self.displays):
            raise ValueError(f"显示器编号应在0～{len(self.displays)-1}之间；可用 --list-monitors 查看")
        self.log = (log_directory / "frames.jsonl").open("w", encoding="utf-8")
        root.title("HDMI前视 / 以太网%s同步播放" % self.side_name)
        root.geometry("1060x760")
        root.minsize(1060, 740)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.state = tk.StringVar(value="已暂停；正在准备第一对画面")
        self.details = tk.StringVar()
        self.position = tk.DoubleVar()
        self.offset = tk.StringVar(value=str(args.right_offset_ms))
        self.monitor = tk.StringVar(value=str(args.monitor))
        self.speed = tk.StringVar(value=str(args.speed))
        ttk.Label(root, text="正前方 → HDMI　　%s → FPGA以太网　　OV5640 → 独立视频输入" % self.side_name,
                  font=("Microsoft YaHei UI", 13, "bold")).pack(pady=8)
        ttk.Label(root, text="同一源时间轴播放；实际HDMI/FPGA接收延迟尚未测量。请使用同一行驶场景的对应视角。",
                  foreground="#8b5700").pack()
        previews = ttk.Frame(root)
        previews.pack(fill="x", padx=10, pady=8)
        self.front_preview, self.right_preview = tk.Label(previews, bg="black"), tk.Label(previews, bg="black")
        self.front_preview.pack(side="left", padx=5)
        self.right_preview.pack(side="left", padx=5)
        controls = ttk.Frame(root)
        controls.pack(fill="x", padx=12)
        for text, command in (("播放 / 暂停 [空格]", self.toggle), ("从头重播 [R]", self.restart),
                              ("后退1秒", lambda: self.seek_delta(-1)), ("前进1秒", lambda: self.seek_delta(1)),
                              ("上一帧 [←]", lambda: self.step(-1)), ("下一帧 [→]", lambda: self.step(1))):
            ttk.Button(controls, text=text, command=command).pack(side="left", padx=3)
        slider = ttk.Scale(root, variable=self.position, from_=self.start / 1e9, to=(self.end - 1) / 1e9)
        slider.pack(fill="x", padx=16, pady=12)
        self.slider = slider
        slider.bind("<ButtonPress-1>", self.begin_seek)
        slider.bind("<ButtonRelease-1>", self.end_seek)
        settings = ttk.Frame(root)
        settings.pack(fill="x", padx=16)
        ttk.Label(settings, text="HDMI显示器编号：").pack(side="left")
        ttk.Combobox(settings, textvariable=self.monitor, width=4, state="readonly",
                     values=[str(i) for i in range(len(self.displays))]).pack(side="left")
        ttk.Button(settings, text="在所选屏幕全屏 [F]", command=self.show_fullscreen).pack(side="left", padx=6)
        ttk.Button(settings, text="退出全屏 [Esc]", command=self.leave_fullscreen).pack(side="left")
        ttk.Label(settings, text="　速度：").pack(side="left")
        speed = ttk.Combobox(settings, textvariable=self.speed, width=5, state="readonly", values=("0.5", "1.0", "2.0"))
        speed.pack(side="left")
        speed.bind("<<ComboboxSelected>>", lambda event: self.change_speed())
        adjustment = ttk.Frame(root)
        adjustment.pack(fill="x", padx=16, pady=10)
        ttk.Label(adjustment, text="%s素材时间偏移(ms，正值=侧视提前)：" % self.side_name).pack(side="left")
        ttk.Spinbox(adjustment, textvariable=self.offset, from_=-10000, to=10000, increment=50, width=8).pack(side="left")
        ttk.Button(adjustment, text="应用偏移", command=self.apply_offset).pack(side="left", padx=6)
        ttk.Label(root, textvariable=self.state, font=("Microsoft YaHei UI", 11, "bold"), wraplength=1020).pack(anchor="w", padx=16)
        ttk.Label(root, textvariable=self.details, justify="left", wraplength=1020).pack(anchor="w", padx=16, pady=6)
        ttk.Label(root, text=f"正前方：{plan.front.path}\n{self.side_name}：{plan.right.path}\n记录：{log_directory}",
                  justify="left", wraplength=1020).pack(anchor="w", padx=16)
        self.hdmi = tk.Toplevel(root)
        self.hdmi.title("HDMI FRONT — F全屏 / Esc退出全屏 / 空格暂停")
        self.hdmi.geometry("960x540")
        self.hdmi_label = tk.Label(self.hdmi, bg="black", borderwidth=0)
        self.hdmi_label.pack(fill="both", expand=True)
        self.hdmi.protocol("WM_DELETE_WINDOW", self.hide_hdmi)
        self.hdmi.bind("<Configure>", self.on_resize)
        for window in (root, self.hdmi):
            window.bind("<space>", self.key_toggle)
            window.bind("<Left>", lambda event: self.key_step(-1))
            window.bind("<Right>", lambda event: self.key_step(1))
            window.bind("r", lambda event: self.restart())
            window.bind("R", lambda event: self.restart())
            window.bind("f", lambda event: self.show_fullscreen())
            window.bind("F", lambda event: self.show_fullscreen())
            window.bind("<Escape>", lambda event: self.leave_fullscreen())
        if args.fullscreen:
            self.show_fullscreen()
        self.tick()

    def key_toggle(self, event):
        if event.widget.winfo_class() not in ("TEntry", "TSpinbox", "TCombobox"):
            self.toggle()
            return "break"

    def key_step(self, direction):
        self.step(direction)
        return "break"

    def invalidate(self):
        self.generation += 1
        self.last_request = None

    def toggle(self):
        if self.control:
            return
        self.clock.play(not self.clock.playing)
        self.invalidate()

    def restart(self):
        if self.control:
            return
        self.clock.seek(self.start)
        self.invalidate()

    def seek_delta(self, seconds):
        if self.control:
            return
        self.clock.seek(max(self.start, min(self.end - 1, self.clock.position() + round(seconds * 1e9))))
        self.invalidate()

    def step(self, direction):
        if self.control:
            return
        self.clock.play(False)
        request = self.plan.request(self.clock.position(), right_offset_ns=self.offset_ns)
        index = max(0, min(len(self.plan.front.times_ns) - 1, request.front_index + direction))
        target = self.plan.front_origin + self.plan.front.times_ns[index]
        self.clock.seek(max(self.start, min(self.end - 1, target)))
        self.invalidate()

    def begin_seek(self, event):
        if self.control:
            return
        self.dragging = True
        self.resume_after_seek = self.clock.playing
        self.clock.play(False)

    def end_seek(self, event):
        if self.control:
            return
        self.clock.seek(round(self.position.get() * 1e9))
        self.clock.play(self.resume_after_seek)
        self.dragging = False
        self.invalidate()

    def change_speed(self):
        if self.control:
            self.speed.set("1.0")
            return
        self.clock.set_speed(float(self.speed.get()))

    def apply_offset(self):
        if self.control:
            return
        from tkinter import messagebox
        try:
            offset = float(self.offset.get())
            if not -10000 <= offset <= 10000:
                raise ValueError("右侧时间偏移需在-10000～10000ms之间")
            bounds = self.plan.bounds(round(offset * 1e6))
            self.offset_ns, (self.start, self.end) = round(offset * 1e6), bounds
            self.slider.configure(from_=self.start / 1e9, to=(self.end - 1) / 1e9)
            self.clock.seek(max(self.start, min(self.end - 1, self.clock.position())))
            self.invalidate()
        except ValueError as error:
            messagebox.showerror("时间偏移无效", str(error))

    def show_fullscreen(self):
        self.hdmi.deiconify()
        x, y, width, height = self.displays[int(self.monitor.get())]
        self.hdmi.overrideredirect(True)
        self.hdmi.geometry(f"{width}x{height}{x:+d}{y:+d}")
        self.fullscreen = True
        self.hdmi.lift()
        self.hdmi.focus_set()
        self.draw_hdmi()

    def leave_fullscreen(self):
        if self.control and self.control_session:
            self.clock.play(False)
            self.control_phase = 3
            self.invalidate()
        self.hdmi.overrideredirect(False)
        self.hdmi.geometry("960x540+40+40")
        self.fullscreen = False

    def hide_hdmi(self):
        self.clock.play(False)
        if self.control:
            self.control_phase = 3
        self.invalidate()
        self.hdmi.withdraw()

    def poll_control(self):
        if not self.control:
            return
        if self.control_future is None:
            try:
                command, future = self.control.commands.get_nowait()
            except queue.Empty:
                return
            try:
                action, session = command["action"], command["session"]
                if action == "prepare":
                    if self.worker.network:
                        display = self.displays[int(self.monitor.get())]
                        if display[2:] != (1920, 1080):
                            raise ValueError("HDMI输出显示器必须设为1920×1080；请核对Windows显示设置")
                        self.show_fullscreen()
                    self.control_session, self.control_phase = session, 1
                    self.clock.play(False)
                    self.clock.set_speed(1)
                    self.clock.seek(self.start)
                elif session != self.control_session:
                    raise ValueError("播放编号不匹配，请重新准备")
                elif action == "play":
                    if self.control_phase != 1:
                        raise ValueError("需要先完成prepare")
                    if self.worker.network and not self.fullscreen:
                        raise ValueError("HDMI未全屏，请重新准备")
                    self.control_phase = 2
                    self.clock.play(True)
                else:
                    self.control_phase = 3
                    self.clock.play(False)
                self.invalidate()
                self.control_future = (command, future, self.generation)
            except (ValueError, RuntimeError) as error:
                future.set_result({"ok": False, "error": str(error)})

    def publish_control(self):
        if not self.control:
            return
        phase = "PLAYING" if self.control_phase == 2 else "PREPARED" if self.control_phase == 1 else "HELD"
        if self.control_future:
            phase = "PREPARING" if self.control_future[0]["action"] == "prepare" else phase
        self.control.snapshot = {"state": phase if self.control_session else "WAITING",
            "session": self.control_session, "network": self.worker.network,
            "fullscreen": self.fullscreen, "physical_sync": "awaiting_board_timecode_verification"}
        if self.control_future and self.result and self.result.request.generation == self.control_future[2]:
            command, future, _ = self.control_future
            # This acknowledges UDP submission and the Tk draw request, not physical scan-out.
            self.control.snapshot["state"] = "PREPARED" if self.control_phase == 1 else phase
            future.set_result({"ok": True, "id": command["id"], **self.control.snapshot})
            self.control_future = None

    def on_resize(self, event):
        if event.widget == self.hdmi and self.display_size != (event.width, event.height):
            self.display_size = event.width, event.height
            self.draw_hdmi()

    def set_image(self, label, bgr, size):
        from PIL import Image, ImageTk
        from sync_video_core import fit_frame
        image = Image.fromarray(fit_frame(bgr, *size)[:, :, ::-1])
        label.image = ImageTk.PhotoImage(image)
        label.configure(image=label.image)

    def draw_hdmi(self):
        if self.result is not None:
            size = self.display_size or (960, 540)
            if size[0] > 1 and size[1] > 1:
                self.set_image(self.hdmi_label, self.result.front, size)

    def tick(self):
        if self.closed:
            return
        self.poll_control()
        if self.worker.error:
            self.clock.play(False)
            self.state.set("输出停止：" + self.worker.error)
            if self.control:
                self.control.snapshot = {"state": "ERROR", "session": self.control_session, "error": self.worker.error}
                if self.control_future:
                    self.control_future[1].set_result({"ok": False, "error": self.worker.error})
                    self.control_future = None
            self.root.after(50, self.tick)
            return
        position = self.clock.position()
        if position >= self.end:
            self.clock.play(False)
            self.clock.seek(self.end - 1)
            position = self.end - 1
            if self.control and self.control_phase == 2:
                self.control_phase = 3
                self.invalidate()
        if not self.dragging:
            self.position.set(position / 1e9)
            request = self.plan.request(position, self.generation, self.offset_ns)
            if self.control_session:
                request = replace(request, session=self.control_session, phase=self.control_phase,
                                  right_offset_ns=self.offset_ns)
            # Refresh held frames so the board can distinguish a live source from a frozen link.
            if request != self.last_request or ((self.control_session or getattr(self.args, 'refresh_paused', False)) and time.perf_counter() - self.last_refresh > .25):
                self.worker.submit(request)
                self.last_request = request
                self.last_refresh = time.perf_counter()
        try:
            result = self.worker.results.get_nowait()
        except queue.Empty:
            result = None
        if result is not None and result.request.generation == self.generation:
            self.result = result
            self.set_image(self.front_preview, result.front, (500, 282))
            self.set_image(self.right_preview, result.right, (500, 282))
            self.draw_hdmi()
            draw_ns = time.perf_counter_ns()
            front_ns, right_ns = self.plan.times(result.request)
            self.log.write(json.dumps({"generation": self.generation, "wire_id": result.wire_id,
                "front_frame": result.request.front_index, "right_frame": result.request.right_index,
                "front_pts_ns": front_ns, "right_pts_ns": right_ns, "right_offset_ns": self.offset_ns,
                "source_difference_ms": (right_ns - front_ns) / 1e6,
                "udp_submitted_qpc_ns": result.submitted_ns if self.worker.network else None,
                "hdmi_draw_requested_qpc_ns": draw_ns, "udp_packets": result.packet_count,
                "ov5640_sync": "manual_unverified", "receiver_sync": "not_measured"}) + "\n")
            self.log.flush()
        self.publish_control()
        state = "播放中" if self.clock.playing else "已暂停 / 到达结尾" if position >= self.end - 1 else "已暂停"
        if self.control:
            state += f"　板端控制模式 SESSION:{self.control_session}；本地播放/跳转锁定"
        self.state.set(f"{state}　场景时间 {position / 1e9:.3f}s　OV5640：手动对齐，未确认同步")
        if self.result:
            front_ns, right_ns = self.plan.times(self.result.request)
            elapsed = (self.result.submitted_ns - self.result.started_ns) / 1e6
            self.details.set(f"FRONT帧 {self.result.request.front_index} / {self.side_name}帧 {self.result.request.right_index}　"
                f"源时间差 {(right_ns-front_ns)/1e6:+.1f}ms　输出准备/发送 {elapsed:.1f}ms　"
                f"{'UDP已提交（接收未确认）' if self.worker.network else '本地预演，未发送网络'}\n"
                "HDMI请设置为1920×1080并选择正确显示器全屏；角落F/T/TX用于板端核对。")
        self.root.after(15, self.tick)

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.worker.close()
            finally:
                if self.control:
                    self.control.close()
                self.log.close()
                self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="通用双路源时间轴播放：前方HDMI，右侧EVF1以太网；OV5640手动对齐")
    parser.add_argument("--scene", type=Path, help="便捷入口：目录内front.mp4、side_right.mp4和可选frame_timestamps.csv")
    parser.add_argument("--front", type=Path, help="任意数据集的正前方本地视频")
    parser.add_argument("--right", type=Path, help="同一行驶场景的右侧本地视频")
    parser.add_argument("--side-label", choices=('left', 'right'), default='right', help="以太网视角标签；--right仍为兼容的第二路文件参数")
    parser.add_argument("--refresh-paused", action='store_true', help="暂停时每250ms重发同一静止画面；媒体时间不前进")
    parser.add_argument("--timestamps", type=Path, help="两路共用时间戳CSV")
    parser.add_argument("--front-timestamps", type=Path)
    parser.add_argument("--right-timestamps", type=Path)
    parser.add_argument("--assume-cfr", action="store_true", help="仅在媒体PTS不可用时允许按声明FPS估算")
    parser.add_argument("--host", default="192.168.1.10")
    parser.add_argument("--port", type=int, default=5002)
    parser.add_argument("--bind", default="", help="可指定FPGA网卡IPv4，例如192.168.1.102")
    parser.add_argument("--packet-gap-us", type=float, default=100)
    parser.add_argument("--right-offset-ms", type=float, default=0)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--speed", type=float, default=1)
    parser.add_argument("--monitor", type=int, default=0)
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--controlled", action="store_true", help="等待RK3568指令，锁定本地播放/跳转；保留机器可读帧标记")
    parser.add_argument("--control-host", default="127.0.0.1", help="控制监听地址；虚拟机中转请指定Windows VMnet网卡地址")
    parser.add_argument("--control-port", type=int, default=9012)
    parser.add_argument("--list-monitors", action="store_true")
    parser.add_argument("--no-network", action="store_true", help="本地预演，不向FPGA发送")
    parser.add_argument("--no-markers", action="store_true", help="校准后关闭源画面中的F/T/TX标记")
    parser.add_argument("--inspect-only", action="store_true", help="只检查视频时间轴，不开窗口或发送")
    parser.add_argument("--headless", action="store_true", help="处理有限对帧，不请求实际HDMI显示")
    parser.add_argument("--frames", type=int, default=5, help="headless模式的有限帧对数量")
    args = parser.parse_args()
    if args.controlled:
        if args.headless or args.inspect_only or args.speed != 1 or args.start != 0:
            parser.error("板端控制模式需要GUI、速度1和起始位置0")
        args.fullscreen = args.fullscreen or not args.no_network
    enable_dpi_awareness()
    if args.list_monitors:
        for index, (x, y, width, height) in enumerate(monitors()):
            print(f"monitor={index} origin=({x},{y}) size={width}x{height}")
        return
    if args.scene:
        if args.front or args.right:
            parser.error("--scene 与 --front/--right 二选一")
        args.front, args.right = args.scene / "front.mp4", args.scene / "side_right.mp4"
        candidate = args.scene / "frame_timestamps.csv"
        if not args.timestamps and candidate.exists():
            args.timestamps = candidate
    if not args.front or not args.right:
        if args.headless or args.inspect_only:
            parser.error("请提供 --front 和 --right，或 --scene")
        args.front, args.right = select_video_sources(args.front, args.right)
    if not -10000 <= args.right_offset_ms <= 10000 or not .1 <= args.speed <= 4 or not 0 <= args.start < 86400:
        parser.error("时间偏移、速度或开始时间超出范围")
    if not 1 <= args.frames <= 10000:
        parser.error("--frames 应在1～10000之间")
    print("读取前方时间轴...", flush=True)
    front = load_timeline(args.front, args.front_timestamps or args.timestamps, args.assume_cfr)
    print("读取%s时间轴..." % ('左视' if args.side_label == 'left' else '右视'), flush=True)
    right = load_timeline(args.right, args.right_timestamps or args.timestamps, args.assume_cfr)
    plan = PairPlan(front, right)
    offset = round(args.right_offset_ms * 1e6)
    start, end = plan.bounds(offset)
    print(f"FRONT {front.width}x{front.height} frames={len(front.times_ns)} time_source={front.source}")
    print(f"{args.side_label.upper()} {right.width}x{right.height} frames={len(right.times_ns)} time_source={right.source}")
    print(f"COMMON [{start/1e9:.3f}, {end/1e9:.3f}) seconds; OV5640=manual_unverified")
    if args.inspect_only:
        print("SOURCE_TIMELINE_PASS; physical output synchronization unmeasured")
        return
    directory = Path(__file__).resolve().parent / "sync_playback_runs" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    directory.mkdir(parents=True)
    write_manifest(directory / "session.json", plan, {name: str(value) if isinstance(value, Path) else value
                   for name, value in vars(args).items()})
    worker = PairWorker(plan, args.host, args.port, args.bind, args.packet_gap_us, not args.no_network,
                        not args.no_markers)
    try:
        if args.headless:
            processed = 0
            with (directory / "frames.jsonl").open("w", encoding="utf-8") as log:
                initial = plan.request(max(start, round(args.start * 1e9)), right_offset_ns=offset).front_index
                for index in range(initial, min(initial + args.frames, len(front.times_ns))):
                    timestamp = plan.front_origin + front.times_ns[index]
                    if timestamp >= end:
                        break
                    worker.submit(plan.request(timestamp, right_offset_ns=offset))
                    deadline = time.monotonic() + 15
                    while True:
                        if worker.error:
                            raise RuntimeError(worker.error)
                        try:
                            result = worker.results.get(timeout=.1)
                            break
                        except queue.Empty:
                            if time.monotonic() > deadline:
                                raise TimeoutError("等待输出帧对超时")
                    pts = plan.times(result.request)
                    entry = {"front_frame": result.request.front_index, "right_frame": result.request.right_index,
                             "source_difference_ms": (pts[1]-pts[0])/1e6, "udp_packets": result.packet_count,
                             "wire_id": result.wire_id, "hdmi_display": "not_requested_headless"}
                    log.write(json.dumps(entry) + "\n")
                    print(json.dumps(entry), flush=True)
                    processed += 1
            if processed == 0:
                raise ValueError("当前起始位置没有可处理的帧对")
            print(f"PAIR_HEADLESS_PASS pairs={processed}; receiver and HDMI output not verified; log={directory}")
        else:
            import tkinter as tk
            root = tk.Tk()
            app = Player(root, plan, worker, args, directory)
            try:
                root.mainloop()
            finally:
                if not app.closed:
                    app.close()
    finally:
        worker.close()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, TimeoutError, ImportError) as error:
        raise SystemExit(f"同步播放失败：{error}")
