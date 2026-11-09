import io
import threading
from collections import Counter
from unittest.mock import patch

from video_realtime import LatestFrameWorker, evaluate_request
from video_behavior import VideoBehaviorMonitor
from windows_inference import load_inference_network


class ControlledPipeline:
    def __init__(self):
        self.scene = {}
        self.entered, self.release = threading.Event(), threading.Event()
        self.reset_count = 0
        self.processed = []

    def reset(self):
        self.reset_count += 1

    def process(self, image, frame_seq, timestamp_ns):
        if frame_seq == 1:
            self.entered.set()
            if not self.release.wait(timeout=2):
                raise RuntimeError("Controlled test did not release inference")
        self.processed.append(frame_seq)
        return {"video_seconds": timestamp_ns / 1e9, "decision": {"recommended_action": "NORMAL"},
                "processing_ms": 1.0, "signal": {"state": "unknown"}, "lane": {}}


def main():
    checks = []

    def check(name, condition):
        checks.append((name, bool(condition)))

    pipeline = ControlledPipeline()
    summary = {"samples": 0, "actions": Counter(), "processing_ms_total": 0.0,
               "signals": Counter(), "boundary_types": Counter()}
    log = io.StringIO()
    worker = LatestFrameWorker(pipeline, log, summary)
    worker.submit(None, 1, 0.0, 0, {"generation": 0})
    check("background_entered", pipeline.entered.wait(timeout=2))
    worker.submit(None, 2, 0.2, 0, {"generation": 0})
    worker.submit(None, 3, 0.4, 1, {"generation": 1})
    pipeline.release.set()
    worker.close()
    check("only_latest_pending_frame_processed", pipeline.processed == [1, 3])
    check("pending_replacement_count", worker.replaced == 1)
    check("generation_resets_history", pipeline.reset_count == 2 and pipeline.scene["generation"] == 1)
    check("old_generation_cannot_be_displayed", worker.result(0) is None)
    check("new_generation_available", worker.result(1)[2]["video_seconds"] == 0.4)
    check("every_processed_frame_logged", len(log.getvalue().splitlines()) == summary["samples"] == 2)
    monitor = VideoBehaviorMonitor()
    check("no_result_request_acknowledged", evaluate_request(monitor, "left", None, 1.0)["acknowledged"])
    result = {"video_seconds": 1.0, "lane": {"left_boundary_type": "dashed"},
              "adjacent": {"left": {"exists": True, "visible_clear": True}},
              "decision": {"recommended_action": "NORMAL", "timestamp_ns": 1_000_000_000, "frame_seq": 31}}
    check("stale_result_never_grants_change", evaluate_request(monitor, "left", result, 1.6)["status"] == "unknown")
    check("fresh_result_can_be_evaluated", evaluate_request(monitor, "left", result, 1.2)["status"] == "visible_clear")
    with patch("windows_inference.DirectMLNetwork", side_effect=RuntimeError("simulated unavailable GPU")), patch("warnings.warn") as warning:
        network, runtime = load_inference_network("models/yolov5s.onnx", backend="auto")
        check("auto_cpu_fallback_is_explicit", runtime["backend"] == "opencv-cpu" and warning.called)
        try:
            load_inference_network("models/yolov5s.onnx", backend="directml")
        except RuntimeError:
            check("explicit_gpu_failure_does_not_silently_fallback", True)
        else:
            check("explicit_gpu_failure_does_not_silently_fallback", False)
    failures = [name for name, passed in checks if not passed]
    print(f"Realtime scheduling checks: {len(checks) - len(failures)}/{len(checks)} passed")
    for name in failures:
        print(name)
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
