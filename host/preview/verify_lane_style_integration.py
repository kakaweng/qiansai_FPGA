import argparse
import copy
import json
from pathlib import Path

import cv2
import numpy

from video_behavior import VideoBehaviorMonitor
from video_scene_analysis import corridor_at
from windows_lane_style import DEFAULT_MODEL, LaneStyleMonitor, decode_logits, preprocess_frame, validate_contract


ROOT = Path(__file__).resolve().parent


def synthetic_segmentation():
    classes = numpy.zeros((384, 640), dtype=numpy.uint8)
    rows = numpy.arange(104, 353)
    curvature = ((rows - 104) / 248) ** 2
    for identifier, columns in ((1, 270 - 140 * curvature), (2, 370 + 140 * curvature),
                                (3, numpy.full(len(rows), 50))):
        points = numpy.rint(numpy.column_stack((columns, rows))).astype(numpy.int32)
        cv2.polylines(classes, [points], False, identifier, 5)
    return {"class_mask": classes, "confidence_mask": numpy.full(classes.shape, 0.95, dtype=numpy.float32),
            "margin_mask": numpy.full(classes.shape, 0.80, dtype=numpy.float32),
            "crop_top": 252, "source_shape": [720, 1280], "source": "synthetic_contract_check"}


def run_checks():
    checks = []

    def check(name, condition):
        checks.append((name, bool(condition)))

    def rejected(callable_value):
        try:
            callable_value()
        except ValueError:
            return True
        return False

    image = numpy.full((721, 1280, 3), (10, 20, 30), dtype=numpy.uint8)
    image[:252] = 255
    tensor, crop_top = preprocess_frame(image, numpy)
    expected = (numpy.asarray([30, 20, 10], dtype=numpy.float32) / 255 - numpy.asarray([0.485, 0.456, 0.406])) / numpy.asarray([0.229, 0.224, 0.225])
    check("preprocess_uses_RGB_and_ImageNet_normalization", numpy.allclose(tensor[0, :, 100, 100], expected, atol=1e-6))
    check("preprocess_uses_contract_shape_and_rounding", tensor.shape == (1, 3, 384, 640) and crop_top == 252)
    manifest = json.loads(DEFAULT_MODEL.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    validate_contract(manifest)
    changed = copy.deepcopy(manifest)
    changed["labels"][1:3] = reversed(changed["labels"][1:3])
    check("swapped_style_label_order_is_rejected", rejected(lambda: validate_contract(changed)))
    changed = copy.deepcopy(manifest)
    changed["preprocess"]["resize"] = "letterbox"
    check("old_letterbox_preprocess_is_rejected", rejected(lambda: validate_contract(changed)))
    logits = numpy.zeros((1, 4, 384, 640), dtype=numpy.float32)
    logits[:, 2] = 10
    decoded = decode_logits(logits, numpy)
    check("channel_two_decodes_as_dashed", numpy.all(decoded["class_mask"] == 2))
    ambiguous = decode_logits(numpy.zeros_like(logits), numpy)
    check("equal_logits_have_zero_confidence_margin", numpy.all(ambiguous["margin_mask"] == 0))
    logits[0, 2, 0, 0] = numpy.nan
    check("nonfinite_logits_are_rejected", rejected(lambda: decode_logits(logits, numpy)))

    image = numpy.full((720, 1280, 3), 60, dtype=numpy.uint8)
    segmentation = synthetic_segmentation()
    monitor = LaneStyleMonitor({}, cv2, numpy)
    first = monitor.update(image, [], 0, segmentation)
    monitor.update(image, [], 100_000_000, segmentation)
    confirmed = monitor.update(image, [], 200_000_000, segmentation)
    check("current_frame_requires_temporal_confirmation", first["right_boundary_type"] == "unknown")
    check("continuous_dashed_class_is_not_reclassified_as_solid", confirmed["right_boundary_type"] == "dashed")
    check("left_and_right_are_associated_correctly", confirmed["left_boundary_type"] == "solid")
    check("other_marking_is_excluded_from_boundaries", len(confirmed["boundaries"]) == 2)
    check("curved_ego_corridor_is_estimated", confirmed["state"] == "estimated" and len(confirmed["ego_curve_polygon"]) == 48)
    check("lane_count_is_not_required", confirmed["lane_count"] is None and confirmed["ego_lane"] is None)
    curve_row = confirmed["ego_curves"]["left"][12][1]
    left, _, source = corridor_at(confirmed, curve_row, 1280, 720)
    check("object_corridor_uses_curve_geometry", abs(left - confirmed["ego_curves"]["left"][12][0]) < 2 and source == "observed_lane_geometry")
    check("observation_is_json_serializable", isinstance(json.dumps(confirmed), str))

    blank = dict(segmentation, class_mask=numpy.zeros((384, 640), dtype=numpy.uint8))
    held = monitor.update(image, [], 300_000_000, blank)
    adjacent = {side: {"exists": True, "visible_clear": True} for side in ("left", "right")}
    check("missing_boundary_can_display_a_marked_recent_result", held["boundary_type_evidence"]["right"].get("held"))
    check("recent_dashed_result_cannot_grant_a_new_request", VideoBehaviorMonitor().change_evaluation("right", held, adjacent, "NORMAL")["status"] == "unknown")
    expired = monitor.update(image, [], 600_000_000, blank)
    check("missing_boundary_expires_to_unknown", expired["right_boundary_type"] == "unknown" and expired["ego_polygon"] == [])

    monitor.reset()
    for timestamp_ns in (0, 100_000_000, 200_000_000):
        monitor.update(image, [], timestamp_ns, segmentation)
    changed = dict(segmentation, class_mask=segmentation["class_mask"].copy())
    changed["class_mask"][changed["class_mask"] == 2] = 1
    opposite = monitor.update(image, [], 300_000_000, changed)
    check("current_solid_evidence_clears_an_old_dashed_confirmation", opposite["right_boundary_type"] == "unknown" and not opposite["boundary_type_evidence"]["right"].get("held"))
    rewind = monitor.update(image, [], 0, segmentation)
    check("replay_resets_temporal_history", rewind["left_boundary_type"] == "unknown")
    uncertain = dict(segmentation, confidence_mask=numpy.full((384, 640), 0.40, dtype=numpy.float32))
    uncertain_result = LaneStyleMonitor({}, cv2, numpy).update(image, [], 0, uncertain)
    check("low_confidence_pixels_do_not_create_a_corridor", uncertain_result["boundaries"] == [] and uncertain_result["state"] == "unknown")
    far = dict(segmentation, class_mask=segmentation["class_mask"].copy())
    far["class_mask"][245:] = 0
    check("distant_paths_are_not_extrapolated_into_near_boundaries", LaneStyleMonitor({}, cv2, numpy).update(image, [], 0, far)["boundaries"] == [])
    occluded = LaneStyleMonitor({}, cv2, numpy).update(image, [{"class_id": 2, "box": [0, 370, 1280, 720]}], 0, segmentation)
    check("vehicle_occlusion_does_not_create_a_boundary", occluded["boundaries"] == [])
    single = dict(segmentation, class_mask=segmentation["class_mask"].copy())
    single["class_mask"][single["class_mask"] == 2] = 0
    monitor = LaneStyleMonitor({}, cv2, numpy)
    for timestamp_ns in (0, 100_000_000, 200_000_000):
        result = monitor.update(image, [], timestamp_ns, single)
    check("single_side_can_confirm_independently", result["left_boundary_type"] == "solid" and result["right_boundary_type"] == "unknown" and result["state"] == "unknown")
    mixed = dict(segmentation, class_mask=segmentation["class_mask"].copy())
    mixed["class_mask"][104:228][mixed["class_mask"][104:228] == 2] = 1
    monitor = LaneStyleMonitor({}, cv2, numpy)
    for timestamp_ns in (0, 100_000_000, 200_000_000):
        result = monitor.update(image, [], timestamp_ns, mixed)
    check("mixed_styles_are_not_forced_into_a_dashed_label", result["right_boundary_type"] == "unknown")
    parallel = dict(segmentation, class_mask=segmentation["class_mask"].copy())
    rows = numpy.arange(104, 353)
    columns = 360 + 140 * ((rows - 104) / 248) ** 2
    cv2.polylines(parallel["class_mask"], [numpy.rint(numpy.column_stack((columns, rows))).astype(numpy.int32)], False, 1, 5)
    monitor = LaneStyleMonitor({}, cv2, numpy)
    for timestamp_ns in (0, 100_000_000, 200_000_000):
        result = monitor.update(image, [], timestamp_ns, parallel)
    check("nearby_conflicting_styles_do_not_grant_crossing", result["right_boundary_type"] == "unknown")
    return checks


def smoke_inference(backend):
    from PIL import Image
    from video_driving import VideoDrivingPipeline, VideoRenderer, load_configuration
    from windows_object_detector import WindowsObjectDetector
    dataset = json.loads((ROOT / "datasets/bdd_lane_style_v1/dataset.json").read_text(encoding="utf-8"))
    sample = next(entry for entry in dataset["samples"] if entry["split"] == "val")
    with Image.open(sample["source_image"]) as source:
        image = numpy.asarray(source.convert("RGB"))[:, :, ::-1].copy()
    scene, behavior = load_configuration(ROOT / "windows_video_112507.json")
    detector = WindowsObjectDetector(ROOT / "models/yolov5s.onnx", backend=backend)
    pipeline = VideoDrivingPipeline(detector, scene, behavior, cv2, numpy, backend=backend)
    result = pipeline.process(image, 1, 0)
    assert result["lane_model"]["weights_epoch"] == 24
    assert result["lane"]["perception_source"] == "BDD_LRASPP_lane_style_finetuned"
    rendered = VideoRenderer(cv2, numpy, 1280).render(image, result)
    output = DEFAULT_MODEL.parent / "integration_preview.jpg"
    if not cv2.imwrite(str(output), rendered):
        raise OSError("Cannot save lane-style integration preview")
    output.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"backend": pipeline.lane_segmenter.runtime["backend"], "epoch": result["lane_model"]["weights_epoch"],
                      "lane_inference_ms": result["lane_model"]["inference_ms"], "processing_ms": result["processing_ms"],
                      "boundaries": len(result["lane"]["boundaries"]), "preview": str(output)}, ensure_ascii=True))


def main():
    parser = argparse.ArgumentParser(description="Verify learned lane-style decoding and optional BDD still-image integration")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--backend", choices=("auto", "cpu", "directml"), default="auto")
    arguments = parser.parse_args()
    checks = run_checks()
    failures = [name for name, passed in checks if not passed]
    print(f"Lane-style integration checks: {len(checks) - len(failures)}/{len(checks)} passed")
    for name in failures:
        print(name)
    if failures:
        return 1
    if arguments.smoke:
        smoke_inference(arguments.backend)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
