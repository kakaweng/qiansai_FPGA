import copy
import json
import math
from pathlib import Path

from driving_decision import DrivingDecisionMonitor


def load_decision_config(path):
    config = copy.deepcopy(path) if isinstance(path, dict) else json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("decision config must be an object")
    if "evaluation_only" in config:
        raise ValueError("decision config cannot use an evaluation timeline")
    for field in ("signal_required", "signal_bound", "pedestrian_use_lane_corridor"):
        if not isinstance(config.get(field, field == "signal_required"), bool):
            raise ValueError(f"{field} must be boolean")
    if config.get("signal_required_mode", "fixed") not in ("fixed", "detected"):
        raise ValueError("signal_required_mode must be fixed or detected")
    for field in ("pedestrian_conflict_roi", "signal_detection_roi"):
        region = config.get(field)
        if region is not None and (
                not isinstance(region, list) or len(region) != 4 or
                not all(isinstance(value, (int, float)) and math.isfinite(value) for value in region) or
                not 0 <= region[0] < region[2] <= 1 or not 0 <= region[1] < region[3] <= 1):
            raise ValueError(f"{field} must be normalized left, top, right, bottom")
    for field, default in (("person_min_score", 0.25), ("person_overlap_ratio", 0.1),
                           ("signal_min_score", 0.25)):
        value = config.get(field, default)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{field} must be between zero and one")
    if config.get("requested_maneuver", "keep_lane") not in (
            "keep_lane", "change_left", "change_right", "turn_left", "turn_right"):
        raise ValueError("invalid requested_maneuver")
    if not isinstance(config.get("decision", {}), dict):
        raise ValueError("decision options must be an object")
    return config


class DecisionPreviewAdapter:
    def __init__(self, config, log_path=None):
        self.config = config
        self.monitor = DrivingDecisionMonitor(config.get("decision", {}))
        self.log_path = Path(log_path) if log_path else None
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.last_observation = None
        self.last_result = None
        self.session_id = None
        self.input_timeout_ns = config.get("decision", {}).get("input_timeout_ns", 500000000)
        self.signal_context_seen = False
        self.last_color_evidence_ns = None

    def signal_observation(self, detections, traffic, width, height, fresh, timestamp_ns):
        candidates = traffic.get("candidates", []) if fresh else []
        region = traffic.get("region")
        if region is None and self.config.get("signal_detection_roi") is not None:
            region = [value * (width if index % 2 == 0 else height)
                      for index, value in enumerate(self.config["signal_detection_roi"])]
        lamp_detected = bool(candidates)
        if fresh and region is not None:
            for detection in detections:
                if (detection.get("class_id") != 9 or detection.get("tracked", False) or
                        detection.get("score", 0) < self.config.get("signal_min_score", 0.25)):
                    continue
                box = detection.get("box", ())
                if len(box) == 4:
                    center_x, center_y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                    lamp_detected |= region[0] <= center_x <= region[2] and region[1] <= center_y <= region[3]
        detected_mode = self.config.get("signal_required_mode", "fixed") == "detected"
        if detected_mode and lamp_detected:
            self.signal_context_seen = True
        if candidates:
            self.last_color_evidence_ns = timestamp_ns
        required = self.config.get("signal_required", True) or (detected_mode and self.signal_context_seen)
        bound = self.config.get("signal_bound", False)
        state = traffic.get("state", "unknown") if fresh and bound else "unknown"
        color_fresh = (self.last_color_evidence_ns is not None and
                       0 <= timestamp_ns - self.last_color_evidence_ns <= self.input_timeout_ns)
        if detected_mode and not color_fresh:
            state = "unknown"
        return {"required": required, "state": state, "bound": bound,
                "source": "observed" if fresh and traffic else "unavailable",
                "bound_source": "scene_configuration",
                "required_source": "observed_latched" if detected_mode and self.signal_context_seen else "scene_configuration",
                "context_seen": self.signal_context_seen, "lamp_detected": lamp_detected,
                "context_reason": "signal_context_latched" if detected_mode and self.signal_context_seen else
                                  "scene_initial_signal_requirement" if detected_mode else "fixed_scene_requirement",
                "last_color_evidence_ns": self.last_color_evidence_ns}

    def pedestrian_observation(self, detections, width, height, fresh, lane=None):
        region = self.config.get("pedestrian_conflict_roi")
        result = {"conflict": "unknown", "source": "unavailable", "roi_source": "scene_configuration"}
        if not fresh or region is None:
            return result
        left, top, right, bottom = (region[0] * width, region[1] * height,
                                   region[2] * width, region[3] * height)
        polygon = (lane or {}).get("ego_polygon", [])
        use_corridor = self.config.get("pedestrian_use_lane_corridor", False)
        valid_polygon = (use_corridor and (lane or {}).get("state") == "estimated" and
                         isinstance(polygon, (list, tuple)) and len(polygon) == 4 and
                         all(isinstance(point, (list, tuple)) and len(point) == 2 and
                             all(isinstance(value, (int, float)) and math.isfinite(value) for value in point) and
                             0 <= point[0] <= width and 0 <= point[1] <= height for point in polygon))
        if valid_polygon:
            turns = [(polygon[(index + 1) % 4][0] - polygon[index][0]) *
                     (polygon[(index + 2) % 4][1] - polygon[(index + 1) % 4][1]) -
                     (polygon[(index + 1) % 4][1] - polygon[index][1]) *
                     (polygon[(index + 2) % 4][0] - polygon[(index + 1) % 4][0]) for index in range(4)]
            valid_polygon = all(value > 0 for value in turns) or all(value < 0 for value in turns)
        method = "lane_corridor" if valid_polygon else "roi_fallback" if use_corridor else "roi"
        result.update(conflict="clear", source="observed_" + method if use_corridor else "observed",
                      conflict_method=method, region=list(region), conflict_boxes=[], corridor_used=bool(valid_polygon))
        if valid_polygon:
            result["corridor_polygon"] = polygon
        for detection in detections:
            if (detection.get("class_id") != 0 or detection.get("tracked", False) or
                    detection.get("score", 0) < self.config.get("person_min_score", 0.25)):
                continue
            box = detection.get("box", ())
            if len(box) != 4:
                continue
            person_left, person_top, person_right, person_bottom = box
            person_area = max(0, person_right - person_left) * max(0, person_bottom - person_top)
            if not person_area:
                continue
            foot_x = (person_left + person_right) / 2
            foot_inside = left <= foot_x <= right and top <= person_bottom <= bottom
            overlap = (max(0, min(right, person_right) - max(left, person_left)) *
                       max(0, min(bottom, person_bottom) - max(top, person_top)))
            conflict = foot_inside or (overlap > 0 and overlap / person_area >= self.config.get("person_overlap_ratio", 0.1))
            if conflict and valid_polygon:
                intersections = []
                for first, second in zip(polygon, polygon[1:] + polygon[:1]):
                    if first[1] != second[1] and min(first[1], second[1]) <= person_bottom <= max(first[1], second[1]):
                        intersections.append(first[0] + (person_bottom - first[1]) *
                                             (second[0] - first[0]) / (second[1] - first[1]))
                if len(intersections) >= 2:
                    corridor_left, corridor_right = min(intersections), max(intersections)
                    foot_overlap = max(0, min(corridor_right, person_right) - max(corridor_left, person_left))
                    conflict = (corridor_left <= foot_x <= corridor_right or
                                foot_overlap > 0 and foot_overlap / (person_right - person_left) >=
                                self.config.get("person_overlap_ratio", 0.1))
                else:
                    result.update(source="observed_roi_fallback", conflict_method="roi_fallback_outside_corridor_span")
            if conflict:
                result["conflict"] = "present"
                result["conflict_boxes"].append(list(box))
        return result

    def update(self, record, traffic, lane, fresh, session_id, *, width=1920, height=1080):
        if (type(width) is not int or type(height) is not int or width <= 0 or height <= 0):
            raise ValueError("image dimensions must be positive integers")
        session_id = str(session_id)
        record = record or {}
        previous = self.last_observation or {}
        timestamp_ns = record.get("timestamp_ns", previous.get("timestamp_ns", 0))
        frame_seq = record.get("sequence", previous.get("frame_seq", 0))
        timeline_reset = (fresh and bool(record) and self.last_observation is not None and
                          (timestamp_ns < self.last_observation["timestamp_ns"] or
                           frame_seq < self.last_observation["frame_seq"]))
        if session_id != self.session_id or timeline_reset:
            self.monitor.reset()
            self.session_id = session_id
            self.signal_context_seen = False
            self.last_color_evidence_ns = None
        matched = record.get("kind") == "combined" and record.get("inference") is not None
        fresh = bool(fresh and matched)
        traffic, lane = traffic or {}, lane or {}
        detections = record.get("inference", {}).get("detections", []) if fresh else []
        signal = self.signal_observation(detections, traffic, width, height, fresh, timestamp_ns)
        pedestrian = self.pedestrian_observation(detections, width, height, fresh, lane)
        boundary_source = lane.get("boundary_type_source", "unavailable") if fresh else "unavailable"
        boundary_types = {side: lane.get(f"{side}_boundary_type", "unknown")
                          if boundary_source != "unavailable" else "unknown" for side in ("left", "right")}
        crossed_source = lane.get("crossed_boundary_type_source", "unavailable") if fresh else "unavailable"
        crossed_type = lane.get("crossed_boundary_type", "unknown") if crossed_source != "unavailable" else "unknown"
        observation = {
            "session_id": session_id, "frame_seq": frame_seq,
            "timestamp_ns": timestamp_ns, "fresh": fresh, "source": "observed",
            "signal": signal,
            "pedestrian": pedestrian,
            "lane": {"state": lane.get("state", "unknown") if fresh else "unknown",
                     "left_boundary_type": boundary_types["left"], "right_boundary_type": boundary_types["right"],
                     "boundary_type_source": boundary_source,
                     "left_adjacent_exists": None, "right_adjacent_exists": None,
                     "left_adjacent_clear": None, "right_adjacent_clear": None,
                     "source": "observed" if fresh and lane else "unavailable"},
            "request": {"maneuver": self.config.get("requested_maneuver", "keep_lane"),
                        "source": "scene_configuration"},
            "turn": {"left": "unknown", "right": "unknown", "source": "unavailable"},
            "actual": {"moving": None, "source": "unavailable"},
            "lane_change": {"event_seq": lane.get("lane_change_event_seq", 0),
                            "direction": lane.get("lane_change", "unknown") if fresh else "unknown",
                            "timestamp_ns": lane.get("lane_change_timestamp_ns"),
                            "crossed_boundary_type": crossed_type, "crossed_boundary_type_source": crossed_source,
                            "source": "observed" if fresh and lane else "unavailable"},
            "input_sources": {"signal_state": "observed" if fresh and traffic else "unavailable",
                              "signal_binding": "scene_configuration", "signal_requirement": signal["required_source"],
                              "pedestrian": pedestrian["source"],
                              "pedestrian_region": "scene_configuration", "lane_geometry": "observed" if fresh and lane else "unavailable",
                              "boundary_type": boundary_source, "crossed_boundary_type": crossed_source,
                              "adjacent_lane": "unavailable",
                              "request": "scene_configuration", "turn_permission": "unavailable", "vehicle_motion": "unavailable"},
        }
        result = self.monitor.update(observation)
        self.last_observation, self.last_result = observation, result
        if self.log_path:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"observation": observation, "decision": result}, ensure_ascii=False) + "\n")
        return result


def draw_decision(image, result, cv2, header_y=100):
    action = str(result.get("recommended_action", "WAIT"))
    state = str(result.get("state", "WAIT_INPUT"))
    color = (0, 220, 0) if action in ("PROCEED", "GO", "KEEP_LANE") else (0, 180, 255)
    if "STOP" in action or result.get("alarm_codes"):
        color = (0, 0, 255)
    rows = [f"Decision {state} | {action}",
            "Reason " + ", ".join(str(reason) for reason in result.get("reasons", [])),
            "Alarm " + (", ".join(str(code) for code in result.get("alarm_codes", [])) or "none")]
    for row_index, text in enumerate(rows):
        top = header_y + row_index * 25
        cv2.rectangle(image, (0, top), (image.shape[1], top + 25), (0, 0, 0), -1)
        cv2.putText(image, text, (10, top + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    return image
