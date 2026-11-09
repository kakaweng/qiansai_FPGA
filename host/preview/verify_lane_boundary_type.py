import cv2
import numpy

from lane_boundary_type import BoundaryTypeMonitor
from video_behavior import VideoBehaviorMonitor


def run_checks():
    checks = []

    def check(name, actual, expected):
        checks.append({"name": name, "actual": actual, "expected": expected, "passed": actual == expected})

    monitor = BoundaryTypeMonitor(cv2, numpy, require_corridor=False, hold_ms=800)
    image = numpy.full((480, 640, 3), 60, dtype=numpy.uint8)
    solid = image.copy()
    dashed = image.copy()
    cv2.line(solid, (240, 240), (240, 470), (240, 240, 240), 5)
    for start, end in ((250, 280), (330, 370), (425, 455)):
        cv2.line(dashed, (240, start), (240, end), (240, 240, 240), 5)
    boundary = [240, 240, 240, 470]
    lane = {"state": "unknown", "boundaries": [boundary], "boundary_track_ids": [1]}
    check("raw_solid", monitor.classify(solid, boundary, [])["type"], "solid")
    check("raw_dashed", monitor.classify(dashed, boundary, [])["type"], "dashed")
    check("occlusion_is_not_dash_gap", monitor.classify(solid, boundary,
          [{"class_id": 2, "box": [200, 280, 270, 440]}])["type"], "unknown")

    first = monitor.update(dashed, lane, [], 0)
    monitor.update(dashed, lane, [], 100_000_000)
    confirmed = monitor.update(dashed, lane, [], 200_000_000)
    held = monitor.update(image, lane, [], 400_000_000)
    monitor.update(image, lane, [], 700_000_000)
    expired = monitor.update(image, lane, [], 1_100_000_000)
    conflict = monitor.update(solid, lane, [], 1_200_000_000)
    solid_pending = monitor.update(solid, lane, [], 1_300_000_000)
    solid_confirmed = monitor.update(solid, lane, [], 1_400_000_000)

    check("two_frames_are_not_confirmed", first["left_boundary_type"], "unknown")
    check("three_frames_confirm_dashed", confirmed["left_boundary_type"], "dashed")
    check("unknown_frame_can_display_recent_hold", held["left_boundary_type"], "dashed")
    check("hold_is_marked_as_stale", held["boundary_type_evidence"]["left"]["held"], True)
    check("expired_hold_returns_unknown", expired["left_boundary_type"], "unknown")
    check("opposite_type_clears_old_confirmation", conflict["left_boundary_type"], "unknown")
    check("opposite_type_requires_confirmation", solid_pending["left_boundary_type"], "unknown")
    check("three_solid_frames_confirm_solid", solid_confirmed["left_boundary_type"], "solid")
    check("single_side_does_not_require_lane_pair", confirmed["state"], "unknown")
    check("no_hold_timestamp_refresh", held["boundary_type_evidence"]["left"]["age_ms"], 200.0)

    monitor.reset()
    for timestamp in (0, 100_000_000, 200_000_000):
        monitor.update(dashed, lane, [], timestamp)
    conflict = monitor.update(solid, lane, [], 300_000_000)
    check("solid_appearance_clears_confirmed_dash_immediately", conflict["left_boundary_type"], "unknown")

    monitor.reset()
    for timestamp in (0, 100_000_000, 200_000_000):
        monitor.update(dashed, lane, [], timestamp)
    changed_id = monitor.update(image, dict(lane, boundary_track_ids=[2]), [], 300_000_000)
    check("new_boundary_id_does_not_inherit_dash", changed_id["left_boundary_type"], "unknown")

    monitor.reset()
    for timestamp in (0, 100_000_000, 200_000_000):
        monitor.update(dashed, lane, [], timestamp)
    changed_event = monitor.update(image, dict(lane, lane_change_event_seq=1), [], 300_000_000)
    check("lane_change_event_clears_old_side", changed_event["left_boundary_type"], "unknown")

    monitor.reset()
    monitor.update(dashed, lane, [], 0)
    monitor.update(dashed, lane, [], 100_000_000)
    monitor.update(image, lane, [], 200_000_000)
    resumed = monitor.update(dashed, lane, [], 300_000_000)
    check("short_gap_preserves_pending_real_observations", resumed["left_boundary_type"], "dashed")
    rewind = monitor.update(dashed, lane, [], 0)
    check("rewind_resets_confirmation", rewind["left_boundary_type"], "unknown")

    monitor.reset()
    monitor.update(dashed, lane, [], 0)
    monitor.update(dashed, lane, [], 100_000_000)
    for timestamp in (200_000_000, 400_000_000, 600_000_000, 800_000_000):
        monitor.update(image, lane, [], timestamp)
    resumed = monitor.update(dashed, lane, [], 900_000_000)
    check("long_gap_cannot_resurrect_pending_confirmation", resumed["left_boundary_type"], "unknown")

    original = BoundaryTypeMonitor(cv2, numpy, require_corridor=False)
    original.update(dashed, lane, [], 0)
    original.update(dashed, lane, [], 100_000_000)
    original.update(image, lane, [], 200_000_000)
    check("default_retains_consecutive_confirmation", original.update(dashed, lane, [], 300_000_000)["left_boundary_type"], "unknown")

    behavior = VideoBehaviorMonitor()
    adjacent = {side: {"exists": True, "visible_clear": True} for side in ("left", "right")}
    held_lane = dict(held, right_boundary_type="dashed")
    evaluation = behavior.change_evaluation("left", held_lane, adjacent, "NORMAL")
    check("held_dash_never_grants_change", evaluation["status"], "unknown")
    check("held_dash_does_not_assert_prohibition", evaluation["line_constraint"], "unknown")
    check("other_side_remains_independent", behavior.change_evaluation("right", held_lane, adjacent, "NORMAL")["status"], "visible_clear")
    check("stop_still_blocks_change", behavior.change_evaluation("left", held_lane, adjacent, "STOP")["status"], "forbidden")
    return checks


def main():
    checks = run_checks()
    passed = sum(check["passed"] for check in checks)
    print(f"Lane boundary checks: {passed}/{len(checks)} passed")
    for check in checks:
        if not check["passed"]:
            print(check)
    raise SystemExit(0 if passed == len(checks) else 1)


if __name__ == "__main__":
    main()
