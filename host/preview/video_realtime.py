import copy
import hashlib
import json
import math
import threading
import time
from collections import Counter

from video_behavior import VideoBehaviorMonitor


class LatestFrameWorker:
    def __init__(self, pipeline, log, summary):
        self.pipeline, self.log, self.summary = pipeline, log, summary
        self.condition = threading.Condition()
        self.pending, self.latest = None, None
        self.closed, self.error = False, None
        self.replaced = 0
        self.thread = threading.Thread(target=self.run, name="video-perception", daemon=True)
        self.thread.start()

    def submit(self, image, frame_seq, seconds, generation, scene):
        with self.condition:
            if self.pending is not None:
                self.replaced += 1
            self.pending = (image, frame_seq, seconds, generation, scene)
            self.condition.notify()

    def result(self, generation):
        with self.condition:
            if self.error:
                raise RuntimeError(f"Background analysis failed: {self.error}") from self.error
            return self.latest if self.latest and self.latest[0] == generation else None

    def run(self):
        generation = None
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.pending is not None or self.closed)
                    if self.pending is None and self.closed:
                        return
                    image, frame_seq, seconds, current_generation, scene = self.pending
                    self.pending = None
                if generation != current_generation:
                    self.pipeline.scene.clear()
                    self.pipeline.scene.update(copy.deepcopy(scene))
                    self.pipeline.reset()
                    generation = current_generation
                result = self.pipeline.process(image, frame_seq, round(seconds * 1e9))
                self.log.write(json.dumps(result, ensure_ascii=False) + "\n")
                self.summary["samples"] += 1
                self.summary["actions"][result["decision"]["recommended_action"]] += 1
                self.summary["processing_ms_total"] += result["processing_ms"]
                self.summary["signals"][result["signal"]["state"]] += 1
                self.summary["boundary_types"].update(result["lane"].get(side + "_boundary_type", "unknown")
                                                       for side in ("left", "right"))
                with self.condition:
                    self.latest = (generation, image, result)
        except Exception as error:
            with self.condition:
                self.error = error

    def close(self, drain=True):
        with self.condition:
            self.closed = True
            if not drain:
                self.pending = None
            self.condition.notify()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError("Background inference did not stop within 10 seconds")
        if self.error:
            raise RuntimeError(f"Background analysis failed: {self.error}") from self.error


class RealtimeRenderer:
    def __init__(self, cv2, numpy, width):
        from video_driving import VideoRenderer
        self.cv2, self.numpy, self.width = cv2, numpy, width
        self.renderer = VideoRenderer(cv2, numpy, width)
        self.cached_key, self.cached = None, None

    def render(self, image, analyzed, result, seconds, paused, fps, request):
        cv2, numpy, width = self.cv2, self.numpy, self.width
        left_width = round(width * 0.60)
        image_height = round(image.shape[0] * left_width / image.shape[1])
        canvas = numpy.full((image_height + 460, width, 3), (28, 28, 28), dtype=numpy.uint8)
        canvas[:image_height, :left_width] = cv2.resize(image, (left_width, image_height))
        age = max(0, seconds - result["video_seconds"]) if result else None
        stale = age is not None and age > 0.75
        view = None
        if result:
            view = copy.deepcopy(result)
            view["decision"]["lane_request"] = copy.deepcopy(request)
            if request:
                view["decision"]["lane_request"]["historical"] = seconds != request["timestamp_ns"] / 1e9
            if stale:
                view["decision"].update(recommended_action="WATCH", state="ANALYSIS_STALE",
                                         message="分析结果已过期，请观察当前画面；暂不提供新的通行或变道判断")
                view["decision"]["permissions"] = {name: "unknown" for name in view["decision"]["permissions"]}
                view["decision"]["notes"] = ["下方信号与线型属于右侧分析帧，等待最新结果"]
            view["display_context"] = {"playback_seconds": seconds, "analysis_age_ms": round(age * 1000, 2),
                                       "stale": stale, "overlay_scope": "analyzed_frame_only"}
            key = (result["decision"]["session_id"], result["decision"]["frame_seq"], paused, stale,
                   json.dumps(view["decision"]["lane_request"], ensure_ascii=False))
            if key != self.cached_key:
                self.cached = self.renderer.render(analyzed, view, paused)
                self.cached_key = key
            original_height = round(analyzed.shape[0] * width / analyzed.shape[1])
            right_width = width - left_width
            right_height = round(analyzed.shape[0] * right_width / analyzed.shape[1])
            canvas[:right_height, left_width:] = cv2.resize(self.cached[:original_height], (right_width, right_height))
            canvas[image_height + 70:] = self.cached[original_height:]
        title = f"{'已暂停' if paused else '原速播放'} {seconds:.2f}s  显示 {fps:.1f} fps"
        analysis_text = f"同帧分析 {result['video_seconds']:.2f}s  落后 {age * 1000:.0f}ms" if result else "正在分析首帧，请稍候"
        if self.renderer.fonts:
            Image, ImageDraw = self.renderer.pillow
            rendered = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
            drawing = ImageDraw.Draw(rendered)
            font = self.renderer.fonts[1]
            drawing.rectangle((0, 0, left_width, 28), fill=(28, 28, 28))
            drawing.text((8, 4), title, font=font, fill=(225, 225, 225))
            drawing.text((left_width + 8, image_height - 26), "分析帧保留真实框与车道线", font=font, fill=(235, 210, 125))
            drawing.text((14, image_height + 6), analysis_text, font=font, fill=(235, 150, 100) if stale else (95, 235, 150))
            drawing.text((14, image_height + 34), "左侧当前视频｜右侧最近分析帧；检测框仅叠加到其对应帧", font=font, fill=(225, 225, 225))
            if not result:
                drawing.text((14, image_height + 90), "Space 暂停  A/D 变道判断  R 重播  Q 退出", font=font, fill=(225, 225, 225))
                if request:
                    message = "已收到变道请求；分析尚未完成，暂时无法判断"
                    drawing.text((14, image_height + 124), message, font=font, fill=(235, 210, 125))
            return cv2.cvtColor(numpy.array(rendered), cv2.COLOR_RGB2BGR), view, age
        cv2.putText(canvas, f"Playback {seconds:.2f}s | {fps:.1f} fps", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (225, 225, 225), 1)
        cv2.putText(canvas, f"Analysis lag {age * 1000:.0f}ms" if age is not None else "Waiting for analysis", (14, image_height + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (225, 225, 225), 1)
        return canvas, view, age


def evaluate_request(monitor, side, result, seconds):
    age = seconds - result["video_seconds"] if result else None
    if result and 0 <= age <= 0.5:
        request = monitor.request_change(side, copy.deepcopy(result))
        request.update(playback_seconds=seconds, analysis_age_seconds=round(age, 3))
        return request
    monitor.request_seq += 1
    return {"status": "unknown", "message": "已收到变道请求，但当前分析尚未完成或落后超过 500ms，暂时无法判断",
            "reasons": ["analysis_not_fresh"], "side": side, "request_seq": monitor.request_seq,
            "timestamp_ns": round(seconds * 1e9), "frame_seq": None, "acknowledged": True,
            "analysis_age_seconds": age, "scope": "visible_front_area_only"}


def run_realtime_video(arguments, pipeline, cv2, numpy):
    from video_driving import ROOT, select_region
    capture = cv2.VideoCapture(str(arguments.video))
    rate = capture.get(cv2.CAP_PROP_FPS)
    total_frames = round(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if not capture.isOpened() or not math.isfinite(rate) or rate <= 0:
        capture.release()
        raise ValueError(f"Cannot read video: {arguments.video}")
    output = arguments.output_dir
    output.mkdir(parents=True, exist_ok=True)
    scene = copy.deepcopy(pipeline.scene)
    (output / "config.json").write_text(json.dumps({"perception": scene, "behavior": pipeline.behavior}, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"video": str(arguments.video.resolve()), "mode": "realtime_playback_latest_frame_analysis",
               "decision_version": pipeline.behavior_monitor.VERSION,
               "runtime": {"objects": pipeline.detector.runtime, "lanes": pipeline.lane_segmenter.runtime},
               "model": str(pipeline.detector.model_path), "lane_model": str(pipeline.lane_segmenter.path),
               "lane_source": pipeline.lane_source,
               "lane_decoder_thresholds": dict(getattr(pipeline.lanes, "style_config", {})),
               "model_sha256": hashlib.sha256(pipeline.detector.model_path.read_bytes()).hexdigest(),
               "lane_model_sha256": hashlib.sha256(pipeline.lane_segmenter.path.read_bytes()).hexdigest(),
               "sample_fps": arguments.sample_fps, "source_fps": rate, "samples": 0,
               "actions": Counter(), "signals": Counter(), "boundary_types": Counter(),
               "processing_ms_total": 0.0, "display_frames": 0, "skipped_display_frames": 0,
               "submitted_frames": 0, "analysis_age_ms": [],
               "code_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                               for name in ("video_driving.py", "video_realtime.py", "windows_inference.py",
                                            "windows_object_detector.py", "windows_lane_segmenter.py", "windows_lane_style.py",
                                            "lane_detection.py", "lane_boundary_type.py", "video_scene_analysis.py")}}
    renderer = RealtimeRenderer(cv2, numpy, arguments.width)
    request_monitor = VideoBehaviorMonitor()
    title = "Windows video driving - realtime playback and analysis"
    generation, paused, request = 0, False, None
    seconds, next_sample = arguments.start, arguments.start
    frame_index = round(arguments.start * rate)
    capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    anchor_wall, anchor_video = time.perf_counter(), arguments.start
    displayed_frames, active_seconds = 0, 0.0
    image, rendered, view = None, None, None
    last_frame_index, last_display_ns = frame_index - 1, None
    video_writer, latest_frame_saved = None, False
    duration_end = min(total_frames / rate, arguments.start + arguments.seconds) if arguments.seconds else total_frames / rate
    if not arguments.headless:
        cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    log = (output / "observations.jsonl").open("w", encoding="utf-8")
    worker = LatestFrameWorker(pipeline, log, summary)
    anchor_wall = time.perf_counter()
    stopped_early = False
    try:
        while True:
            now = time.perf_counter()
            if not paused:
                target_seconds = anchor_video + now - anchor_wall
                if target_seconds >= duration_end:
                    break
                target_index = math.floor(target_seconds * rate)
                if target_index > last_frame_index:
                    skipped = max(0, target_index - frame_index)
                    for unused in range(skipped):
                        if not capture.grab():
                            break
                    summary["skipped_display_frames"] += skipped
                    success, image = capture.read()
                    if not success:
                        break
                    seconds = target_index / rate
                    frame_index, last_frame_index = target_index + 1, target_index
                    if last_display_ns is not None:
                        active_seconds += now - last_display_ns
                    last_display_ns = now
                    displayed_frames += 1
                    summary["display_frames"] += 1
                    if seconds + 1e-8 >= next_sample:
                        worker.submit(image, frame_index, seconds, generation, copy.deepcopy(scene))
                        summary["submitted_frames"] += 1
                        while next_sample <= seconds + 1e-8:
                            next_sample += 1 / arguments.sample_fps
            latest = worker.result(generation)
            result, analyzed = (latest[2], latest[1]) if latest else (None, None)
            if image is not None:
                render_key = (last_frame_index, result["decision"]["frame_seq"] if result else None,
                              paused, json.dumps(request, ensure_ascii=False))
                if render_key != getattr(renderer, "last_display_key", None):
                    rendered, view, age = renderer.render(image, analyzed, result, seconds, paused,
                                                          displayed_frames / max(0.001, active_seconds), request)
                    renderer.last_display_key = render_key
                    if age is not None and not paused:
                        summary["analysis_age_ms"].append(round(age * 1000, 2))
                    if result and not latest_frame_saved:
                        cv2.imwrite(str(output / "first_frame.jpg"), rendered)
                        latest_frame_saved = True
                    if not arguments.headless:
                        cv2.imshow(title, rendered)
                if arguments.save_video and not paused and last_frame_index != getattr(renderer, "last_written_index", None):
                    if video_writer is None:
                        video_writer = cv2.VideoWriter(str(output / "annotated.mp4"), cv2.VideoWriter_fourcc(*"mp4v"),
                                                       rate, (rendered.shape[1], rendered.shape[0]))
                        if not video_writer.isOpened():
                            raise OSError("Cannot create realtime preview recording")
                    missing_frames = last_frame_index - getattr(renderer, "last_written_index", last_frame_index - 1)
                    for unused in range(missing_frames):
                        video_writer.write(rendered)
                    renderer.last_written_index = last_frame_index
            if arguments.headless:
                time.sleep(0.002)
                continue
            key = cv2.waitKey(2) & 0xFF
            if key in (27, ord("q")) or cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                stopped_early = True
                break
            if key == ord(" "):
                paused = not paused
                if not paused:
                    anchor_video, anchor_wall = seconds, time.perf_counter()
                last_display_ns = None
            elif key in (ord("a"), ord("d"), ord("A"), ord("D")):
                request = evaluate_request(request_monitor, "left" if chr(key).lower() == "a" else "right", result, seconds)
                with (output / "lane_requests.jsonl").open("a", encoding="utf-8") as request_log:
                    request_log.write(json.dumps(request, ensure_ascii=False) + "\n")
                print(f"Lane request acknowledged: {request['side']} {request['status']}", flush=True)
            elif key in (ord("k"), ord("K")):
                request = None
            elif key == ord("s") and rendered is not None:
                prefix = output / f"frame_{round(seconds * 1000)}ms_{time.time_ns()}"
                cv2.imwrite(str(prefix.with_suffix(".jpg")), rendered)
                prefix.with_suffix(".json").write_text(json.dumps({"playback_seconds": seconds, "analysis": view,
                                                                  "lane_request": request}, ensure_ascii=False, indent=2), encoding="utf-8")
            elif key == ord("r"):
                generation += 1
                capture.set(cv2.CAP_PROP_POS_FRAMES, round(arguments.start * rate))
                frame_index = round(arguments.start * rate)
                last_frame_index = frame_index - 1
                seconds, next_sample = arguments.start, arguments.start
                anchor_video, anchor_wall = arguments.start, time.perf_counter()
                image, request, paused = None, None, False
                last_display_ns = None
                renderer.last_written_index = frame_index - 1
            elif key == ord("t") and image is not None:
                started = time.perf_counter()
                region = select_region(image, "Select signal ROI", cv2, arguments.width)
                anchor_wall += time.perf_counter() - started
                if region:
                    scene["search_region"] = region
                    generation += 1
                    request = None
                    worker.submit(image, frame_index, seconds, generation, copy.deepcopy(scene))
                    (output / "config.json").write_text(json.dumps({"perception": scene, "behavior": pipeline.behavior}, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        try:
            worker.close(drain=not stopped_early)
        finally:
            log.close()
            capture.release()
            if video_writer:
                video_writer.release()
            if not arguments.headless:
                cv2.destroyAllWindows()
            summary["replaced_pending_frames"] = worker.replaced
            summary["average_processing_ms"] = round(summary["processing_ms_total"] / max(1, summary["samples"]), 2)
            summary["display_fps"] = round(displayed_frames / max(0.001, active_seconds), 2)
            summary["played_video_seconds"] = round(max(0, seconds - arguments.start), 3)
            summary["active_playback_seconds"] = round(active_seconds, 3)
            ages = summary.pop("analysis_age_ms")
            summary["average_analysis_age_ms"] = round(sum(ages) / max(1, len(ages)), 2)
            summary["maximum_analysis_age_ms"] = max(ages, default=None)
            (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if not summary["display_frames"]:
        raise ValueError("No video frames were displayed; check start position")
    print(f"Finished: {summary['display_frames']} display frames, {summary['samples']} analyzed frames; results: {output.resolve()}", flush=True)
    return 0
