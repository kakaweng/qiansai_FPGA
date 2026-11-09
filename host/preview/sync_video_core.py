"""Dataset-independent, timestamp-based HDMI / EVF1 playback primitives.

This synchronizes source content. UDP submission and a desktop draw request do
not measure HDMI scan-out, FPGA reception, or camera exposure synchronization.
"""
from bisect import bisect_right
import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path
import queue
import socket
import threading
import time

import cv2
import numpy as np

from send_ethernet_video import packets


def packet_wait_until(deadline):
    # Python 3.11 on Windows can implement monotonic() with 15.625ms
    # GetTickCount64. perf_counter() uses QPC and can pace microsecond packets.
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        if remaining > .002:
            time.sleep(remaining - .001)


@dataclass(frozen=True)
class Timeline:
    path: Path
    times_ns: tuple
    end_ns: int
    width: int
    height: int
    source: str
    origin_ns: object = None

    def index_at(self, timestamp_ns):
        return max(0, min(len(self.times_ns) - 1, bisect_right(self.times_ns, timestamp_ns) - 1))


def _csv_times(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError(f"时间戳文件为空: {path}")
    for index, row in enumerate(rows):
        if "video_frame_index" in row and int(row["video_frame_index"]) != index:
            raise ValueError("video_frame_index 必须从0开始连续排列")
    origin = None
    if "source_timestamp_micros" in rows[0]:
        raw = [int(row["source_timestamp_micros"]) * 1000 for row in rows]
        origin = raw[0]
    elif "timestamp_ns" in rows[0]:
        raw = [int(row["timestamp_ns"]) for row in rows]
        origin = raw[0]
    else:
        field = "pts_seconds" if "pts_seconds" in rows[0] else "relative_time_seconds"
        raw = [round(float(row[field]) * 1e9) for row in rows]
    first = raw[0]
    times = [value - first for value in raw]
    duration = rows[-1].get("display_duration_seconds")
    last_duration = round(float(duration) * 1e9) if duration else None
    return times, origin, last_duration


def load_timeline(path, timestamps=None, assume_cfr=False):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f"找不到本地视频: {path}；本版尚不接入实时RTSP/USB流")
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"无法打开视频: {path}")
    try:
        width, height = (int(capture.get(prop)) for prop in (cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT))
        fps = capture.get(cv2.CAP_PROP_FPS)
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        origin = last_duration = None
        if timestamps:
            times, origin, last_duration = _csv_times(timestamps)
            source = f"csv:{Path(timestamps).resolve()}"
            if count > 0 and count != len(times):
                raise ValueError(f"时间戳数量{len(times)}与视频帧数{count}不一致: {path}")
        else:
            # Decode headers/frames through the backend to retain VFR PTS. No
            # frame-number/fps substitution unless the user explicitly opts in.
            times = []
            while capture.grab():
                milliseconds = capture.get(cv2.CAP_PROP_POS_MSEC)
                if not math.isfinite(milliseconds):
                    raise ValueError(f"媒体时间戳无效: {path}")
                times.append(round(milliseconds * 1e6))
            source = "opencv-media-pts"
            if times:
                first = times[0]
                times = [value - first for value in times]
        valid = bool(times) and all(a < b for a, b in zip(times, times[1:]))
        if not valid and assume_cfr and not timestamps and math.isfinite(fps) and fps > 0:
            times = [round(index * 1e9 / fps) for index in range(len(times))]
            source = "assumed-CFR-estimated"
            valid = bool(times)
        if not valid:
            raise ValueError("视频时间戳不递增；请提供CSV，或确认固定帧率后使用 --assume-cfr")
        if width <= 0 or height <= 0:
            raise ValueError("视频尺寸无效")
        if last_duration is None:
            last_duration = times[-1] - times[-2] if len(times) > 1 else round(1e9 / fps) if fps > 0 else 100_000_000
        if last_duration <= 0:
            raise ValueError("最后一帧显示时长必须为正数")
        return Timeline(path, tuple(times), times[-1] + last_duration, width, height, source, origin)
    finally:
        capture.release()


@dataclass(frozen=True)
class PairRequest:
    generation: int
    front_index: int
    right_index: int
    session: int = 0
    phase: int = 0
    right_offset_ns: int = 0


class PairPlan:
    def __init__(self, front, right):
        self.front, self.right = front, right
        self.front_origin = self.right_origin = 0
        if front.origin_ns is not None and right.origin_ns is not None:
            origin = min(front.origin_ns, right.origin_ns)
            self.front_origin = front.origin_ns - origin
            self.right_origin = right.origin_ns - origin

    def bounds(self, right_offset_ns=0):
        start = max(self.front_origin, self.right_origin - right_offset_ns)
        end = min(self.front_origin + self.front.end_ns,
                  self.right_origin + self.right.end_ns - right_offset_ns)
        if start >= end:
            raise ValueError("两路视频在当前时间偏移下没有共同区间")
        return start, end

    def request(self, scene_ns, generation=0, right_offset_ns=0):
        start, end = self.bounds(right_offset_ns)
        scene_ns = max(start, min(end - 1, scene_ns))
        return PairRequest(generation, self.front.index_at(scene_ns - self.front_origin),
                           self.right.index_at(scene_ns + right_offset_ns - self.right_origin))

    def times(self, request):
        return (self.front_origin + self.front.times_ns[request.front_index],
                self.right_origin + self.right.times_ns[request.right_index])


class PlaybackClock:
    def __init__(self, start_ns=0, now=time.perf_counter):
        self.now, self.anchor, self.position_ns = now, now(), start_ns
        self.playing, self.speed = False, 1.0

    def position(self):
        return self.position_ns + (round((self.now() - self.anchor) * self.speed * 1e9) if self.playing else 0)

    def seek(self, position_ns):
        self.position_ns, self.anchor = int(position_ns), self.now()

    def play(self, enabled):
        self.seek(self.position())
        self.playing = bool(enabled)

    def set_speed(self, speed):
        if not math.isfinite(speed) or not .1 <= speed <= 4:
            raise ValueError("播放速度需在0.1～4之间")
        self.seek(self.position())
        self.speed = speed


class IndexedReader:
    def __init__(self, timeline):
        self.capture = cv2.VideoCapture(str(timeline.path))
        if not self.capture.isOpened():
            raise ValueError(f"无法打开视频: {timeline.path}")
        self.index, self.image = -1, None

    def read(self, index):
        if index == self.index:
            return self.image
        if index < self.index or index - self.index > 30:
            if not self.capture.set(cv2.CAP_PROP_POS_FRAMES, index):
                raise ValueError("解码器不支持帧跳转")
            self.index = index - 1
        while self.index < index:
            ok, image = self.capture.read()
            if not ok:
                raise ValueError(f"无法解码第{index}帧")
            self.index += 1
            self.image = image
        return self.image

    def close(self):
        self.capture.release()


def fit_frame(image, width, height):
    scale = min(width / image.shape[1], height / image.shape[0])
    w, h = min(width, max(1, round(image.shape[1] * scale))), min(height, max(1, round(image.shape[0] * scale)))
    output = np.zeros((height, width, 3), np.uint8)
    x, y = (width - w) // 2, (height - h) // 2
    output[y:y+h, x:x+w] = cv2.resize(image, (w, h), interpolation=cv2.INTER_AREA)
    return output


def stamp_frame(image, role, index, pts_ns, wire_id):
    scale = 1.5 if image.shape[1] >= 1000 else .8
    # Existing board lane/object status occupies the top 80 pixels of the
    # 640x360 HDMI tile (240 source pixels). Keep the diagnostic timecode below
    # that header, in the upper image, rather than losing it under the status.
    top = round(image.shape[0] * .25) if role == "FRONT" else 0
    cv2.rectangle(image, (0, top), (image.shape[1] - 1, top + round(52 * scale)), (0, 0, 0), -1)
    text = f"{role} F:{index:05d} T:{pts_ns / 1e9:.3f}s TX:{wire_id}"
    cv2.putText(image, text, (round(10 * scale), top + round(36 * scale)), cv2.FONT_HERSHEY_SIMPLEX,
                scale, (255, 255, 255), max(1, round(2 * scale)), cv2.LINE_AA)


def rgb565_bytes(image):
    blue, green, red = (image[:, :, channel].astype(np.uint16) for channel in range(3))
    return (((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)).astype("<u2").tobytes()


@dataclass
class PairResult:
    request: PairRequest
    front: object
    right: object
    wire_id: int
    started_ns: int
    submitted_ns: int
    packet_count: int


class PairWorker:
    """One processing pair plus one replaceable pending pair; no video backlog.

    Finish one complete EVF1 frame before offering its HDMI partner to the UI.
    Generation changes discard outdated results and schedule a corrective pair.
    """
    def __init__(self, plan, host="192.168.1.10", port=5002, bind="", packet_gap_us=100,
                 network=True, markers=True):
        if not 0 < port <= 65535 or not math.isfinite(packet_gap_us) or not 0 <= packet_gap_us <= 10000:
            raise ValueError("端口或包间隔无效")
        self.plan, self.destination, self.bind = plan, (host, port), bind
        self.packet_gap_us, self.network = packet_gap_us, network
        self.markers = markers
        self.condition, self.results = threading.Condition(), queue.Queue(maxsize=1)
        self.pending, self.stopping, self.error = None, False, None
        self.replaced, self.sent = 0, 0
        self.thread = threading.Thread(target=self._run, name="hdmi-ethernet-pair", daemon=True)
        self.thread.start()

    def submit(self, request):
        with self.condition:
            if self.stopping or self.error:
                return False
            if self.pending is not None:
                self.replaced += 1
            self.pending = request
            self.condition.notify()
            return True

    def _run(self):
        front = right = connection = None
        try:
            front = IndexedReader(self.plan.front)
            right = IndexedReader(self.plan.right)
            if self.network:
                connection = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                connection.settimeout(.5)
                if self.bind:
                    connection.bind((self.bind, 0))
                connection.connect(self.destination)
            while True:
                with self.condition:
                    while self.pending is None and not self.stopping:
                        self.condition.wait()
                    if self.stopping:
                        return
                    request, self.pending = self.pending, None
                started = time.perf_counter_ns()
                hdmi = fit_frame(front.read(request.front_index), 1920, 1080)
                ethernet = fit_frame(right.read(request.right_index), 640, 480)
                wire_id = (self.sent + 1) & 0xffffffff
                front_ns, right_ns = self.plan.times(request)
                if self.markers:
                    stamp_frame(hdmi, "FRONT", request.front_index, front_ns, wire_id)
                    stamp_frame(ethernet, "RIGHT", request.right_index, right_ns, wire_id)
                if request.session:
                    from sync_timecode import stamp_timecode
                    stamp_timecode(hdmi, 1, request.phase, request.session, wire_id, front_ns, request.front_index)
                    stamp_timecode(ethernet, 2, request.phase, request.session, wire_id,
                                   right_ns - request.right_offset_ns, request.right_index)
                count = 0
                if connection:
                    deadline = time.perf_counter()
                    for message in packets(rgb565_bytes(ethernet), wire_id):
                        if self.stopping:
                            return
                        packet_wait_until(deadline)
                        if connection.send(message) != len(message):
                            raise OSError("UDP数据报发送不完整")
                        count += 1
                        deadline = max(deadline, time.perf_counter()) + self.packet_gap_us / 1e6
                self.sent += 1
                result = PairResult(request, hdmi, ethernet, wire_id, started, time.perf_counter_ns(), count)
                try:
                    self.results.get_nowait()
                except queue.Empty:
                    pass
                self.results.put_nowait(result)
        except Exception as error:
            self.error = f"{type(error).__name__}: {error}"
        finally:
            if front:
                front.close()
            if right:
                right.close()
            if connection:
                connection.close()

    def close(self):
        with self.condition:
            self.stopping = True
            self.pending = None
            self.condition.notify_all()
        self.thread.join(5)
        if self.thread.is_alive():
            raise RuntimeError("视频发送线程未在5秒内退出")


def write_manifest(path, plan, options):
    def video(timeline):
        stat = timeline.path.stat()
        return {"path": str(timeline.path), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                "size": [timeline.width, timeline.height], "frames": len(timeline.times_ns),
                "duration_seconds": timeline.end_ns / 1e9, "time_source": timeline.source,
                "origin_ns": timeline.origin_ns}
    data = {"version": "hdmi-ethernet-source-sync-v1", "front": video(plan.front), "right": video(plan.right),
            "output": {"hdmi": [1920, 1080], "ethernet": [640, 480], "resize": "preserve-aspect-letterbox",
                       "ethernet_protocol": "EVF1 RGB565LE 600 packets; unchanged FPGA format"},
            "options": options, "ov5640_sync": "manual_unverified",
            "sync_scope": "source timeline; physical receiver/scan-out synchronization unmeasured",
            "scene_relationship": "user must supply corresponding views from the same driving scene"}
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
