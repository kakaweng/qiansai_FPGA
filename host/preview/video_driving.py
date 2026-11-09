import argparse
import hashlib
import json
import math
import os
import sys
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ACTION_NAMES = {"STOP": "停车 / 礼让", "WAIT": "等待有效判断", "GO": "保持车道通行",
                "CHANGE_LEFT": "向左变道", "CHANGE_RIGHT": "向右变道",
                "TURN_LEFT": "左转", "TURN_RIGHT": "右转"}
STATE_NAMES = {"RED_STOP": "红灯停车", "YIELD_PEDESTRIAN": "礼让冲突区域内行人",
               "GREEN_GO": "绿灯且路径条件满足", "ROAD_GO": "当前道路条件允许继续",
               "WAIT_INPUT": "视频输入失效", "WAIT_SIGNAL": "信号不明确",
               "WAIT_PEDESTRIAN": "行人风险待确认", "WAIT_LANE": "本车区域待确认",
               "WAIT_CONFIRM": "正在连续确认", "KEEP_LANE": "拒绝变道请求",
               "WAIT_TURN": "转弯许可待确认", "YELLOW_WAIT": "黄灯等待"}
VALUE_NAMES = {"red": "红灯", "green": "绿灯", "yellow": "黄灯", "unknown": "未知",
               "solid": "实线", "dashed": "虚线", "left": "左", "right": "右", "center": "中",
               "none": "无", "estimated": "几何估计", "present": "有冲突", "clear": "未见冲突",
               "allowed": "条件满足", "forbidden": "禁止",
               "keep_lane": "保持当前车道", "change_left": "请求左变道", "change_right": "请求右变道",
               "turn_left": "请求左转", "turn_right": "请求右转"}
REASON_NAMES = {"red_signal": "本车相关信号为红灯", "signal_unknown": "信号颜色未知或已丢失",
                "signal_not_bound": "尚未确认本车信号关联", "pedestrian_conflict": "行人进入冲突区域",
                "pedestrian_clearance_pending": "等待连续确认行人风险解除", "pedestrian_unknown": "行人风险区域未配置",
                "lane_unknown": "当前车道区域证据不足", "go_confirmation_pending": "通行条件连续确认中",
                "green_signal_and_clear_path": "绿灯、行人冲突已解除、车道有效",
                "uncontrolled_road_clear_path": "按起步道路配置判断路径条件满足",
                "signal_direction_mismatch": "信号方向与驾驶请求不一致", "yellow_signal": "黄灯",
                "left_solid_boundary": "左侧实线禁止跨越", "right_solid_boundary": "右侧实线禁止跨越",
                "stale_input": "输入已暂停或过期", "duplicate_frame": "没有新分析帧"}
OBJECT_NAMES = {0: "行人", 1: "自行车", 2: "汽车", 3: "摩托车", 5: "公交", 7: "卡车", 9: "交通灯"}


def choose_video():
    import tkinter
    from tkinter import filedialog
    window = tkinter.Tk()
    window.withdraw()
    try:
        selected = filedialog.askopenfilename(title="选择要自动识别和判断的行车视频",
                                              filetypes=[("视频", "*.mp4 *.avi *.mov *.mkv"), ("所有文件", "*.*")])
    finally:
        window.destroy()
    return Path(selected) if selected else None


def reject_timeline(value):
    if isinstance(value, dict):
        if "evaluation_only" in value:
            raise ValueError("video decision config cannot contain annotation timelines")
        for child in value.values():
            reject_timeline(child)
    elif isinstance(value, list):
        for child in value:
            reject_timeline(child)


def load_configuration(path):
    config = json.loads(path.read_text(encoding="utf-8"))
    reject_timeline(config)
    scene = config["perception"]
    region = scene["search_region"]
    if (not isinstance(region, list) or len(region) != 4 or
            not all(isinstance(value, (int, float)) and math.isfinite(value) for value in region) or
            not 0 <= region[0] < region[2] <= 1 or not 0 <= region[1] < region[3] <= 1):
        raise ValueError("perception.search_region must be normalized left, top, right, bottom")
    behavior = dict(config.get("behavior", {}))
    if behavior.get("mode", "advisory_only") != "advisory_only":
        raise ValueError("Windows video behavior supports advisory_only mode")
    return scene, behavior


class VideoDrivingPipeline:
    def __init__(self, detector, scene, behavior, cv2, numpy, backend="auto", threads=4,
                 lane_source="finetuned", lane_model=None):
        from lane_boundary_type import BoundaryTypeMonitor
        from lane_detection import LaneMonitor
        from video_scene_analysis import ObjectMotionMonitor, PedestrianRiskMonitor, AdjacentTrafficMonitor, DirectionalSignalMonitor, RoadContextMonitor
        from video_behavior import VideoBehaviorMonitor
        from windows_lane_segmenter import WindowsLaneSegmenter
        self.detector, self.scene, self.behavior = detector, scene, behavior
        self.cv2, self.numpy = cv2, numpy
        self.lane_source = lane_source
        if lane_source == "finetuned":
            from windows_lane_style import LaneStyleMonitor, WindowsLaneStyleSegmenter
            self.lanes = LaneStyleMonitor(scene, cv2, numpy)
            self.lane_segmenter = WindowsLaneStyleSegmenter(lane_model, backend=backend, threads=threads)
            self.boundaries = None
        elif lane_source == "yolop":
            self.lanes = LaneMonitor(scene, cv2, numpy)
            self.lane_segmenter = WindowsLaneSegmenter(lane_model, backend=backend, threads=threads)
            self.boundaries = BoundaryTypeMonitor(cv2, numpy, require_corridor=False, hold_ms=800)
        else:
            raise ValueError("lane_source must be finetuned or yolop")
        self.signals = DirectionalSignalMonitor(scene, cv2, numpy)
        self.motion = ObjectMotionMonitor()
        self.pedestrians = PedestrianRiskMonitor()
        self.adjacent = AdjacentTrafficMonitor()
        self.road = RoadContextMonitor(cv2, numpy)
        self.behavior_monitor = VideoBehaviorMonitor()
        self.reset()

    def reset(self):
        self.session_id = "local-video-" + uuid.uuid4().hex[:12]
        self.lanes.reset()
        self.signals.reset()
        if self.boundaries is not None:
            self.boundaries.reset()
        self.motion.reset()
        self.pedestrians.reset()
        self.road.reset()
        self.behavior_monitor.reset()

    def process(self, image, frame_seq, timestamp_ns):
        started = time.perf_counter()
        detections = [detection for detection in self.detector.detect(image)
                      if detection["class_id"] in OBJECT_NAMES]
        detector_ms = (time.perf_counter() - started) * 1000
        segmentation = self.lane_segmenter.segment(image)
        if self.lane_source == "finetuned":
            lane = self.lanes.update(image, detections, timestamp_ns, segmentation)
        else:
            lane = self.lanes.update(image, detections, timestamp_ns, segmentation=segmentation["lane_mask"])
            lane = self.boundaries.update(image, lane, detections, timestamp_ns, self.lanes.camera_center)
        signal = self.signals.update(image, detections, timestamp_ns)
        height, width = image.shape[:2]
        objects = self.motion.update(detections, lane, timestamp_ns, width, height)
        pedestrian = self.pedestrians.update(objects, lane, timestamp_ns, width, height)
        adjacent = self.adjacent.update(objects, lane, width, height)
        road = self.road.update(image, lane, detections, timestamp_ns)
        decision = self.behavior_monitor.update(pedestrian, signal, road, lane, adjacent, frame_seq, timestamp_ns, self.session_id)
        observation = {"pedestrian": pedestrian, "signal": signal, "lane": lane, "road": road,
                       "frame_seq": frame_seq, "timestamp_ns": timestamp_ns, "request": {"maneuver": "keep_lane"},
                       "input_sources": decision["input_sources"]}
        return {"video_seconds": timestamp_ns / 1e9, "detections": detections, "lane": lane,
                "signal": signal, "observation": observation, "decision": decision, "objects": objects,
                "adjacent": adjacent, "road": road,
                "lane_model": {key: value for key, value in segmentation.items() if not key.endswith("_mask")},
                "detector_ms": round(detector_ms, 2),
                "processing_ms": round((time.perf_counter() - started) * 1000, 2)}


class VideoRenderer:
    def __init__(self, cv2, numpy, width):
        self.cv2, self.numpy, self.width = cv2, numpy, width
        self.fonts = None
        try:
            from PIL import Image, ImageDraw, ImageFont
            font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc"
            if font_path.is_file():
                self.pillow = Image, ImageDraw
                font_scale = min(1.0, width / 960)
                self.fonts = (ImageFont.truetype(str(font_path), max(17, round(24 * font_scale))),
                              ImageFont.truetype(str(font_path), max(13, round(17 * font_scale))))
        except ImportError:
            pass

    def render(self, image, result, paused=False):
        from draw_detections import draw_boxes
        from lane_detection import draw_lane
        from traffic_light import draw_traffic_light
        cv2, numpy = self.cv2, self.numpy
        height = round(image.shape[0] * self.width / image.shape[1])
        picture = cv2.resize(image, (self.width, height))
        scale = self.width / image.shape[1]
        if result["lane"].get("style_regions"):
            from windows_lane_style import draw_style_regions
            draw_style_regions(picture, result["lane"]["style_regions"], cv2, numpy, scale)
        draw_boxes(picture, result["detections"], cv2, scale=scale)
        draw_lane(picture, result["lane"], cv2, scale=scale, header_y=None)
        if result["lane"].get("boundary_curves"):
            from windows_lane_style import draw_style_boundaries
            draw_style_boundaries(picture, result["lane"], cv2, numpy, scale)
        draw_traffic_light(picture, result["signal"], cv2, scale=scale, header_y=None)
        for evidence in result["observation"]["pedestrian"]["evidence"]:
            if evidence["risk"] != "none":
                left, top, right, bottom = [round(value * scale) for value in evidence["box"]]
                color = (0, 0, 255) if evidence["risk"] == "stop" else (0, 170, 255)
                cv2.rectangle(picture, (left, top), (right, bottom), color, 3)
        canvas = numpy.full((height + 390, self.width, 3), (28, 28, 28), dtype=numpy.uint8)
        canvas[:height] = picture
        decision = result["decision"]
        counts = Counter(detection["class_id"] for detection in result["detections"])
        objects = "  ".join(f"{name}:{counts[identifier]}" for identifier, name in OBJECT_NAMES.items() if counts[identifier]) or "未检测到相关目标"
        lane = result["lane"]
        action = decision["recommended_action"]
        directions = result["signal"]["directions"]
        direction_names = {"straight": "直行", "left": "左转", "right": "右转"}
        signal_text = "  ".join(f"{name}：{VALUE_NAMES.get(directions[direction]['state'], '?')}{'（暂存）' if directions[direction]['retained'] else ''}" for direction, name in direction_names.items())
        risk_names = {"none": "未见路径冲突", "watch": "注意观察", "slow": "减速准备礼让", "stop": "停车礼让"}
        request = decision.get("lane_request")
        boundary_labels = {}
        for side in ("left", "right"):
            evidence = lane.get("boundary_type_evidence", {}).get(side, {})
            label = VALUE_NAMES.get(lane.get(f"{side}_boundary_type"), "未知")
            if evidence.get("held"):
                label += f"（暂存 {evidence.get('age_ms', 0):.0f}ms）"
            elif lane.get(f"{side}_boundary_type") == "unknown":
                reason_names = {"no_current_boundary": "未定位边界", "boundary_occluded": "遮挡",
                                "insufficient_visible_marking": "标线不足", "mixed_model_styles": "线型混合",
                                "conflicting_boundary_styles": "相邻线型冲突",
                                "model_style_uncertain": "模型证据不足"}
                detail = ("确认中" if evidence.get("observed_type") in ("solid", "dashed")
                          else reason_names.get(evidence.get("reason"), "待确认"))
                label += f"（{detail}）"
            if evidence.get("confidence") is not None:
                label += f" 分数{evidence['confidence']:.2f}"
            boundary_labels[side] = label
        lane_model = result.get("lane_model", {})
        model_text = (f"车道微调模型：第{lane_model['weights_epoch']}轮  推理{lane_model['inference_ms']:.0f}ms  |  橙：实线  蓝：虚线  紫：其他标线"
                      if lane_model.get("weights_epoch") is not None else "方向信号许可与转弯安全分开判断；仅分析可见前方区域")
        request_text = "变道判断：未发起请求（A 向左 / D 向右，每次按键判断一次）"
        if request:
            request_text = f"已收到{'左' if request['side'] == 'left' else '右'}变道请求 @ {request['timestamp_ns']/1e9:.2f}s：{request['message']}"
            if request.get("historical"):
                request_text += "（历史结果，重新判断请再按键）"
        rows = [
            (f"{'已暂停｜' if paused else ''}{decision['message']}", True),
            (f"视频 {result['video_seconds']:.2f}s  |  自动识别  |  分析耗时 {result['processing_ms']:.0f}ms  |  {objects}", False),
            (signal_text + f"  |  本车方向估计：{direction_names[decision['direction']]}", False),
            (f"左边界：{boundary_labels['left']}  右边界：{boundary_labels['right']}  |  行人：{risk_names[result['observation']['pedestrian']['risk']]}", False),
            (request_text, False),
            ("补充：" + ("；".join(decision["notes"]) or "无") , False),
            (model_text, False),
            ("空格 暂停/继续  ·  R 重播  ·  S 截图  ·  T 选信号区域", False),
            ("A/D 单次左/右变道判断  ·  K 清除请求  ·  Q 退出", False),
        ]
        if self.fonts:
            Image, ImageDraw = self.pillow
            rendered = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
            drawing = ImageDraw.Draw(rendered)
            text_y = height + 10
            for text, title in rows:
                color = (255, 95, 95) if title and action == "STOP" else (95, 235, 150) if title and action == "NORMAL" else (235, 210, 125) if title else (225, 225, 225)
                font = self.fonts[0 if title else 1]
                line = ""
                for character in text:
                    if line and drawing.textlength(line + character, font=font) > self.width - 28:
                        drawing.text((14, text_y), line, font=font, fill=color)
                        text_y += 26
                        line = ""
                    line += character
                drawing.text((14, text_y), line, font=font, fill=color)
                text_y += 34
            return cv2.cvtColor(numpy.array(rendered), cv2.COLOR_RGB2BGR)
        english = [f"Decision {decision['state']} | {action}",
                   f"Video {result['video_seconds']:.2f}s | Analysis {result['processing_ms']:.0f}ms",
                   f"Boundaries L={lane.get('left_boundary_type', 'unknown')} R={lane.get('right_boundary_type', 'unknown')}",
                   "Reason " + ", ".join(decision["reasons"]), "Alarm " + ", ".join(decision["alarm_codes"]),
                   "Space pause | R restart | S snapshot | T signal ROI",
                   "A/D lane-change request | K keep lane | Q quit"]
        for index, text in enumerate(english):
            cv2.putText(canvas, text, (14, height + 28 + index * 35), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (230, 230, 230), 1)
        return canvas


def select_region(image, title, cv2, display_width):
    scale = display_width / image.shape[1]
    preview = cv2.resize(image, (display_width, round(image.shape[0] * scale)))
    left, top, width, height = cv2.selectROI(title, preview, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow(title)
    if width <= 0 or height <= 0:
        return None
    return [left / preview.shape[1], top / preview.shape[0],
            (left + width) / preview.shape[1], (top + height) / preview.shape[0]]


def run_video(arguments, cv2, numpy):
    from windows_object_detector import WindowsObjectDetector
    scene, behavior = load_configuration(arguments.config)
    detector = WindowsObjectDetector(arguments.model, confidence=arguments.confidence, threads=arguments.threads,
                                     backend=arguments.backend)
    pipeline = VideoDrivingPipeline(detector, scene, behavior, cv2, numpy, arguments.backend, arguments.threads,
                                    arguments.lane_source, arguments.lane_model)
    print(f"Inference: objects={detector.runtime['backend']}; lanes={pipeline.lane_segmenter.runtime['backend']}", flush=True)
    print(f"Lane source: {pipeline.lane_source}; model: {pipeline.lane_segmenter.path.resolve()}", flush=True)
    if pipeline.lane_source == "finetuned":
        print(f"Lane version: {pipeline.lane_segmenter.manifest['model_version']}; best epoch: {pipeline.lane_segmenter.manifest['weights']['epoch']}", flush=True)
    if arguments.playback == "realtime":
        from video_realtime import run_realtime_video
        return run_realtime_video(arguments, pipeline, cv2, numpy)
    renderer = VideoRenderer(cv2, numpy, arguments.width)
    capture = cv2.VideoCapture(str(arguments.video))
    rate = capture.get(cv2.CAP_PROP_FPS)
    if not capture.isOpened() or not math.isfinite(rate) or rate <= 0:
        capture.release()
        raise ValueError(f"Cannot read video/frame rate: {arguments.video}")
    output = arguments.output_dir
    output.mkdir(parents=True, exist_ok=True)
    (output / "config.json").write_text(json.dumps({"perception": scene, "behavior": behavior},
                                                   ensure_ascii=False, indent=2), encoding="utf-8")
    capture.set(cv2.CAP_PROP_POS_FRAMES, round(arguments.start * rate))
    frame_index = round(arguments.start * rate)
    next_sample = frame_index / rate
    interval = 1 / min(rate, arguments.sample_fps)
    video_writer = None
    summary = {"video": str(arguments.video.resolve()), "config": str(arguments.config.resolve()),
               "model": str(arguments.model.resolve()), "mode": "local_video_observed_advisory",
               "lane_model": str(pipeline.lane_segmenter.path.resolve()),
               "lane_model_sha256": hashlib.sha256(pipeline.lane_segmenter.path.read_bytes()).hexdigest(),
               "lane_source": pipeline.lane_source,
               "lane_decoder_thresholds": dict(getattr(pipeline.lanes, "style_config", {})),
               "decision_version": pipeline.behavior_monitor.VERSION,
               "runtime": {"objects": detector.runtime, "lanes": pipeline.lane_segmenter.runtime},
               "code_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                               for name in ("video_driving.py", "video_behavior.py", "video_scene_analysis.py",
                                            "lane_detection.py", "lane_boundary_type.py", "windows_lane_segmenter.py", "windows_lane_style.py",
                                            "windows_object_detector.py", "windows_inference.py")},
               "sample_fps": min(rate, arguments.sample_fps), "samples": 0, "actions": Counter(),
               "signals": Counter(), "detected_classes": Counter(), "boundary_types": Counter(),
               "processing_ms_total": 0.0, "signal_context_note": "seen signals are retained; disappearance does not prove intersection clearance"}
    title = "Windows video driving - automatic perception and decisions"
    paused, stop = False, False
    try:
        if not arguments.headless:
            cv2.namedWindow(title, cv2.WINDOW_NORMAL)
        with (output / "observations.jsonl").open("w", encoding="utf-8") as log:
            while not stop:
                success, image = capture.read()
                if not success:
                    break
                seconds = frame_index / rate
                frame_index += 1
                if arguments.seconds and seconds >= arguments.start + arguments.seconds:
                    break
                if seconds + 1e-8 < next_sample:
                    continue
                next_sample += interval
                started = time.perf_counter()
                result = pipeline.process(image, frame_index, round(seconds * 1e9))
                summary["samples"] += 1
                summary["actions"][result["decision"]["recommended_action"]] += 1
                summary["signals"][result["signal"]["state"]] += 1
                summary["detected_classes"].update(detection["label"] for detection in result["detections"])
                summary["boundary_types"].update(result["lane"].get(f"{side}_boundary_type", "unknown") for side in ("left", "right"))
                summary["processing_ms_total"] += result["processing_ms"]
                log.write(json.dumps(result, ensure_ascii=False) + "\n")
                if result["decision"]["changed"]:
                    print(f"{seconds:6.2f}s  {result['decision']['state']}  {result['decision']['recommended_action']}  {result['decision']['reasons']}", flush=True)
                need_render = not arguments.headless or arguments.save_video or summary["samples"] == 1
                rendered = renderer.render(image, result) if need_render else None
                if summary["samples"] == 1:
                    cv2.imwrite(str(output / "first_frame.jpg"), rendered)
                if arguments.save_video:
                    if video_writer is None:
                        video_writer = cv2.VideoWriter(str(output / "annotated.mp4"), cv2.VideoWriter_fourcc(*"mp4v"),
                                                       min(rate, arguments.sample_fps), (rendered.shape[1], rendered.shape[0]))
                        if not video_writer.isOpened():
                            raise OSError("Cannot create annotated video")
                    video_writer.write(rendered)
                if not arguments.headless:
                    cv2.imshow(title, rendered)
                    delay = max(1, round((interval - (time.perf_counter() - started)) * 1000))
                    while True:
                        key = cv2.waitKey(50 if paused else delay) & 0xFF
                        if key in (27, ord("q")) or cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                            stop = True
                        elif key == ord(" "):
                            paused = not paused
                            cv2.imshow(title, renderer.render(image, result, paused))
                        elif key == ord("s"):
                            prefix = output / f"frame_{round(seconds * 1000)}ms_{time.time_ns()}"
                            cv2.imwrite(str(prefix.with_suffix(".jpg")), renderer.render(image, result, paused))
                            prefix.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                        elif key == ord("r"):
                            capture.set(cv2.CAP_PROP_POS_FRAMES, round(arguments.start * rate))
                            frame_index = round(arguments.start * rate)
                            next_sample = frame_index / rate
                            pipeline.reset()
                            paused = False
                        elif key in (ord("a"), ord("d"), ord("k"), ord("A"), ord("D"), ord("K")):
                            key = ord(chr(key).lower())
                            if key == ord("k"):
                                pipeline.behavior_monitor.cancel_request(result)
                            else:
                                request = pipeline.behavior_monitor.request_change("left" if key == ord("a") else "right", result)
                                with (output / "lane_requests.jsonl").open("a", encoding="utf-8") as request_log:
                                    request_log.write(json.dumps(request, ensure_ascii=False) + "\n")
                                print(request["message"], flush=True)
                            cv2.imshow(title, renderer.render(image, result, paused))
                        elif key == ord("t"):
                            region = select_region(image, "Select signal ROI", cv2, arguments.width)
                            if region:
                                scene["search_region"] = region
                                pipeline.reset()
                                (output / "config.json").write_text(json.dumps({"perception": scene, "behavior": behavior}, ensure_ascii=False, indent=2), encoding="utf-8")
                            cv2.imshow(title, renderer.render(image, result, paused))
                        if stop or not paused:
                            break
                if arguments.headless and summary["samples"] % 50 == 0:
                    print(f"Analyzed {summary['samples']} frames; video {seconds:.1f}s", flush=True)
    finally:
        capture.release()
        if video_writer is not None:
            video_writer.release()
        if not arguments.headless:
            cv2.destroyAllWindows()
        summary["average_processing_ms"] = round(summary["processing_ms_total"] / max(1, summary["samples"]), 2)
        (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if not summary["samples"]:
        raise ValueError("No frames were analyzed; check the video start position")
    print(f"Finished: {summary['samples']} analyzed frames; results: {output.resolve()}", flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Play a Windows video with actual YOLO, lane/signal perception and behavior decisions")
    parser.add_argument("video", nargs="?", type=Path, help="omit to choose a video in a file dialog")
    parser.add_argument("--config", type=Path, default=ROOT / "windows_video_112507.json")
    parser.add_argument("--model", type=Path, default=ROOT / "models" / "yolov5s.onnx")
    parser.add_argument("--lane-source", choices=("finetuned", "yolop"), default="finetuned",
                        help="finetuned uses the BDD LR-ASPP lane-style model; yolop selects the previous prototype")
    parser.add_argument("--lane-model", type=Path, help="override the selected lane source's default ONNX path")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "video_decision_runs" / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]))
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--save-video", action="store_true")
    parser.add_argument("--seconds", type=float, default=0, help="duration to analyze; 0 means to end")
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--sample-fps", type=float)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--backend", choices=("auto", "cpu", "directml"), default="auto")
    parser.add_argument("--playback", choices=("realtime", "offline"),
                        help="realtime keeps source speed; offline analyzes every sampled frame")
    arguments = parser.parse_args()
    if arguments.playback is None:
        arguments.playback = "offline" if arguments.headless else "realtime"
    if arguments.sample_fps is None:
        arguments.sample_fps = 8 if arguments.playback == "realtime" else 5
    if (not all(math.isfinite(value) for value in (arguments.seconds, arguments.start, arguments.sample_fps, arguments.confidence))
            or arguments.seconds < 0 or arguments.start < 0 or not 3 <= arguments.sample_fps <= 60
            or not 0 < arguments.confidence <= 1 or not 640 <= arguments.width <= 1920 or not 1 <= arguments.threads <= 32):
        parser.error("invalid duration, start, sampling rate, confidence, width or thread count")
    try:
        if arguments.video is None:
            if arguments.headless:
                parser.error("headless mode requires a video path")
            arguments.video = choose_video()
            if arguments.video is None:
                return 0
        if not arguments.video.is_file():
            raise FileNotFoundError(f"Video does not exist: {arguments.video}")
        import cv2
        import numpy
        return run_video(arguments, cv2, numpy)
    except (ImportError, OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"Video driving failed: {error}", file=sys.stderr)
        print("Dependencies: py -3.11 -m pip install opencv-python numpy pillow", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
