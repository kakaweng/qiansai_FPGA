import argparse
import csv
import json
import math
from pathlib import Path
import sys

from lane_detection import LaneMonitor, draw_lane
from traffic_light import TrafficLightMonitor, draw_traffic_light, load_scene


def quantize_rgb565(image):
    for channel, bits in ((0, 5), (1, 6), (2, 5)):
        reduced = image[:, :, channel] >> (8 - bits)
        image[:, :, channel] = (reduced << (8 - bits)) | (reduced >> (2 * bits - 8))
    return image


def reference_at(segments, seconds):
    return next((segment for segment in segments
                 if segment["start_seconds"] <= seconds < segment["end_seconds"]), None)


def main():
    parser = argparse.ArgumentParser(description="Replay image-based lane and signal perception with separate annotation scoring")
    parser.add_argument("video", type=Path)
    parser.add_argument("--scene", type=Path, default=Path(__file__).with_name("traffic_lane_scene.json"))
    parser.add_argument("--seconds", type=float, default=0, help="0 processes the full video")
    parser.add_argument("--sample-fps", type=float, default=10)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--headless", action="store_true")
    arguments = parser.parse_args()
    if (not math.isfinite(arguments.seconds) or not math.isfinite(arguments.sample_fps) or
            arguments.seconds < 0 or arguments.sample_fps <= 0):
        parser.error("seconds must be nonnegative; sample-fps must be positive")
    try:
        import cv2
        import numpy
        scene = load_scene(arguments.scene)
        lanes = LaneMonitor(scene, cv2, numpy)
        signals = TrafficLightMonitor(scene, cv2, numpy, allow_roi_fallback=True)
    except (ImportError, OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Road video setup failed: {error}\n")
    capture = cv2.VideoCapture(str(arguments.video))
    rate = capture.get(cv2.CAP_PROP_FPS)
    if not capture.isOpened() or rate <= 0:
        capture.release()
        parser.exit(1, f"Cannot read video/frame rate: {arguments.video}\n")
    references = scene.get("evaluation_only", {})
    lane_references = references.get("lane_segments", [])
    signal_references = references.get("traffic_signals", [])
    snapshot_times = {segment["start_seconds"] for segment in lane_references}
    snapshot_times.update(event["seconds"] for event in references.get("events", []))
    snapshot_times.update(reference["start_seconds"] for reference in signal_references)
    snapshot_times.update(reference["end_seconds"] for reference in signal_references)
    snapshot_times.update(range(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT) / rate) + 1, 5))
    snapshot_times = sorted(snapshot_times)
    snapshot_index = 0
    sample_interval = 1 / min(rate, arguments.sample_fps)
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    counts = dict(samples=0, annotated_lane_samples=0, ego_lane_known=0, ego_lane_correct=0,
                  lane_count_known=0, lane_count_correct=0, lane_pair_correct=0,
                  corridor_estimated_samples=0, corridor_visible_samples=0,
                  annotated_corridor_estimated_samples=0, annotated_corridor_visible_samples=0,
                  annotated_signal_samples=0, signal_correct=0)
    frame_index, next_sample = 0, 0.0
    lane_change_events = []
    last_event_key = None
    try:
        with (arguments.output_dir / "road.csv").open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(("video_seconds", "lane_state", "ego_lane", "lane_count", "lane_confidence",
                             "lane_reason", "light_state", "light_raw", "light_source", "light_reason",
                             "expected_ego_lane", "expected_lane_count", "expected_light_state",
                             "lane_boundaries", "ego_polygon", "lane_position", "lateral_position",
                             "lane_change", "lane_change_confidence", "lane_change_event_seq",
                             "lane_change_timestamp_ns", "lane_change_reason"))
            while not arguments.seconds or frame_index / rate < arguments.seconds:
                success, image = capture.read()
                if not success:
                    break
                seconds = frame_index / rate
                frame_index += 1
                if seconds + 1e-9 < next_sample:
                    continue
                next_sample += sample_interval
                image = quantize_rgb565(image)
                timestamp_ns = round(seconds * 1e9)
                lane_result = lanes.update(image, [], timestamp_ns)
                signal_result = signals.update(image, [], timestamp_ns)
                event_key = (lane_result.get("lane_change_event_seq", 0),
                             lane_result.get("lane_change_timestamp_ns"))
                change_direction = lane_result.get("lane_change", "unknown")
                new_change_event = (change_direction in ("left", "right") and event_key[0] > 0 and
                                    event_key[1] is not None and event_key != last_event_key)
                if new_change_event:
                    lane_change_events.append({"video_seconds": seconds, "direction": change_direction,
                                               "confidence": lane_result.get("lane_change_confidence", 0),
                                               "event_seq": event_key[0], "timestamp_ns": event_key[1],
                                               "reason": lane_result.get("lane_change_reason", "not_available")})
                    last_event_key = event_key
                lane_reference = reference_at(lane_references, seconds)
                signal_reference = reference_at(signal_references, seconds)
                expected_light = (signal_reference["state"].replace("_arrow", "")
                                  if signal_reference else None)
                counts["samples"] += 1
                counts["corridor_visible_samples"] += bool(lane_result.get("ego_polygon"))
                counts["corridor_estimated_samples"] += (lane_result["state"] == "estimated" and
                                                        bool(lane_result.get("ego_polygon")))
                if lane_reference:
                    counts["annotated_lane_samples"] += 1
                    counts["annotated_corridor_visible_samples"] += bool(lane_result.get("ego_polygon"))
                    counts["annotated_corridor_estimated_samples"] += (lane_result["state"] == "estimated" and
                                                                      bool(lane_result.get("ego_polygon")))
                    counts["ego_lane_known"] += lane_result["ego_lane"] is not None
                    counts["lane_count_known"] += lane_result["lane_count"] is not None
                    ego_correct = lane_result["ego_lane"] == lane_reference["ego_lane"]
                    count_correct = lane_result["lane_count"] == lane_reference["lane_count"]
                    counts["ego_lane_correct"] += ego_correct
                    counts["lane_count_correct"] += count_correct
                    counts["lane_pair_correct"] += ego_correct and count_correct
                if signal_reference:
                    counts["annotated_signal_samples"] += 1
                    counts["signal_correct"] += signal_result["state"] == expected_light
                writer.writerow((f"{seconds:.3f}", lane_result["state"], lane_result["ego_lane"],
                                 lane_result["lane_count"], f"{lane_result['confidence']:.3f}", lane_result["reason"],
                                 signal_result["state"], signal_result["raw_state"], signal_result["source"],
                                 signal_result["reason"], lane_reference["ego_lane"] if lane_reference else None,
                                 lane_reference["lane_count"] if lane_reference else None, expected_light,
                                 json.dumps(lane_result["boundaries"]), json.dumps(lane_result.get("ego_polygon", [])),
                                 lane_result.get("lane_position", "unknown"), lane_result.get("lateral_position", "unknown"),
                                 change_direction, lane_result.get("lane_change_confidence", 0), event_key[0], event_key[1],
                                 lane_result.get("lane_change_reason", "not_available")))
                if new_change_event or (snapshot_index < len(snapshot_times) and seconds >= snapshot_times[snapshot_index]):
                    snapshot = image.copy()
                    draw_lane(snapshot, lane_result, cv2)
                    draw_traffic_light(snapshot, signal_result, cv2)
                    cv2.imwrite(str(arguments.output_dir / f"road_{seconds:06.2f}.jpg"), snapshot)
                    while snapshot_index < len(snapshot_times) and seconds >= snapshot_times[snapshot_index]:
                        snapshot_index += 1
                if lane_result["changed"] or signal_result["changed"]:
                    print(f"Road state: video_s={seconds:.3f} ego={lane_result['ego_lane']} "
                          f"position={lane_result.get('lane_position', 'unknown')} "
                          f"change={change_direction} signal={signal_result['state']}", flush=True)
                if not arguments.headless:
                    preview = cv2.resize(image, (960, round(960 * image.shape[0] / image.shape[1])))
                    scale = 960 / image.shape[1]
                    draw_lane(preview, lane_result, cv2, scale=scale)
                    draw_traffic_light(preview, signal_result, cv2, scale=scale)
                    cv2.imshow("Lane and traffic-light offline check", preview)
                    if cv2.waitKey(max(1, round(sample_interval * 1000))) & 0xFF in (27, ord("q")):
                        break
    finally:
        capture.release()
        if not arguments.headless:
            cv2.destroyAllWindows()
    summary = dict(counts, lane_change_events=lane_change_events,
                   mode="offline image perception with RGB565 quantization; no RKNN detections")
    for name, numerator, denominator in (
            ("ego_lane_coverage", "ego_lane_known", "annotated_lane_samples"),
            ("ego_lane_accuracy_when_known", "ego_lane_correct", "ego_lane_known"),
            ("lane_count_coverage", "lane_count_known", "annotated_lane_samples"),
            ("lane_count_accuracy_when_known", "lane_count_correct", "lane_count_known"),
            ("lane_pair_correct_rate", "lane_pair_correct", "annotated_lane_samples"),
            ("corridor_estimate_coverage", "corridor_estimated_samples", "samples"),
            ("corridor_visible_coverage", "corridor_visible_samples", "samples"),
            ("annotated_corridor_estimate_coverage", "annotated_corridor_estimated_samples", "annotated_lane_samples"),
            ("annotated_corridor_visible_coverage", "annotated_corridor_visible_samples", "annotated_lane_samples"),
            ("annotated_green_recall", "signal_correct", "annotated_signal_samples")):
        summary[name] = counts[numerator] / counts[denominator] if counts[denominator] else None
    (arguments.output_dir / "road_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    print(f"Offline check completed. Results: {arguments.output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
