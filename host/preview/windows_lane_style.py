import hashlib
import json
import math
import time
import warnings
from pathlib import Path

from lane_boundary_type import BoundaryTypeMonitor
from lane_detection import LaneMonitor


LABELS = ["background", "lane_solid", "lane_dashed", "other_marking"]
DECODER_VERSION = "lane-style-curves-v1"
DEFAULT_MODEL = Path(__file__).resolve().parent / "runs" / "lane_style" / "bdd_lraspp_v1_20261013" / "best.onnx"
STYLE_COLORS = {1: (0, 170, 255), 2: (255, 210, 0), 3: (210, 90, 210)}


def validate_contract(manifest):
    preprocess = manifest["preprocess"]
    expected = {"color_order": "RGB", "crop_normalized": [0, 0.35, 1, 1],
                "crop_top_rounding": "round(source_height*crop_top)", "resize": "stretch_to_input",
                "interpolation": "PIL_bilinear", "input_layout": "NCHW", "input_shape": [1, 3, 384, 640],
                "dtype": "float32", "normalization": "(RGB/255-mean)/std",
                "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225]}
    if manifest["family"] != "torchvision_lraspp_mobilenet_v3_large" or manifest["labels"] != LABELS:
        raise ValueError("Lane-style architecture or label order differs from the decoder contract")
    if any(preprocess.get(name) != value for name, value in expected.items()):
        raise ValueError("Lane-style preprocessing differs from the decoder contract")
    output = manifest["output"]
    if (output.get("name") != "lane_style_logits" or output.get("layout") != "NCHW" or
            output.get("shape") != [1, 4, 384, 640] or
            output.get("activation") != "none; softmax_axis_1_then_argmax" or
            output.get("coordinate_space") != "resized_road_ROI"):
        raise ValueError("Lane-style output differs from the decoder contract")
    if manifest["quantization"].get("precision") != "FP32" or manifest["quantization"].get("method") != "none":
        raise ValueError("This Windows lane-style decoder expects the FP32 export")


def preprocess_frame(image, numpy):
    from PIL import Image
    if image.ndim != 3 or image.shape[2] != 3 or image.dtype != numpy.uint8 or image.shape[0] < 4:
        raise ValueError("Lane-style input must be a nonempty uint8 BGR image")
    crop_top = round(image.shape[0] * 0.35)
    source = Image.fromarray(image[crop_top:, :, ::-1])
    resized = source.resize((640, 384), Image.Resampling.BILINEAR)
    rgb = numpy.asarray(resized, dtype=numpy.float32) / 255.0
    normalized = (rgb - numpy.asarray([0.485, 0.456, 0.406], dtype=numpy.float32)) / numpy.asarray([0.229, 0.224, 0.225], dtype=numpy.float32)
    return numpy.ascontiguousarray(normalized.transpose(2, 0, 1)[None]), crop_top


def decode_logits(logits, numpy):
    if logits.shape != (1, 4, 384, 640) or not numpy.all(numpy.isfinite(logits)):
        raise ValueError("Lane-style logits have an invalid shape or nonfinite values")
    exponentials = numpy.exp(logits[0] - logits[0].max(axis=0, keepdims=True))
    probabilities = exponentials / exponentials.sum(axis=0, keepdims=True)
    ordered = numpy.sort(probabilities, axis=0)
    return {"class_mask": probabilities.argmax(axis=0).astype(numpy.uint8),
            "confidence_mask": ordered[-1], "margin_mask": ordered[-1] - ordered[-2]}


class WindowsLaneStyleSegmenter:
    def __init__(self, model_path=None, backend="auto", threads=4):
        import numpy
        import onnxruntime
        self.numpy = numpy
        self.path = Path(model_path) if model_path else DEFAULT_MODEL
        self.manifest_path = self.path.with_suffix(".manifest.json")
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        validate_contract(self.manifest)
        self.model_sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        if self.model_sha256 != self.manifest["onnx"]["sha256"]:
            raise ValueError("Lane-style ONNX SHA256 differs from its manifest; re-export best.pt")
        if backend not in ("auto", "cpu", "directml"):
            raise ValueError("Lane-style backend must be auto, cpu or directml")
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = threads
        options.enable_mem_pattern = False
        options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
        use_directml = backend != "cpu" and "DmlExecutionProvider" in onnxruntime.get_available_providers()
        if backend == "directml" and not use_directml:
            raise RuntimeError("Lane-style DirectML provider is unavailable; install onnxruntime-directml")
        try:
            providers = [("DmlExecutionProvider", {"device_id": 0}), "CPUExecutionProvider"] if use_directml else ["CPUExecutionProvider"]
            self.session = onnxruntime.InferenceSession(str(self.path), sess_options=options, providers=providers)
            self._check_session()
            self.session.run(["lane_style_logits"], {self.input_name: numpy.zeros((1, 3, 384, 640), dtype=numpy.float32)})
        except Exception as error:
            if not use_directml or backend == "directml":
                raise RuntimeError(f"Lane-style inference initialization failed: {error}") from error
            warnings.warn(f"Lane-style GPU unavailable; using ONNX Runtime CPU: {error}", RuntimeWarning)
            self.session = onnxruntime.InferenceSession(str(self.path), sess_options=options, providers=["CPUExecutionProvider"])
            self._check_session()
            use_directml = False
        if backend == "auto" and not use_directml:
            warnings.warn("Lane-style inference uses ONNX Runtime CPU", RuntimeWarning)
        self.runtime = {"backend": "onnxruntime-directml" if use_directml else "onnxruntime-cpu",
                        "providers": self.session.get_providers(), "version": onnxruntime.__version__,
                        "input_shape": [1, 3, 384, 640], "decoder_version": DECODER_VERSION}

    def _check_session(self):
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if (len(inputs) != 1 or inputs[0].shape != [1, 3, 384, 640] or inputs[0].type != "tensor(float)" or
                len(outputs) != 1 or outputs[0].name != "lane_style_logits" or outputs[0].shape != [1, 4, 384, 640]):
            raise ValueError("Lane-style ONNX tensors differ from the manifest")
        self.input_name = inputs[0].name

    def segment(self, image):
        started = time.perf_counter()
        tensor, crop_top = preprocess_frame(image, self.numpy)
        inference_started = time.perf_counter()
        logits = self.session.run(["lane_style_logits"], {self.input_name: tensor})[0]
        inference_ms = (time.perf_counter() - inference_started) * 1000
        result = decode_logits(logits, self.numpy)
        result.update(crop_top=crop_top, source_shape=list(image.shape[:2]),
                      inference_ms=round(inference_ms, 2), segment_ms=round((time.perf_counter() - started) * 1000, 2),
                      source="BDD_LRASPP_lane_style_finetuned", model_version=self.manifest["model_version"],
                      weights_epoch=self.manifest["weights"]["epoch"], model_sha256=self.model_sha256,
                      decoder_version=DECODER_VERSION)
        return result


class LearnedBoundaryTypeMonitor(BoundaryTypeMonitor):
    def update(self, image, lane, detections, timestamp_ns, camera_center=0.5):
        self.observations = {tuple(boundary): evidence for boundary, evidence in
                             zip(lane["boundaries"], lane["boundary_style_observations"])}
        result = super().update(image, lane, detections, timestamp_ns, camera_center)
        result["boundary_type_source"] = "learned_lane_style_classes"
        return result

    def classify(self, image, boundary, detections):
        return dict(self.observations.get(tuple(boundary), {"type": "unknown", "reason": "model_style_uncertain"}))


class LaneStyleMonitor(LaneMonitor):
    def __init__(self, scene, cv2, numpy):
        super().__init__(scene, cv2, numpy)
        self.region = [0, 0.52, 1, 0.94]
        self.style_config = {"pixel_confidence": 0.45, "pixel_margin": 0.10, "style_fraction": 0.70,
                             "reference_row": 0.84, "confirm_frames": 3, "hold_ms": 350}
        self.style_config.update(scene.get("lane_style", {}))
        for name in ("pixel_confidence", "pixel_margin", "style_fraction"):
            value = self.style_config[name]
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 1:
                raise ValueError(f"Invalid lane_style.{name}")
        if not 0.78 <= self.style_config["reference_row"] <= 0.94:
            raise ValueError("lane_style.reference_row must be between 0.78 and 0.94")
        if not 1 <= self.style_config["confirm_frames"] <= 30 or not 0 <= self.style_config["hold_ms"] <= 1000:
            raise ValueError("Invalid lane-style confirmation count or hold duration")
        self.types = LearnedBoundaryTypeMonitor(cv2, numpy, confirm_frames=int(self.style_config["confirm_frames"]),
                                                require_corridor=False, hold_ms=self.style_config["hold_ms"])

    def reset(self):
        super().reset()
        if hasattr(self, "types"):
            self.types.reset()

    def _to_source(self, columns, rows, segmentation):
        numpy = self.numpy
        height, width = segmentation["source_shape"]
        crop_top = segmentation["crop_top"]
        return numpy.column_stack(((numpy.asarray(columns) + 0.5) * width / 640 - 0.5,
                                   crop_top + (numpy.asarray(rows) + 0.5) * (height - crop_top) / 384 - 0.5))

    def _regions(self, segmentation):
        regions = []
        for identifier in STYLE_COLORS:
            mask = (segmentation["class_mask"] == identifier).astype(self.numpy.uint8)
            contours, _ = self.cv2.findContours(mask, self.cv2.RETR_LIST, self.cv2.CHAIN_APPROX_SIMPLE)
            polygons = []
            for contour in sorted(contours, key=self.cv2.contourArea, reverse=True)[:100]:
                if self.cv2.contourArea(contour) < 5:
                    continue
                points = self.cv2.approxPolyDP(contour, 1.0, True)[:, 0]
                if len(points) >= 3:
                    polygons.append(self.numpy.rint(self._to_source(points[:, 0], points[:, 1], segmentation)).astype(int).tolist())
            regions.append({"class_id": identifier, "contours": polygons})
        return regions

    def _candidates(self, segmentation, detections):
        numpy = self.numpy
        classes = segmentation["class_mask"]
        confidence, margin = segmentation["confidence_mask"], segmentation["margin_mask"]
        support = ((classes == 1) | (classes == 2)) & (confidence >= self.style_config["pixel_confidence"]) & (margin >= self.style_config["pixel_margin"])
        height, width = segmentation["source_shape"]
        crop_top = segmentation["crop_top"]
        for detection in detections:
            if detection["class_id"] in (0, 1, 2, 3, 5, 7):
                left, top, right, bottom = detection["box"]
                first_column, last_column = max(0, math.floor(left * 640 / width) - 3), min(640, math.ceil(right * 640 / width) + 3)
                first_row = max(0, math.floor((top - crop_top) * 384 / (height - crop_top)) - 3)
                last_row = min(384, math.ceil((bottom - crop_top) * 384 / (height - crop_top)) + 3)
                if last_column > first_column and last_row > first_row:
                    support[first_row:last_row, first_column:last_column] = False
        first_row = max(0, round((height * self.region[1] - crop_top) * 384 / (height - crop_top)))
        last_row = min(383, round((height * self.region[3] - crop_top) * 384 / (height - crop_top)))
        paths = []
        for row in range(last_row, first_row - 1, -4):
            columns = numpy.flatnonzero(support[row])
            groups = numpy.split(columns, numpy.flatnonzero(numpy.diff(columns) > 1) + 1) if len(columns) else []
            observations = []
            for group in groups:
                if 2 <= len(group) <= 48:
                    observations.append((float(numpy.mean(group)), float(numpy.mean(confidence[row, group])),
                                         float(numpy.mean(classes[row, group] == 1))))
            matches = []
            for path_index, path in enumerate(paths):
                gap = path["rows"][-1] - row
                if gap > 28:
                    continue
                slope = ((path["columns"][-1] - path["columns"][-3]) /
                         (path["rows"][-1] - path["rows"][-3])) if len(path["rows"]) >= 3 else 0
                predicted = path["columns"][-1] - gap * slope
                for observation_index, observation in enumerate(observations):
                    distance = abs(observation[0] - predicted)
                    if distance <= 9 + gap * 0.30:
                        matches.append((distance, path_index, observation_index))
            used_paths, used_observations = set(), set()
            for _, path_index, observation_index in sorted(matches):
                if path_index in used_paths or observation_index in used_observations:
                    continue
                used_paths.add(path_index)
                used_observations.add(observation_index)
                column, score, solid_fraction = observations[observation_index]
                path = paths[path_index]
                for name, value in (("rows", row), ("columns", column), ("scores", score), ("solid", solid_fraction)):
                    path[name].append(value)
            for observation_index, (column, score, solid_fraction) in enumerate(observations):
                if observation_index not in used_observations:
                    paths.append({"rows": [row], "columns": [column], "scores": [score], "solid": [solid_fraction]})
        candidates = []
        reference_y = height * self.style_config["reference_row"]
        reference_row = (reference_y - crop_top + 0.5) * 384 / (height - crop_top) - 0.5
        for path in paths:
            rows, columns = numpy.asarray(path["rows"]), numpy.asarray(path["columns"])
            if len(rows) < 8 or numpy.ptp(rows) < 64 or reference_row > rows.max() + 24:
                continue
            coefficients = numpy.polyfit(rows / 384, columns, 2)
            residuals = numpy.abs(numpy.polyval(coefficients, rows / 384) - columns)
            inliers = residuals <= max(4, float(numpy.median(residuals)) * 2.5)
            if numpy.count_nonzero(inliers) < 8 or numpy.ptp(rows[inliers]) < 64:
                continue
            rows, columns = rows[inliers], columns[inliers]
            coefficients = numpy.polyfit(rows / 384, columns, 2)
            error = float(numpy.mean(numpy.abs(numpy.polyval(coefficients, rows / 384) - columns)))
            if error > 4.5 or reference_row > rows.max() + 24:
                continue
            sampled_rows = numpy.linspace(rows.min(), max(rows.max(), reference_row), 32)
            sampled_columns = numpy.polyval(coefficients, sampled_rows / 384)
            if sampled_columns.min() < -32 or sampled_columns.max() > 672:
                continue
            points = self._to_source(sampled_columns, sampled_rows, segmentation)
            bottom = float(self._to_source([numpy.polyval(coefficients, reference_row / 384)], [reference_row], segmentation)[0, 0])
            if not 0 <= bottom < width:
                continue
            quality = float(numpy.mean(numpy.asarray(path["scores"])[inliers]))
            solid_fraction = float(numpy.mean(numpy.asarray(path["solid"])[inliers]))
            style_fraction = max(solid_fraction, 1 - solid_fraction)
            style = ("solid" if solid_fraction >= 0.5 else "dashed") if style_fraction >= self.style_config["style_fraction"] else "unknown"
            top_x, top_y = points[0]
            slope = (bottom - top_x) / max(1, reference_y - top_y)
            candidates.append({"bottom": bottom, "top_x": float(top_x), "display_top": float(top_y),
                               "slope": slope, "intercept": float(top_x - slope * top_y), "quality": quality,
                               "curve": numpy.rint(points).astype(int).tolist(),
                               "evidence": {"type": style, "reason": "model_style_class" if style != "unknown" else "mixed_model_styles",
                                            "confidence": round(quality, 3), "style_fraction": round(style_fraction, 3),
                                            "sampled_rows": len(rows), "fit_error_pixels": round(error, 2)}})
        selected = []
        for candidate in sorted(candidates, key=lambda item: item["quality"], reverse=True):
            overlapping = next((other for other in selected if abs(candidate["bottom"] - other["bottom"]) <= width * 0.018), None)
            if overlapping is None:
                selected.append(candidate)
            elif candidate["evidence"]["type"] != overlapping["evidence"]["type"]:
                overlapping["evidence"] = dict(overlapping["evidence"], type="unknown", reason="conflicting_boundary_styles")
        return sorted(selected, key=lambda item: item["bottom"])

    def update(self, image, detections, timestamp_ns, segmentation):
        if timestamp_ns == self.last_timestamp_ns:
            return self.last_result
        if self.last_timestamp_ns is not None and (timestamp_ns < self.last_timestamp_ns or timestamp_ns - self.last_timestamp_ns > 500_000_000):
            self.reset()
        previous_result = self.last_result
        self.last_timestamp_ns = timestamp_ns
        height, width = image.shape[:2]
        candidates = self._candidates(segmentation, detections)
        current_tracks = self._track_boundaries(candidates, timestamp_ns, width, height)
        reference_y = round(height * self.style_config["reference_row"])
        boundaries = [[round(item["top_x"]), round(item["display_top"]), round(item["bottom"]), reference_y] for item in candidates]
        result = {"state": "unknown", "lane_count": None, "ego_lane": None, "confidence": 0.0,
                  "boundaries": boundaries, "boundary_curves": [item["curve"] for item in candidates],
                  "boundary_track_ids": [item["track_id"] for item in candidates],
                  "boundary_style_observations": [item["evidence"] for item in candidates],
                  "boundary_observation_bottom": reference_y, "ego_polygon": [], "ego_curve_polygon": [],
                  "ego_curves": {}, "offset": None, "changed": False, "reason": "no_ego_lane_pair",
                  "confidence_kind": "model_support_and_curve_geometry", "lane_position": "unknown",
                  "lateral_position": "unknown", "lane_change": "unknown", "lane_change_confidence": 0.0,
                  "lane_change_reason": "insufficient_tracking", "lane_change_event_seq": self.lane_change_event_seq,
                  "lane_change_timestamp_ns": self.lane_change_timestamp_ns, "alert": "none",
                  "perception_source": segmentation["source"], "style_regions": self._regions(segmentation),
                  "decoder_version": DECODER_VERSION, "decoder_thresholds": dict(self.style_config),
                  "camera_center": self.camera_center}
        center = width * self.camera_center
        left = max((item for item in candidates if item["bottom"] < center), key=lambda item: item["bottom"], default=None)
        right = min((item for item in candidates if item["bottom"] >= center), key=lambda item: item["bottom"], default=None)
        ego_pair, corridor_width = None, None
        if left and right:
            top = max(height * 0.60, left["display_top"], right["display_top"])
            corridor_bottom = min(left["curve"][-1][1], right["curve"][-1][1], height * self.region[3])
            rows = self.numpy.linspace(top, corridor_bottom, 24)
            curves = {}
            for side, candidate in (("left", left), ("right", right)):
                points = self.numpy.asarray(candidate["curve"])
                columns = self.numpy.interp(rows, points[:, 1], points[:, 0])
                curves[side] = self.numpy.rint(self.numpy.column_stack((columns, rows))).astype(int).tolist()
            widths = self.numpy.asarray(curves["right"])[:, 0] - self.numpy.asarray(curves["left"])[:, 0]
            proposed_width = right["bottom"] - left["bottom"]
            if (top < reference_y - height * 0.10 and 0.09 * width <= proposed_width <= 0.75 * width and
                    self.numpy.all(widths > width * 0.025) and widths[0] <= proposed_width * 1.05):
                ego_pair = (left["track_id"], right["track_id"])
                corridor_width = proposed_width
                result["ego_curves"] = curves
                result["ego_polygon"] = [curves["left"][0], curves["right"][0], curves["right"][-1], curves["left"][-1]]
                result["ego_curve_polygon"] = curves["left"] + list(reversed(curves["right"]))
                result["offset"] = round((center - (left["bottom"] + right["bottom"]) / 2) / corridor_width, 3)
                result["confidence"] = round(min(left["quality"], right["quality"]), 3)
                self.pending_count = self.pending_count + 1 if self.pending == ego_pair else 1
                self.pending = ego_pair
                result["state"] = "estimated" if self.pending_count >= self.confirm_frames else "unknown"
                result["reason"] = "current_model_lane_geometry" if result["state"] == "estimated" else "confirming_lane_geometry"
                result["lateral_position"] = "left" if result["offset"] < -0.18 else "right" if result["offset"] > 0.18 else "center"
            else:
                result["reason"] = "implausible_lane_geometry"
        if ego_pair is None:
            self.pending, self.pending_count = None, 0
        self._update_lane_change(current_tracks, ego_pair, timestamp_ns, center, corridor_width)
        self._update_corridor_crossing(result["offset"], corridor_width, timestamp_ns, result["confidence"])
        result["lane_change"] = "none" if ego_pair else "unknown"
        if self.lane_change_timestamp_ns is not None and 0 <= timestamp_ns - self.lane_change_timestamp_ns <= self.change_hold_ms * 1000000:
            result.update(lane_change=self.last_lane_change, lane_change_confidence=round(self.last_lane_change_confidence, 3),
                          lane_change_reason=self.last_lane_change_reason, alert="lane_change_" + self.last_lane_change)
        result.update(lane_change_event_seq=self.lane_change_event_seq, lane_change_timestamp_ns=self.lane_change_timestamp_ns)
        result = self.types.update(image, result, detections, timestamp_ns, self.camera_center)
        signature = lambda value: (value["state"], value.get("left_boundary_type"), value.get("right_boundary_type"), value["lane_change_event_seq"])
        result["changed"] = previous_result is None or signature(result) != signature(previous_result)
        self.last_result = result
        return result


def draw_style_regions(image, regions, cv2, numpy, scale):
    overlay = image.copy()
    for region in regions:
        contours = [numpy.rint(numpy.asarray(points) * scale).astype(numpy.int32) for points in region["contours"]]
        if contours:
            cv2.drawContours(overlay, contours, -1, STYLE_COLORS[region["class_id"]], cv2.FILLED)
    cv2.addWeighted(overlay, 0.45, image, 0.55, 0, image)


def draw_style_boundaries(image, lane, cv2, numpy, scale):
    for points in lane.get("boundary_curves", []):
        coordinates = numpy.rint(numpy.asarray(points) * scale).astype(numpy.int32)
        cv2.polylines(image, [coordinates], False, (170, 170, 170), 1, cv2.LINE_AA)
    boundaries = lane.get("boundaries", [])
    center = lane.get("camera_center", 0.5) * image.shape[1] / scale
    for side in ("left", "right"):
        indices = [index for index, boundary in enumerate(boundaries) if (boundary[2] < center) == (side == "left")]
        if not indices:
            continue
        index = max(indices, key=lambda identifier: boundaries[identifier][2]) if side == "left" else min(indices, key=lambda identifier: boundaries[identifier][2])
        coordinates = numpy.rint(numpy.asarray(lane["boundary_curves"][index]) * scale).astype(numpy.int32)
        observed = lane.get("boundary_type_evidence", {}).get(side, {}).get("observed_type", "unknown")
        color = STYLE_COLORS[1] if observed == "solid" else STYLE_COLORS[2] if observed == "dashed" else (180, 180, 180)
        cv2.polylines(image, [coordinates], False, color, 3, cv2.LINE_AA)
        anchor = coordinates[-1]
        cv2.putText(image, side.upper(), (max(0, int(anchor[0]) - 25), max(18, int(anchor[1]) - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2, cv2.LINE_AA)
