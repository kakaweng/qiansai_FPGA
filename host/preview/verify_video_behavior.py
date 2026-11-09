import copy
import json
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy

from video_behavior import VideoBehaviorMonitor
from video_scene_analysis import DirectionalSignalMonitor, ObjectMotionMonitor, PedestrianRiskMonitor


def main():
    checks = []

    def check(name, actual, expected):
        checks.append({"name": name, "actual": actual, "expected": expected, "passed": actual == expected})

    lane = {"state": "estimated", "ego_polygon": [[430, 620], [570, 620], [760, 940], [240, 940]],
            "boundaries": [[300, 620, 0, 940], [430, 620, 240, 940],
                           [570, 620, 760, 940], [700, 620, 1000, 940]],
            "left_boundary_type": "dashed", "right_boundary_type": "dashed"}
    pedestrian = PedestrianRiskMonitor()

    def person(box, offset=0.0, speed=0.3):
        return {"class_id": 0, "label": "person", "score": 0.9, "box": box, "track_id": 1,
                "lane_offset": offset, "lateral_speed": speed, "closing_speed": 0.0}

    check("roadside_person", pedestrian.update([person([800, 700, 840, 800], 2.0, 0.0)], lane, 0, 1000, 1000)["risk"], "none")
    check("approaching_path", pedestrian.update([person([750, 650, 790, 730], 1.0, -0.3)], lane, 200_000_000, 1000, 1000)["risk"], "watch")
    check("distant_crossing", pedestrian.update([person([480, 650, 520, 730])], lane, 400_000_000, 1000, 1000)["risk"], "slow")
    check("near_path_conflict", pedestrian.update([person([480, 750, 520, 880])], lane, 600_000_000, 1000, 1000)["risk"], "stop")
    check("observed_exit_releases_immediately", pedestrian.update([person([780, 750, 820, 880], 1.0)], lane, 800_000_000, 1000, 1000)["risk"], "none")
    pedestrian.update([person([480, 750, 520, 880])], lane, 1_000_000_000, 1000, 1000)
    check("missing_person_is_not_clearance", pedestrian.update([], lane, 1_200_000_000, 1000, 1000)["risk"], "watch")
    check("missing_person_memory_expires", pedestrian.update([], lane, 1_800_000_000, 1000, 1000)["risk"], "none")

    motion = ObjectMotionMonitor()
    rider = {"class_id": 0, "label": "person", "score": 0.9, "box": [480, 750, 520, 880]}
    cycle = {"class_id": 3, "label": "motorcycle", "score": 0.8, "box": [470, 770, 540, 910]}
    motion.update([rider, cycle], lane, 0, 1000, 1000)
    objects = motion.update([rider], lane, 200_000_000, 1000, 1000)
    check("rider_identity_survives_cycle_dropout", objects[0]["rider_confirmed"], True)
    check("rider_is_not_pedestrian_alert", PedestrianRiskMonitor().update(objects, lane, 200_000_000, 1000, 1000)["risk"], "none")

    monitor = VideoBehaviorMonitor()
    signal = {"directions": {side: {"state": "unknown", "retained": False, "boxes": [], "source": "synthetic_regression"}
                              for side in ("straight", "left", "right")}, "candidates": [], "region": [0, 0, 1000, 600]}
    road = {"direction": "straight", "direction_source": "synthetic_regression", "stop_line_near": False}
    clear = {"risk": "none", "source": "synthetic_regression"}
    adjacent = {side: {"exists": True, "visible_clear": True, "evidence": [], "scope": "visible_front_area_only"}
                for side in ("left", "right")}

    def update(timestamp, current_lane=lane):
        return monitor.update(clear, signal, road, current_lane, adjacent, int(timestamp * 5) + 1,
                              round(timestamp * 1e9), "synthetic_regression")

    unknown_lane = dict(lane, state="unknown", left_boundary_type="unknown", right_boundary_type="unknown")
    check("unknown_line_does_not_force_wait", update(0, unknown_lane)["recommended_action"], "NORMAL")
    result = {"lane": lane, "adjacent": adjacent, "decision": update(0.2)}
    request = monitor.request_change("left", result)
    check("request_acknowledged_immediately", request["acknowledged"], True)
    check("dashed_visible_clear_request", request["status"], "visible_clear")
    lane["left_boundary_type"] = "solid"
    later = update(0.4)
    check("single_request_does_not_change_silently", later["lane_request"]["status"], "visible_clear")
    check("previous_request_marked_historical", later["lane_request"]["historical"], True)
    result["decision"] = later
    check("second_press_checks_current_solid_line", monitor.request_change("left", result)["status"], "forbidden")
    lane["left_boundary_type"] = "dashed"
    adjacent["left"]["visible_clear"] = False
    check("near_adjacent_vehicle_blocks_request", monitor.request_change("left", result)["status"], "forbidden")
    adjacent["left"]["visible_clear"] = None
    check("unknown_adjacent_area_does_not_grant_request", monitor.request_change("left", result)["status"], "unknown")
    monitor.cancel_request(result)
    check("cancel_clears_request", result["decision"]["lane_request"], None)

    signal["directions"]["straight"]["state"] = "red"
    check("distant_red_slows", update(0.6)["recommended_action"], "SLOW")
    road["stop_line_near"] = True
    check("near_red_stops", update(0.8)["recommended_action"], "STOP")
    road["stop_line_near"] = False
    check("occluded_stop_line_preserves_confirmed_stop", update(1.0)["recommended_action"], "STOP")
    signal["directions"]["straight"]["state"] = "green"
    check("confirmed_green_releases_red_stop", update(1.2)["recommended_action"], "NORMAL")
    signal["directions"]["straight"]["state"] = "unknown"
    signal["context_active"] = True
    check("expired_signal_requires_observation", update(1.4)["recommended_action"], "WATCH")

    scene = {"search_region": [0.0, 0.0, 1.0, 0.6]}
    lights = DirectionalSignalMonitor(scene, cv2, numpy)
    lights.glyphs.classify = lambda mask: ("straight", 1.0)
    image = numpy.zeros((1000, 1000, 3), dtype=numpy.uint8)
    candidates = [{"color": "red", "box": [450, 100, 470, 120]}]
    with patch("traffic_light.classify_region", side_effect=lambda *args: copy.deepcopy(candidates)), patch("traffic_light.paired_lamps", side_effect=lambda lamps, config: lamps):
        lights.update(image, [], 0)
        check("red_requires_repeated_evidence", lights.update(image, [], 200_000_000)["directions"]["straight"]["state"], "red")
        candidates.clear()
        check("brief_signal_dropout_retains_red", lights.update(image, [], 400_000_000)["directions"]["straight"]["state"], "red")
        expired = lights.update(image, [], 1_200_000_000)
        expired = lights.update(image, [], 2_000_000_000)
        check("long_signal_dropout_expires_color", expired["directions"]["straight"]["state"], "unknown")
        check("long_signal_dropout_retains_context", expired["context_active"], True)
        candidates.append({"color": "green", "box": [450, 100, 470, 120]})
        lights.update(image, [], 2_200_000_000)
        lights.update(image, [], 2_400_000_000)
        candidates[0]["color"] = "red"
        check("new_red_never_keeps_previous_green", lights.update(image, [], 2_600_000_000)["directions"]["straight"]["state"], "unknown")

    output = Path(__file__).resolve().parent / "video_decision_runs" / "v2_rule_regression.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"source": "synthetic_regression_not_recognition_accuracy", "checks": checks},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [item for item in checks if not item["passed"]]
    print(f"Video behavior checks: {len(checks) - len(failures)}/{len(checks)} passed; report: {output}")
    for failure in failures:
        print(failure)
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
