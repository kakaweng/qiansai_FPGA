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


def read_rows(directory):
    return [json.loads(line) for line in (directory / "observations.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]


def summarize(rows):
    displayed, fresh, held, raw, unknown_reasons = (Counter() for _ in range(5))
    for row in rows:
        for side in ("left", "right"):
            lane = row["lane"]
            boundary_type = lane.get(f"{side}_boundary_type", "unknown")
            evidence = lane.get("boundary_type_evidence", {}).get(side, {})
            observed = evidence.get("observed_type", evidence.get("type", "unknown"))
            displayed[boundary_type] += 1
            raw[observed] += 1
            if evidence.get("held"):
                held[boundary_type] += 1
            elif boundary_type in ("solid", "dashed"):
                fresh[boundary_type] += 1
            if boundary_type == "unknown":
                reason = "confirming" if observed in ("solid", "dashed") else evidence.get("reason", "no_current_boundary")
                unknown_reasons[reason] += 1
    return {"side_observations": len(rows) * 2, "displayed": dict(displayed),
            "fresh_confirmed": dict(fresh), "held": dict(held), "raw": dict(raw),
            "unknown_reasons": dict(unknown_reasons)}


def main():
    parser = argparse.ArgumentParser(description="Audit lane boundary coverage without treating coverage as accuracy")
    parser.add_argument("--prefix", default="lane_patch_")
    parser.add_argument("--baseline-prefix", default="gpu_final_")
    arguments = parser.parse_args()
    monitor = VideoBehaviorMonitor()
    renderer = VideoRenderer(cv2, numpy, 960)
    checks, runs = [], {}
    for suffix, expected_count in (("112507", 225), ("113421", 66), ("113539", 386)):
        directory = ROOT / "video_decision_runs" / (arguments.prefix + suffix)
        summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
        rows = read_rows(directory)
        checks.append({"name": suffix + "_complete", "passed": len(rows) == expected_count})
        checks.append({"name": suffix + "_current_code", "passed": all(
            hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
            for name, digest in summary["code_sha256"].items())})
        held_grants = []
        for row in rows:
            for side in ("left", "right"):
                evidence = row["lane"].get("boundary_type_evidence", {}).get(side, {})
                evaluation = monitor.change_evaluation(side, row["lane"], row["adjacent"], row["decision"]["recommended_action"])
                if evidence.get("held") and evaluation["status"] == "visible_clear":
                    held_grants.append({"seconds": row["video_seconds"], "side": side})
        checks.append({"name": suffix + "_held_never_grants_change", "passed": not held_grants,
                       "evidence": held_grants})
        baseline = ROOT / "video_decision_runs" / (arguments.baseline_prefix + suffix)
        runs[suffix] = {"directory": str(directory), "samples": len(rows),
                        "average_processing_ms": summary["average_processing_ms"],
                        "current": summarize(rows), "baseline": summarize(read_rows(baseline))}
        candidates = {
            "held": next((row for row in rows if any(row["lane"]["boundary_type_evidence"].get(side, {}).get("held")
                                                   for side in ("left", "right"))), None),
            "fresh": next((row for row in rows if any(row["lane"].get(f"{side}_boundary_type") in ("solid", "dashed")
                                                    and not row["lane"]["boundary_type_evidence"].get(side, {}).get("held")
                                                    for side in ("left", "right"))), None),
        }
        source = cv2.VideoCapture(summary["video"])
        for name, row in candidates.items():
            if row is None:
                continue
            source.set(cv2.CAP_PROP_POS_MSEC, row["video_seconds"] * 1000)
            available, image = source.read()
            if available:
                cv2.imwrite(str(directory / ("lane_" + name + ".jpg")), renderer.render(image, row))
        source.release()
    output = ROOT / "video_decision_runs" / "lane_boundary_audit.json"
    output.write_text(json.dumps({"scope": "sampled_actual_video_inference; output_coverage_is_not_accuracy",
                                  "runs": runs, "checks": checks}, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [check for check in checks if not check["passed"]]
    print(f"Lane output audit: {len(checks) - len(failures)}/{len(checks)} checks passed; report: {output}")
    for suffix, run in runs.items():
        print(suffix, json.dumps(run["current"], ensure_ascii=True))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
