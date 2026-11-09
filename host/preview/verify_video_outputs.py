import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import cv2
import numpy

from video_behavior import VideoBehaviorMonitor
from video_driving import VideoRenderer


ROOT = Path(__file__).resolve().parent


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description="Check full sampled video outputs and known regression frames")
    parser.add_argument("--prefix", default="gpu_final_")
    parser.add_argument("--report", default="gpu_acceptance.json")
    arguments = parser.parse_args()
    checks, runs = [], {}

    def check(name, passed, evidence):
        checks.append({"name": name, "passed": bool(passed), "evidence": evidence})

    for suffix, expected_count in (("112507", 225), ("113421", 66), ("113539", 386)):
        directory = ROOT / "video_decision_runs" / (arguments.prefix + suffix)
        summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
        rows = read_rows(directory / "observations.jsonl")
        code_matches = all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
                           for name, digest in summary["code_sha256"].items())
        check(suffix + "_current_code", code_matches, summary["code_sha256"])
        check(suffix + "_complete_samples", len(rows) == expected_count == summary["samples"], len(rows))
        check(suffix + "_decision_version", all(row["decision"]["version"] == VideoBehaviorMonitor.VERSION for row in rows), VideoBehaviorMonitor.VERSION)
        check(suffix + "_ordered_frames", all(later["video_seconds"] > earlier["video_seconds"] for earlier, later in zip(rows, rows[1:])), [rows[0]["video_seconds"], rows[-1]["video_seconds"]])
        capture = cv2.VideoCapture(str(directory / "annotated.mp4"))
        exported_count = round(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        exported_fps = capture.get(cv2.CAP_PROP_FPS)
        capture.release()
        check(suffix + "_export_complete", exported_count == expected_count and abs(exported_fps - 5) < 0.01,
              {"frames": exported_count, "fps": exported_fps})
        runs[suffix] = {"directory": str(directory), "samples": len(rows), "actions": summary["actions"],
                        "average_processing_ms": summary["average_processing_ms"],
                        "lane_states": dict(Counter(row["lane"]["state"] for row in rows)),
                        "boundary_types": dict(Counter(row["lane"][side + "_boundary_type"] for row in rows for side in ("left", "right"))),
                        "directions": dict(Counter(row["road"]["direction"] for row in rows))}
        selected_times = (3.2, 18.2, 23.2, 28.0, 38.0, 40.2, 41.2) if suffix == "112507" else (10.2, 26.0, 26.2, 70.0) if suffix == "113539" else (10.0, 12.0)
        selected = [min(rows, key=lambda row: abs(row["video_seconds"] - second)) for second in selected_times]
        source = cv2.VideoCapture(summary["video"])
        keyframes = []
        for row in selected:
            source.set(cv2.CAP_PROP_POS_MSEC, row["video_seconds"] * 1000)
            success, image = source.read()
            if not success:
                raise RuntimeError("Cannot read selected source frame")
            name = f"sample_{round(row['video_seconds'] * 1000)}ms.jpg"
            cv2.imwrite(str(directory / name), VideoRenderer(cv2, numpy, 960).render(image, row))
            keyframes.append({"seconds": row["video_seconds"], "action": row["decision"]["recommended_action"],
                              "pedestrian_risk": row["observation"]["pedestrian"]["risk"],
                              "direction": row["road"]["direction"], "snapshot": str(directory / name)})
        source.release()
        runs[suffix]["keyframes"] = keyframes
        if suffix == "112507":
            for second in (3.2, 18.2, 23.2):
                row = min(rows, key=lambda item: abs(item["video_seconds"] - second))
                check(f"112507_{second}_no_pedestrian_stop", row["observation"]["pedestrian"]["risk"] == "none"
                      and row["decision"]["recommended_action"] != "STOP", row["decision"]["state"])
            rider_frame = min(rows, key=lambda row: abs(row["video_seconds"] - 18.2))
            check("112507_rider_dropout_excluded", rider_frame["observation"]["pedestrian"]["riders_excluded"] > 0,
                  rider_frame["observation"]["pedestrian"]["riders_excluded"])
        if suffix == "113539":
            red_rows = [row for row in rows if 10.2 <= row["video_seconds"] <= 25.8]
            check("113539_red_stop_stable", bool(red_rows) and all(row["decision"]["recommended_action"] == "STOP" for row in red_rows), len(red_rows))
            green = min(rows, key=lambda row: abs(row["video_seconds"] - 26.2))
            check("113539_green_releases_stop", green["decision"]["recommended_action"] == "NORMAL", green["decision"]["state"])

    gui = ROOT / "video_decision_runs" / "realtime_gui_keys"
    requests = read_rows(gui / "lane_requests.jsonl")
    check("gui_uppercase_A_D_during_pause", len(requests) == 2 and [item["side"] for item in requests] == ["left", "right"]
          and all(item["acknowledged"] and item["status"] == "visible_clear" for item in requests)
          and requests[0]["timestamp_ns"] == requests[1]["timestamp_ns"], requests)
    snapshots = list(gui.glob("frame_*.json"))
    check("gui_snapshot_after_each_request", len(snapshots) == 2
          and {json.loads(path.read_text(encoding="utf-8"))["lane_request"]["side"] for path in snapshots} == {"left", "right"}, [path.name for path in snapshots])
    gui_rows = read_rows(gui / "observations.jsonl")
    check("gui_restart_uses_new_session", len({row["decision"]["session_id"] for row in gui_rows}) == 2,
          sorted({row["decision"]["session_id"] for row in gui_rows}))
    regression = json.loads((ROOT / "video_decision_runs" / "v2_rule_regression.json").read_text(encoding="utf-8"))
    check("synthetic_rules_pass", all(item["passed"] for item in regression["checks"]), len(regression["checks"]))
    report = {"decision_version": VideoBehaviorMonitor.VERSION,
              "scope": "real_full_video_gpu_inference_plus_injected_keys_in_realtime_preview; counts_are_not_accuracy",
              "runs": runs, "checks": checks}
    output = ROOT / "video_decision_runs" / arguments.report
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [item for item in checks if not item["passed"]]
    print(f"Full video acceptance: {len(checks) - len(failures)}/{len(checks)} passed; report: {output}")
    for suffix, result in runs.items():
        print(suffix, json.dumps({key: result[key] for key in ("samples", "actions", "average_processing_ms", "lane_states", "boundary_types", "directions")}))
    for failure in failures:
        print(failure["name"], failure["evidence"])
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
