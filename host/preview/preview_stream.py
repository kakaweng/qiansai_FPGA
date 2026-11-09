import argparse
import os
from pathlib import Path
import socket
import sys
import threading
import time

from capture_stream import STREAM_HEADER, STREAM_MAGIC, STREAM_VERSION
from draw_detections import draw_boxes, draw_detections
from frame_to_png import FRAME_HEIGHT, FRAME_WIDTH, checksum_backend, frame_checksum
from frame_format import GRAY8, format_name, pixel_format, save_record_frame, to_bgr
from rknn_stream import (
    DETECTION_ENTRY, RESULT_FLAG_DETECTION_ONLY,
    RESULT_FLAG_MATCHED,
    RESULT_HEADER_BYTES, RESULT_HEADER_BYTES_SPLIT, RESULT_VERSION, RESULT_VERSION_SPLIT,
    RESULT_VERSION_COLOR, RESULT_HEADER_BYTES_COLOR,
    decode_detections, decode_extension,
)


def receive_exact(connection, byte_count):
    data = bytearray(byte_count)
    received = 0
    while received < byte_count:
        count = connection.recv_into(memoryview(data)[received:])
        if not count:
            raise ConnectionError(f"sender closed after {received}/{byte_count} bytes")
        received += count
    return bytes(data)


def receive_record(connection, timing=None):
    started_ns = time.perf_counter_ns() if timing is not None else 0
    header = receive_exact(connection, STREAM_HEADER.size)
    magic, version, header_bytes, width, height, pixel_bytes, checksum, sequence, timestamp_ns = STREAM_HEADER.unpack(header)
    if magic != STREAM_MAGIC or version not in (STREAM_VERSION, RESULT_VERSION, RESULT_VERSION_SPLIT, RESULT_VERSION_COLOR):
        raise RuntimeError(f"invalid frame header: magic=0x{magic:08x}, version={version}")
    expected_header = {STREAM_VERSION: STREAM_HEADER.size, RESULT_VERSION: RESULT_HEADER_BYTES,
                       RESULT_VERSION_SPLIT: RESULT_HEADER_BYTES_SPLIT,
                       RESULT_VERSION_COLOR: RESULT_HEADER_BYTES_COLOR}[version]
    if header_bytes != expected_header or width != FRAME_WIDTH or height != FRAME_HEIGHT:
        raise RuntimeError("frame dimensions or header size are invalid")
    count = 0
    result_info = None
    flags = 0
    if version != STREAM_VERSION:
        extension_size = header_bytes - STREAM_HEADER.size
        count, result_info = decode_extension(receive_exact(connection, extension_size), timestamp_ns)
        flags = result_info.get("flags", 0)
    if flags not in (0, RESULT_FLAG_DETECTION_ONLY, RESULT_FLAG_MATCHED):
        raise RuntimeError(f"unsupported result flags: {flags}")
    if version in (RESULT_VERSION_SPLIT, RESULT_VERSION_COLOR) and not flags and count:
        raise RuntimeError("split raw record unexpectedly contains detections")
    header_ns = time.perf_counter_ns() if timing is not None else 0
    if flags & RESULT_FLAG_DETECTION_ONLY:
        if pixel_bytes:
            raise RuntimeError("detection-only record contains pixels")
        pixels = None
    else:
        try:
            actual_format = pixel_format(pixel_bytes)
        except ValueError as error:
            raise RuntimeError(str(error)) from error
        expected_format = (result_info["pixel_format"] if version == RESULT_VERSION_COLOR else
                           actual_format if version == STREAM_VERSION else GRAY8)
        if actual_format != expected_format:
            raise RuntimeError("pixel format does not match the frame byte count")
        pixels = receive_exact(connection, pixel_bytes)
    if version != STREAM_VERSION:
        entries = receive_exact(connection, count * DETECTION_ENTRY.size)
        result_info["detections"] = decode_detections(entries, width, height)
    pixels_ns = time.perf_counter_ns() if timing is not None else 0
    if pixels is not None and frame_checksum(pixels) != checksum:
        raise RuntimeError(f"frame {sequence} checksum mismatch: header=0x{checksum:08x}")
    if timing is not None:
        checked_ns = time.perf_counter_ns()
        timing.update(header_us=(header_ns - started_ns) // 1000, pixels_us=(pixels_ns - header_ns) // 1000,
                      checksum_us=(checked_ns - pixels_ns) // 1000, total_us=(checked_ns - started_ns) // 1000)
    kind = "detection" if flags & RESULT_FLAG_DETECTION_ONLY else (
        "combined" if version == RESULT_VERSION or flags == RESULT_FLAG_MATCHED else "raw"
    )
    return {"kind": kind,
            "sequence": sequence, "timestamp_ns": timestamp_ns, "checksum": checksum,
            "pixels": pixels, "inference": result_info if kind != "raw" else None,
            "rknn_service": version != STREAM_VERSION}


def box_iou(first, second):
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = first_area + second_area - intersection
    return intersection / union if union > 0.0 else 0.0


def update_tracks(tracks, inference):
    detections = inference["inference"]["detections"]
    timestamp_ns = inference["timestamp_ns"]
    matched = set()
    for track in tracks:
        best_index = None
        best_iou = 0.0
        for index, detection in enumerate(detections):
            if index in matched or detection["class_id"] != track["class_id"]:
                continue
            overlap = box_iou(track["box"], detection["box"])
            if overlap > best_iou:
                best_iou = overlap
                best_index = index
        if best_index is None or best_iou < 0.15:
            track["misses"] += 1
            continue
        detection = detections[best_index]
        old_box = track["box"]
        new_box = tuple(float(value) for value in detection["box"])
        elapsed = max((timestamp_ns - track["timestamp_ns"]) / 1e9, 0.001)
        measured_velocity = tuple((new_box[index] - old_box[index]) / elapsed for index in range(4))
        track["velocity"] = tuple(0.5 * track["velocity"][index] + 0.5 * measured_velocity[index]
                                   for index in range(4))
        track["box"] = new_box
        track["score"] = detection["score"]
        track["label"] = detection["label"]
        track["timestamp_ns"] = timestamp_ns
        track["sequence"] = inference["sequence"]
        track["misses"] = 0
        matched.add(best_index)
    tracks[:] = [track for track in tracks if track["misses"] <= 3]
    for index, detection in enumerate(detections):
        if index in matched:
            continue
        tracks.append({
            "class_id": detection["class_id"],
            "label": detection["label"],
            "score": detection["score"],
            "box": tuple(float(value) for value in detection["box"]),
            "velocity": (0.0, 0.0, 0.0, 0.0),
            "timestamp_ns": timestamp_ns,
            "sequence": inference["sequence"],
            "misses": 0,
        })


def predict_tracks(tracks, timestamp_ns, max_age_ms):
    detections = []
    max_age_ns = max_age_ms * 1000000.0
    for track in tracks:
        age_ns = max(0.0, timestamp_ns - track["timestamp_ns"])
        if age_ns > max_age_ns:
            continue
        elapsed = age_ns / 1e9
        box = tuple(track["box"][index] + track["velocity"][index] * elapsed for index in range(4))
        detections.append({
            "class_id": track["class_id"],
            "label": track["label"],
            "score": track["score"],
            "box": box,
            "tracked": age_ns > 0.0,
            "age_ms": age_ns / 1e6,
        })
    return detections


class PreviewReceiver:
    def __init__(self, host, port, count, reconnect=True, reconnect_delay=2.0, profile=False, require_detections=False):
        self.host, self.port, self.count = host, port, count
        self.reconnect, self.reconnect_delay = reconnect, reconnect_delay
        self.profile, self.require_detections = profile, require_detections
        self.connection = None
        self.lock = threading.Lock()
        self.stop, self.done = threading.Event(), threading.Event()
        self.latest_raw = self.latest_inference = None
        self.raw_received = self.detection_received = self.raw_gaps = self.reconnections = 0
        self.raw_fps = self.detection_fps = self.receive_fps = 0.0
        self.status, self.connected, self.error = "Connecting", False, None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        try:
            while not self.stop.is_set():
                connection = None
                try:
                    with self.lock:
                        self.status = "Connecting"
                    connection = socket.create_connection((self.host, self.port), timeout=5)
                    connection.settimeout(30)
                    with self.lock:
                        self.connection, self.connected, self.status = connection, True, "Live"
                        self.latest_raw = self.latest_inference = None
                        self.raw_fps = self.detection_fps = self.receive_fps = 0.0
                    print(f"Connected {self.host}:{self.port}; checksum={checksum_backend()}", flush=True)
                    previous_raw_seq = previous_raw_ts = previous_raw_ns = previous_detection_ns = None
                    while not self.stop.is_set():
                        timing = {} if self.profile else None
                        record = receive_record(connection, timing)
                        if self.require_detections and not record["rknn_service"]:
                            raise RuntimeError("raw preview service connected; use pcie_rknn_probe --serve")
                        received_ns = time.perf_counter_ns()
                        kind, inference = record["kind"], record["inference"]
                        if kind in ("raw", "combined"):
                            sequence = record["sequence"]
                            if previous_raw_seq is not None and sequence <= previous_raw_seq:
                                raise RuntimeError(f"raw sequence did not increase: {previous_raw_seq}->{sequence}")
                            if previous_raw_ts is not None and record["timestamp_ns"] <= previous_raw_ts:
                                raise RuntimeError("frame timestamp did not increase")
                        with self.lock:
                            if kind in ("raw", "combined"):
                                if previous_raw_seq is not None:
                                    self.raw_gaps += sequence - previous_raw_seq - 1
                                self.raw_received += 1
                                if previous_raw_ts is not None:
                                    self.raw_fps = 1e9 / (record["timestamp_ns"] - previous_raw_ts)
                                if previous_raw_ns is not None:
                                    self.receive_fps = 1e9 / (received_ns - previous_raw_ns)
                                self.latest_raw = (record, time.monotonic())
                            if kind in ("combined", "detection"):
                                self.latest_inference = record
                                self.detection_received += 1
                                if previous_detection_ns is not None:
                                    self.detection_fps = 1e9 / (received_ns - previous_detection_ns)
                        if kind in ("raw", "combined"):
                            previous_raw_seq, previous_raw_ts, previous_raw_ns = sequence, record["timestamp_ns"], received_ns
                        if kind in ("combined", "detection"):
                            previous_detection_ns = received_ns
                            if self.profile:
                                targets = inference["detections"]
                                max_score = max((target["score"] for target in targets), default=0.0)
                                inference_ms = sum(inference[name] for name in (
                                    "preprocess_us", "inputs_us", "run_us", "outputs_us", "postprocess_us"
                                )) / 1000
                                print(f"Detection result: seq={record['sequence']} targets={len(targets)} "
                                      f"max_score={max_score:.4f} infer_ms={inference_ms:.3f}", flush=True)
                        if self.profile and timing and (record["sequence"] <= 3 or record["sequence"] % 10 == 0):
                            print(f"Preview timing: kind={kind} seq={record['sequence']} "
                                  f"header_us={timing['header_us']} pixels_us={timing['pixels_us']} "
                                  f"checksum_us={timing['checksum_us']} total_us={timing['total_us']}", flush=True)
                        if self.count and self.raw_received >= self.count:
                            return
                except (OSError, RuntimeError, ConnectionError) as error:
                    if self.stop.is_set():
                        return
                    if not self.reconnect:
                        raise
                    with self.lock:
                        self.connected, self.status = False, "Disconnected; reconnecting"
                        self.reconnections += 1
                    print(f"Connection lost: {error}; retrying in {self.reconnect_delay:g}s", flush=True)
                finally:
                    with self.lock:
                        self.connection, self.connected = None, False
                    if connection is not None:
                        connection.close()
                if self.stop.wait(self.reconnect_delay):
                    return
        except (OSError, RuntimeError, ConnectionError) as error:
            if not self.stop.is_set():
                self.error = str(error)
        finally:
            self.done.set()

    def close(self):
        self.stop.set()
        with self.lock:
            connection = self.connection
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.thread.join(timeout=6)


def display_preview(receiver, arguments, cv2, numpy):
    title = "FPGA PCIe HDMI Live Preview"
    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(title, arguments.width, arguments.width * FRAME_HEIGHT // FRAME_WIDTH)
    waiting = numpy.zeros((540, 960), dtype=numpy.uint8)
    cv2.putText(waiting, "Waiting for a PCIe HDMI frame...", (25, 270), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 255, 2)
    cv2.imshow(title, waiting)
    displayed, previous_display_ns, display_fps = None, None, 0.0
    shown_frames = 0
    render_state = None
    displayed_detections = []
    tracks = []
    last_inference = None
    last_reconnections = 0
    traffic_monitor = None
    lane_monitor = None
    scene_path = arguments.road_scene or arguments.traffic_scene
    if scene_path:
        from traffic_light import TrafficLightMonitor, draw_traffic_light, load_scene
        scene = load_scene(scene_path)
        traffic_monitor = TrafficLightMonitor(scene, cv2, numpy,
                                              arguments.traffic_roi_fallback)
        if arguments.road_scene:
            from lane_detection import LaneMonitor, draw_lane
            lane_monitor = LaneMonitor(scene, cv2, numpy)
    traffic_result = None
    lane_result = None
    decision_adapter = None
    decision_result = None
    if arguments.decision_config:
        from decision_preview import DecisionPreviewAdapter, draw_decision
        decision_adapter = DecisionPreviewAdapter(arguments.decision_options, arguments.decision_log)
        decision_result = decision_adapter.update(None, None, None, False, 0)
        waiting = numpy.repeat(waiting[:, :, None], 3, axis=2)
        draw_decision(waiting, decision_result, cv2, header_y=300)
        cv2.imshow(title, waiting)
    print("Live window created; Q/Esc quits, S saves a screenshot.", flush=True)
    while True:
        with receiver.lock:
            raw, inference = receiver.latest_raw, receiver.latest_inference
            status, connected = receiver.status, receiver.connected
            raw_fps, receive_fps, detection_fps = receiver.raw_fps, receiver.receive_fps, receiver.detection_fps
            detection_received, gaps, reconnections = receiver.detection_received, receiver.raw_gaps, receiver.reconnections
        if reconnections != last_reconnections:
            tracks.clear()
            last_inference = None
            previous_display_ns = None
            last_reconnections = reconnections
            if traffic_monitor is not None:
                traffic_monitor.reset()
                traffic_result = {"state": "unknown", "action": "WAIT", "source": "reconnecting", "candidates": []}
            if lane_monitor is not None:
                lane_monitor.reset()
                lane_result = {"state": "unknown", "lane_count": None, "ego_lane": None,
                               "lane_position": "unknown", "lateral_position": "unknown",
                               "lane_change": "unknown", "lane_change_event_seq": 0,
                               "lane_change_timestamp_ns": None, "alert": "none",
                               "confidence": 0.0, "boundaries": [], "reason": "reconnecting"}
        new_frame = raw is not None and raw is not displayed
        if new_frame:
            displayed = raw
            shown_frames += 1
            now_ns = time.perf_counter_ns()
            if previous_display_ns is not None:
                display_fps = 1e9 / (now_ns - previous_display_ns)
            previous_display_ns = now_ns
        matched = displayed is not None and displayed[0]["kind"] == "combined"
        if matched:
            inference = displayed[0]
            tracks.clear()
            last_inference = None
        elif inference is not None and inference is not last_inference:
            update_tracks(tracks, inference)
            last_inference = inference
        traffic_fresh = connected and displayed is not None and (
            traffic_monitor is None or time.monotonic() - displayed[1] <=
            traffic_monitor.scene.get("evidence_timeout_ms", 500) / 1000)
        decision_fresh = (decision_adapter is not None and connected and matched and
                          time.monotonic() - displayed[1] <= decision_adapter.input_timeout_ns / 1e9)
        current_render_state = (id(displayed), id(inference), status, connected, reconnections,
                                traffic_fresh, decision_fresh)
        if displayed is not None and current_render_state != render_state:
            render_started_ns = time.perf_counter_ns()
            record, _ = displayed
            image = to_bgr(record["pixels"], numpy)
            if traffic_monitor is not None:
                if new_frame and traffic_fresh:
                    if not matched or pixel_format(len(record["pixels"])) == GRAY8:
                        raise RuntimeError("road perception requires matched RGB565LE frames")
                    traffic_result = traffic_monitor.update(image, inference["inference"]["detections"], record["timestamp_ns"])
                    if traffic_result["changed"] or arguments.profile:
                        print(f"Traffic signal: seq={record['sequence']} state={traffic_result['state']} "
                              f"raw={traffic_result['raw_state']} action={traffic_result['action']} "
                              f"source={traffic_result['source']} reason={traffic_result['reason']}", flush=True)
                    if lane_monitor is not None:
                        lane_result = lane_monitor.update(image, inference["inference"]["detections"], record["timestamp_ns"])
                        if lane_result["changed"] or arguments.profile:
                            print(f"Lane perception: seq={record['sequence']} state={lane_result['state']} "
                                  f"current_lane={lane_result['ego_lane']} position={lane_result.get('lane_position', 'unknown')} "
                                  f"lateral={lane_result.get('lateral_position', 'unknown')} "
                                  f"change={lane_result.get('lane_change', 'unknown')} "
                                  f"change_confidence={lane_result.get('lane_change_confidence', 0):.2f} "
                                  f"change_event={lane_result.get('lane_change_event_seq', 0)} "
                                  f"change_reason={lane_result.get('lane_change_reason', 'not_available')} "
                                  f"confidence={lane_result['confidence']:.2f} reason={lane_result['reason']}", flush=True)
                if not traffic_fresh:
                    traffic_monitor.reset()
                    traffic_result = {"state": "unknown", "action": "WAIT", "source": "stale", "candidates": []}
                    if lane_monitor is not None:
                        lane_monitor.reset()
                        lane_result = {"state": "unknown", "lane_count": None, "ego_lane": None,
                                       "lane_position": "unknown", "lateral_position": "unknown",
                                       "lane_change": "unknown", "lane_change_event_seq": 0,
                                       "lane_change_timestamp_ns": None, "alert": "none",
                                       "confidence": 0.0, "boundaries": [], "reason": "stale"}
            if decision_adapter is not None and (new_frame or not decision_fresh or
                                                  decision_adapter.session_id != str(reconnections)):
                decision_result = decision_adapter.update(record, traffic_result, lane_result,
                                                           decision_fresh, reconnections)
                if decision_result["changed"] or arguments.profile:
                    print(f"Driving decision: seq={record['sequence']} state={decision_result['state']} "
                          f"action={decision_result['recommended_action']} "
                          f"reasons={decision_result['reasons']} alarms={decision_result['alarm_codes']}", flush=True)
            image = cv2.resize(image, (arguments.width, arguments.width * FRAME_HEIGHT // FRAME_WIDTH))
            displayed_detections = (inference["inference"]["detections"] if matched else
                                    predict_tracks(tracks, record["timestamp_ns"], arguments.track_max_age_ms))
            if displayed_detections:
                draw_boxes(image, displayed_detections, cv2, scale=arguments.width / FRAME_WIDTH)
            if lane_result is not None:
                draw_lane(image, lane_result, cv2, scale=arguments.width / FRAME_WIDTH)
            if traffic_result is not None:
                draw_traffic_light(image, traffic_result, cv2, scale=arguments.width / FRAME_WIDTH)
            if decision_result is not None:
                draw_decision(image, decision_result, cv2)
            detection_seq = inference["sequence"] if inference else None
            detection_delta = (record["timestamp_ns"] - inference["timestamp_ns"]) / 1e6 if inference else None
            source_metric = (f"Source interval {1000 / raw_fps:.1f} ms" if raw_fps else "Source interval --") if matched else f"Raw {raw_fps:.2f} fps"
            overlay = [f"{format_name(record['pixels'])} | Mode {'matched' if matched else 'async/raw'} | {source_metric}",
                       f"Receive {receive_fps:.2f} fps | Display {display_fps:.2f} fps",
                       f"Detection {detection_fps:.2f} fps | image seq={record['sequence']} detection seq={detection_seq} delta={detection_delta:.1f} ms" if inference else
                       f"Detection {detection_fps:.2f} fps | image seq={record['sequence']} detection seq=none",
                       f"Boxes {len(displayed_detections)} | detections {detection_received} | skipped source sequences {gaps}",
                       f"{status} | reconnects {reconnections} | Q/Esc quit | S screenshot"]
            if inference:
                info = inference["inference"]
                infer_ms = sum(info[name] for name in ("preprocess_us", "inputs_us", "run_us", "outputs_us", "postprocess_us")) / 1000
                overlay.append(f"Targets {len(info['detections'])} | infer {infer_ms:.1f} ms | NPU {info['run_us'] / 1000:.1f} ms")
            toolbar = numpy.zeros((len(overlay) * 25 + 10, arguments.width, 3), dtype=numpy.uint8)
            for index, text in enumerate(overlay):
                cv2.putText(toolbar, text, (10, 23 + index * 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255) if connected else (0, 0, 255), 1)
            cv2.imshow(title, numpy.vstack((toolbar, image)))
            render_state = current_render_state
            if arguments.profile and new_frame and (shown_frames <= 3 or shown_frames % 10 == 0):
                print(f"Display timing: mode={'matched' if matched else 'async/raw'} seq={record['sequence']} "
                      f"render_us={(time.perf_counter_ns() - render_started_ns) // 1000} "
                      f"display_fps={display_fps:.2f}", flush=True)
        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord("q")):
            break
        if key == ord("s") and displayed is not None:
            arguments.snapshots.mkdir(parents=True, exist_ok=True)
            record, _ = displayed
            prefix = arguments.snapshots / f"frame_{record['sequence']}_{time.time_ns()}"
            save_record_frame(prefix, record["pixels"])
            original = to_bgr(record["pixels"], numpy)
            cv2.imwrite(str(prefix.with_suffix(".png")), original)
            if record["inference"] is not None or displayed_detections:
                cv2.imwrite(str(prefix.with_name(prefix.name + "_detections.png")), draw_detections(record["pixels"], record["sequence"], displayed_detections, cv2, numpy))
            if traffic_result is not None:
                cv2.imwrite(str(prefix.with_name(prefix.name + "_traffic.png")),
                            draw_traffic_light(original.copy(), traffic_result, cv2))
            if lane_result is not None:
                road_image = original.copy()
                if displayed_detections:
                    draw_boxes(road_image, displayed_detections, cv2)
                draw_lane(road_image, lane_result, cv2)
                draw_traffic_light(road_image, traffic_result, cv2)
                cv2.imwrite(str(prefix.with_name(prefix.name + "_road.png")), road_image)
            if decision_result is not None:
                decision_image = original.copy()
                if displayed_detections:
                    draw_boxes(decision_image, displayed_detections, cv2)
                if lane_result is not None:
                    draw_lane(decision_image, lane_result, cv2)
                if traffic_result is not None:
                    draw_traffic_light(decision_image, traffic_result, cv2)
                draw_decision(decision_image, decision_result, cv2, header_y=150)
                cv2.imwrite(str(prefix.with_name(prefix.name + "_decision.png")), decision_image)
            print(f"Screenshot saved: {prefix.with_suffix('.png')}", flush=True)
        if receiver.done.is_set():
            break


def main():
    parser = argparse.ArgumentParser(description="Display PCIe HDMI frames with matched or asynchronous RKNN detections")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--snapshots", type=Path, default=Path("preview_snapshots"))
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--no-reconnect", action="store_true")
    parser.add_argument("--reconnect-delay", type=float, default=2.0)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--require-detections", action="store_true")
    parser.add_argument("--decision-config", type=Path,
                        help="enable the behavior state machine using explicit scene observations")
    parser.add_argument("--decision-log", type=Path, help="append observed inputs and decisions as JSONL")
    scene_options = parser.add_mutually_exclusive_group()
    scene_options.add_argument("--traffic-scene", type=Path, help="enable red/green detection using a scene search region")
    scene_options.add_argument("--road-scene", type=Path, help="enable image-based lane and traffic-light overlays")
    parser.add_argument("--traffic-roi-fallback", action="store_true",
                        help="allow color evidence in the scene ROI when YOLO signal boxes are missing")
    parser.add_argument("--track-max-age-ms", type=float, default=350.0,
                        help="async mode only: keep and predict a box between detections (default: 350 ms)")
    arguments = parser.parse_args()
    if (not 1 <= arguments.port <= 65535 or not 320 <= arguments.width <= 3840 or
            arguments.count < 0 or not 0 <= arguments.track_max_age_ms <= 3000):
        parser.error("invalid port, width, or count")
    if arguments.traffic_roi_fallback and not (arguments.traffic_scene or arguments.road_scene):
        parser.error("--traffic-roi-fallback requires --traffic-scene or --road-scene")
    if (arguments.traffic_scene or arguments.road_scene) and arguments.headless:
        parser.error("road perception requires the preview window")
    if arguments.decision_log and not arguments.decision_config:
        parser.error("--decision-log requires --decision-config")
    if arguments.decision_config:
        if arguments.headless:
            parser.error("decision preview requires the preview window")
        from decision_preview import load_decision_config
        try:
            arguments.decision_options = load_decision_config(arguments.decision_config)
        except (OSError, ValueError) as error:
            parser.error(str(error))
        arguments.require_detections = True
    if arguments.road_scene:
        arguments.require_detections = True
    if not arguments.headless and sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        parser.error("no graphical desktop; use an Ubuntu desktop terminal or --headless")
    cv2 = numpy = None
    if not arguments.headless:
        try:
            import cv2
            import numpy
        except ImportError:
            parser.error("install python3-opencv and python3-numpy")
    receiver = PreviewReceiver(arguments.host, arguments.port, arguments.count,
                               reconnect=not arguments.no_reconnect and not arguments.headless,
                               reconnect_delay=arguments.reconnect_delay, profile=arguments.profile,
                               require_detections=arguments.require_detections)
    receiver.thread.start()
    try:
        if arguments.headless:
            while not receiver.done.wait(0.2):
                pass
        else:
            display_preview(receiver, arguments, cv2, numpy)
    except (ImportError, OSError, RuntimeError, ValueError, KeyError) as error:
        print(f"Preview failed: {error}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        pass
    finally:
        receiver.close()
        if cv2 is not None:
            cv2.destroyAllWindows()
    if receiver.error:
        print(f"Preview failed: {receiver.error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
