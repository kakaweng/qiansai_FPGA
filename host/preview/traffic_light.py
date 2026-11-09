import json
import math
from pathlib import Path


def load_scene(path):
    scene = json.loads(Path(path).read_text(encoding="utf-8"))
    region = scene["search_region"]
    if (len(region) != 4 or not all(math.isfinite(value) for value in region) or
            not 0 <= region[0] < region[2] <= 1 or not 0 <= region[1] < region[3] <= 1):
        raise ValueError("search_region must be normalized left, top, right, bottom")
    if (not 1 <= scene.get("confirm_frames", 3) <= 30 or
            not 0 < scene.get("evidence_timeout_ms", 500) <= 3000 or
            not 0 <= scene.get("min_detection_score", 0.25) <= 1):
        raise ValueError("invalid confirmation, timeout, or detection threshold")
    threshold_groups = {
        "color_thresholds": {
            "green_hue_min": (0, 179), "green_hue_max": (0, 179),
            "saturation_min": (0, 255), "value_min": (0, 255),
        },
        "lamp_geometry": {
            "min_area": (1, 10000), "max_area": (1, 10000),
            "min_aspect_ratio": (0.1, 10), "max_aspect_ratio": (0.1, 10),
            "min_fill_ratio": (0, 1), "min_dark_fraction": (0, 1),
        },
        "pair_geometry": {
            "max_horizontal_gap_ratio": (2, 100), "max_area_ratio": (1, 10),
        },
    }
    for group_name, thresholds in threshold_groups.items():
        configured = scene.get(group_name, {})
        if not isinstance(configured, dict):
            raise ValueError(f"{group_name} must be an object")
        for name, (minimum, maximum) in thresholds.items():
            if name in configured and (
                    not isinstance(configured[name], (int, float)) or
                    not math.isfinite(configured[name]) or
                    not minimum <= configured[name] <= maximum):
                raise ValueError(f"invalid {group_name}.{name}")
    color_thresholds = scene.get("color_thresholds", {})
    geometry = scene.get("lamp_geometry", {})
    if (color_thresholds.get("green_hue_min", 45) > color_thresholds.get("green_hue_max", 100) or
            geometry.get("min_area", 6) > geometry.get("max_area", 1400) or
            geometry.get("min_aspect_ratio", 0.5) > geometry.get("max_aspect_ratio", 1.8)):
        raise ValueError("minimum signal thresholds must not exceed maximum thresholds")
    return scene


def classify_region(image, box, cv2, numpy, scene=None):
    scene = scene or {}
    color_thresholds = scene.get("color_thresholds", {})
    geometry = scene.get("lamp_geometry", {})
    image_height, image_width = image.shape[:2]
    left, top, right, bottom = box
    left, top = max(0, int(left)), max(0, int(top))
    right, bottom = min(image_width, int(right)), min(image_height, int(bottom))
    if right <= left or bottom <= top:
        return []
    crop = image[top:bottom, left:right]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    saturation_min = int(color_thresholds.get("saturation_min", 110))
    value_min = int(color_thresholds.get("value_min", 160))
    masks = {
        "red": cv2.inRange(hsv, (0, saturation_min, value_min), (10, 255, 255)) |
               cv2.inRange(hsv, (170, saturation_min, value_min), (179, 255, 255)),
        "green": cv2.inRange(hsv, (int(color_thresholds.get("green_hue_min", 45)),
                                  saturation_min, value_min),
                             (int(color_thresholds.get("green_hue_max", 100)), 255, 255)),
    }
    if scene.get("detect_yellow", False):
        masks["yellow"] = cv2.inRange(hsv, (15, saturation_min, value_min), (38, 255, 255))
    candidates = []
    area_scale = image_width * image_height / (1920 * 1080)
    for color, mask in masks.items():
        component_count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        for component_index in range(1, component_count):
            offset_x, offset_y, width, height, area = (
                int(value) for value in stats[component_index]
            )
            if not (max(4, geometry.get("min_area", 6) * area_scale) <= area <=
                    geometry.get("max_area", 1400) * area_scale and
                    geometry.get("min_aspect_ratio", 0.5) <= width / height <=
                    geometry.get("max_aspect_ratio", 1.8)):
                continue
            fill_ratio = area / (width * height)
            compact = fill_ratio >= geometry.get("min_fill_ratio", 0.45)
            green_symbol = color == "green" and fill_ratio >= 0.25
            if not (compact or green_symbol):
                continue
            margin = max(3, int(round(max(width, height) * 0.3)))
            patch = crop[max(0, offset_y - margin):offset_y + height + margin,
                         max(0, offset_x - margin):offset_x + width + margin]
            dark_fraction = float(numpy.mean(numpy.max(patch, axis=2) < 100))
            required_dark = geometry.get("min_dark_fraction", 0.25)
            if not compact:
                required_dark = max(required_dark, 0.35)
            if dark_fraction < required_dark:
                continue
            candidates.append({
                "color": color, "area": int(area), "dark_fraction": dark_fraction,
                "shape_profile": "compact" if compact else "green_symbol",
                "box": (left + int(offset_x), top + int(offset_y),
                        left + int(offset_x + width), top + int(offset_y + height)),
            })
    return candidates


def paired_lamps(candidates, scene=None):
    geometry = (scene or {}).get("pair_geometry", {})
    accepted = set()
    for first_index, first in enumerate(candidates):
        for second_index in range(first_index + 1, len(candidates)):
            second = candidates[second_index]
            first_box, second_box = first["box"], second["box"]
            first_width, first_height = first_box[2] - first_box[0], first_box[3] - first_box[1]
            second_width, second_height = second_box[2] - second_box[0], second_box[3] - second_box[1]
            horizontal_gap = abs((first_box[0] + first_box[2] - second_box[0] - second_box[2]) / 2)
            vertical_gap = abs((first_box[1] + first_box[3] - second_box[1] - second_box[3]) / 2)
            if (2 <= horizontal_gap / max(first_width, second_width) <=
                    geometry.get("max_horizontal_gap_ratio", 20) and
                    vertical_gap <= max(6, max(first_height, second_height) * 0.9) and
                    max(first["area"], second["area"]) / min(first["area"], second["area"]) <=
                    geometry.get("max_area_ratio", 2)):
                accepted.update((first_index, second_index))
    return [candidate for index, candidate in enumerate(candidates) if index in accepted]


class TrafficLightMonitor:
    def __init__(self, scene, cv2, numpy, allow_roi_fallback=False):
        self.scene, self.cv2, self.numpy = scene, cv2, numpy
        self.allow_roi_fallback = allow_roi_fallback
        self.reset()

    def reset(self):
        self.state = self.pending = "unknown"
        self.pending_count = 0
        self.last_timestamp_ns = self.last_evidence_ns = None
        self.last_result = None

    def update(self, image, detections, timestamp_ns):
        if self.last_timestamp_ns == timestamp_ns:
            return self.last_result
        timeout_ns = self.scene.get("evidence_timeout_ms", 500) * 1000000
        if self.last_timestamp_ns is not None and (
                timestamp_ns < self.last_timestamp_ns or
                timestamp_ns - self.last_timestamp_ns > timeout_ns):
            self.reset()
        self.last_timestamp_ns = timestamp_ns
        height, width = image.shape[:2]
        normalized_region = self.scene["search_region"]
        region = tuple(round(value * (width if index % 2 == 0 else height))
                       for index, value in enumerate(normalized_region))
        candidates = []
        for detection in detections:
            if detection["class_id"] != 9 or detection["score"] < self.scene.get("min_detection_score", 0.25):
                continue
            left, top, right, bottom = detection["box"]
            center_x, center_y = (left + right) / 2, (top + bottom) / 2
            if not (region[0] <= center_x <= region[2] and region[1] <= center_y <= region[3]):
                continue
            margin = max(6, round(min(right - left, bottom - top) * 0.3))
            candidates.extend(classify_region(image, (
                max(region[0], left - margin), max(region[1], top - margin),
                min(region[2], right + margin), min(region[3], bottom + margin)),
                self.cv2, self.numpy, self.scene))
        source = "yolo" if candidates else "none"
        if not candidates and self.allow_roi_fallback:
            candidates = classify_region(image, region, self.cv2, self.numpy, self.scene)
            if self.scene.get("fallback_require_pair", False):
                candidates = paired_lamps(candidates, self.scene)
            source = "scene_roi" if candidates else "none"
        colors = {candidate["color"] for candidate in candidates}
        raw_state = next(iter(colors)) if len(colors) == 1 else "unknown"
        previous_state = self.state
        if raw_state == "unknown":
            self.pending, self.pending_count = "unknown", 0
            if (len(colors) > 1 or self.last_evidence_ns is None or
                    timestamp_ns - self.last_evidence_ns > timeout_ns):
                self.state = "unknown"
        else:
            self.last_evidence_ns = timestamp_ns
            self.pending_count = self.pending_count + 1 if self.pending == raw_state else 1
            self.pending = raw_state
            if self.state != raw_state:
                self.state = "unknown"
                if self.pending_count >= self.scene.get("confirm_frames", 3):
                    self.state = raw_state
        action = {"red": "STOP", "green": "GO_SIGNAL_ONLY", "unknown": "WAIT"}[self.state]
        self.last_result = {
            "state": self.state, "raw_state": raw_state, "action": action,
            "source": source, "region": region, "candidates": candidates,
            "changed": self.state != previous_state,
            "reason": "conflicting_colors" if len(colors) > 1 else
                      "no_signal_evidence" if not colors else
                      "confirming_color" if self.state == "unknown" else "color_evidence",
        }
        return self.last_result


def draw_traffic_light(image, result, cv2, scale=1.0, header_y=0):
    colors = {"red": (0, 0, 255), "green": (0, 255, 0), "yellow": (0, 200, 255), "unknown": (0, 200, 255)}
    for candidate in result["candidates"]:
        left, top, right, bottom = (round(value * scale) for value in candidate["box"])
        cv2.rectangle(image, (left, top), (right, bottom), colors[candidate["color"]], 2)
    if header_y is None:
        return image
    text = f"Signal {result['state'].upper()} | {result['action']} | {result['source']}"
    cv2.rectangle(image, (0, header_y), (image.shape[1], header_y + 40), (0, 0, 0), -1)
    cv2.putText(image, text, (10, header_y + 27), cv2.FONT_HERSHEY_SIMPLEX,
                0.65, colors[result["state"]], 2)
    return image
