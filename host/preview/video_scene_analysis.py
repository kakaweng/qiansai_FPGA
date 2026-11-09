import math
from collections import deque


def boundary_x(boundary, row):
    top_x, top_y, bottom_x, bottom_y = boundary
    return top_x + (row - top_y) * (bottom_x - top_x) / max(1, bottom_y - top_y)


def corridor_at(lane, row, width, height):
    curves = lane.get("ego_curves", {})
    if lane.get("state") == "estimated" and curves.get("left") and curves.get("right"):
        left_points, right_points = curves["left"], curves["right"]
        if max(left_points[0][1], right_points[0][1], height * 0.62) <= row <= min(left_points[-1][1], right_points[-1][1]) + height * 0.06:
            positions = []
            for points in (left_points, right_points):
                preceding, following = points[-2], points[-1]
                for first, second in zip(points, points[1:]):
                    if first[1] <= row <= second[1]:
                        preceding, following = first, second
                        break
                positions.append(boundary_x([*preceding, *following], row))
            left, right = positions
            if width * 0.025 < right - left < width * 0.85:
                return left, right, "observed_lane_geometry"
    polygon = lane.get("ego_polygon", [])
    if lane.get("state") == "estimated" and len(polygon) == 4:
        left = boundary_x([*polygon[0], *polygon[3]], row)
        right = boundary_x([*polygon[1], *polygon[2]], row)
        if width * 0.025 < right - left < width * 0.85 and row >= max(height * 0.62, polygon[0][1] - height * 0.15):
            return left, right, "observed_lane_geometry"
    depth = max(0, min(1, (row / height - 0.62) / 0.27))
    half_width = (0.025 + 0.20 * depth) * width
    return width * 0.5 - half_width, width * 0.5 + half_width, "camera_path_estimate"


def box_iou(first, second):
    left, top = max(first[0], second[0]), max(first[1], second[1])
    right, bottom = min(first[2], second[2]), min(first[3], second[3])
    overlap = max(0, right - left) * max(0, bottom - top)
    area = (first[2] - first[0]) * (first[3] - first[1]) + (second[2] - second[0]) * (second[3] - second[1])
    return overlap / max(1, area - overlap)


class ObjectMotionMonitor:
    def __init__(self):
        self.reset()

    def reset(self):
        self.tracks = {}
        self.next_id = 1
        self.last_ns = None

    def update(self, detections, lane, timestamp_ns, width, height):
        if self.last_ns is not None and (timestamp_ns <= self.last_ns or timestamp_ns - self.last_ns > 1_000_000_000):
            self.reset()
        self.last_ns = timestamp_ns
        self.tracks = {identifier: track for identifier, track in self.tracks.items()
                       if timestamp_ns - track["last_ns"] <= 600_000_000}
        available = set(self.tracks)
        observations = []
        for detection in sorted(detections, key=lambda item: item["score"], reverse=True):
            if detection["class_id"] == 9:
                continue
            box = detection["box"]
            candidates = []
            for identifier in available:
                track = self.tracks[identifier]
                if track["class_id"] != detection["class_id"]:
                    continue
                previous = track["box"]
                distance = math.hypot((box[0] + box[2] - previous[0] - previous[2]) / (2 * width),
                                      (box[3] - previous[3]) / height)
                overlap = box_iou(box, previous)
                if overlap > 0.10 or distance < 0.035:
                    candidates.append((overlap - distance * 3, identifier))
            identifier = max(candidates)[1] if candidates else self.next_id
            if not candidates:
                self.next_id += 1
                self.tracks[identifier] = {"class_id": detection["class_id"], "history": deque(maxlen=12)}
            available.discard(identifier)
            track = self.tracks[identifier]
            foot_x = (box[0] + box[2]) / 2
            left, right, path_source = corridor_at(lane, box[3], width, height)
            lane_offset = (foot_x - (left + right) / 2) / max(width * 0.025, right - left)
            track["history"].append((timestamp_ns, lane_offset, box[3] / height, (box[3] - box[1]) / height, path_source))
            track.update(box=list(box), last_ns=timestamp_ns)
            history = list(track["history"])
            oldest = next((sample for sample in history if timestamp_ns - sample[0] <= 900_000_000), history[-1])
            elapsed = (timestamp_ns - oldest[0]) / 1e9
            coherent_path = oldest[4] == path_source
            lateral_speed = (lane_offset - oldest[1]) / elapsed if elapsed >= 0.3 and coherent_path else None
            closing_speed = (box[3] / height - oldest[2]) / elapsed if elapsed >= 0.3 else None
            observations.append(dict(detection, track_id=identifier, lane_offset=round(lane_offset, 3),
                                     lateral_speed=None if lateral_speed is None else round(lateral_speed, 3),
                                     closing_speed=None if closing_speed is None else round(closing_speed, 3),
                                     tracked_seconds=round(elapsed, 3), path_source=path_source))
        riders = [item for item in observations if item["class_id"] in (1, 3)]
        for person in observations:
            if person["class_id"] != 0:
                continue
            box = person["box"]
            foot_x = (box[0] + box[2]) / 2
            rider_overlap = any(rider["box"][0] - width * 0.006 <= foot_x <= rider["box"][2] + width * 0.006
                                and rider["box"][1] <= box[3] <= rider["box"][3] + height * 0.025 for rider in riders)
            track = self.tracks[person["track_id"]]
            if rider_overlap:
                track["rider_evidence_ns"] = timestamp_ns
            person["rider_confirmed"] = timestamp_ns - track.get("rider_evidence_ns", -10**12) <= 1_000_000_000
            person["rider_source"] = "current_person_cycle_overlap" if rider_overlap else "recent_person_cycle_overlap" if person["rider_confirmed"] else "none"
        return observations


class PedestrianRiskMonitor:
    def __init__(self):
        self.reset()

    def reset(self):
        self.last_risk = None

    def update(self, objects, lane, timestamp_ns, width, height):
        levels = {"none": 0, "watch": 1, "slow": 2, "stop": 3}
        result = {"risk": "none", "conflict": "clear", "source": "observed_tracked_geometry",
                  "message": "未发现影响本车路径的行人", "evidence": [], "riders_excluded": 0}
        riders = [item for item in objects if item["class_id"] in (1, 3)]
        for person in objects:
            if person["class_id"] != 0 or person["score"] < 0.35:
                continue
            box = person["box"]
            foot_x, foot_y = (box[0] + box[2]) / 2, box[3]
            is_rider = any(rider["box"][0] - width * 0.006 <= foot_x <= rider["box"][2] + width * 0.006
                           and rider["box"][1] <= foot_y <= rider["box"][3] + height * 0.025
                           and box[1] < rider["box"][3] for rider in riders)
            if is_rider or person.get("rider_confirmed", False):
                result["riders_excluded"] += 1
                continue
            left, right, source = corridor_at(lane, foot_y, width, height)
            half_foot = min((box[2] - box[0]) * 0.18, (right - left) * 0.12)
            inside = foot_x + half_foot >= left and foot_x - half_foot <= right
            speed = person["lateral_speed"]
            offset = person["lane_offset"]
            moving_in = speed is not None and abs(speed) >= 0.16 and offset * speed < 0
            crossing = speed is not None and abs(speed) >= 0.16
            in_road = foot_y >= height * 0.64
            risk, message = "none", ""
            if in_road and inside:
                near = foot_y >= height * 0.83 or box[3] - box[1] >= height * 0.16
                if source == "observed_lane_geometry" and (crossing or near):
                    risk, message = ("stop", "行人接近且占用本车路径，停车礼让") if near else ("slow", "行人正在横穿本车路径，建议减速、准备礼让")
                elif crossing or near:
                    risk, message = "watch", "前方行人可能影响路径，请注意观察（车道待确认）"
            elif in_road and abs(offset) < 1.4 and moving_in:
                risk, message = "watch", "注意行人，可能横穿"
            evidence = {"track_id": person["track_id"], "box": list(box), "risk": risk,
                        "inside_path": inside, "path_source": source, "lateral_speed": speed,
                        "reason": "crossing_motion" if crossing else "near_path_occupancy" if risk != "none" else "no_path_conflict"}
            result["evidence"].append(evidence)
            if levels[risk] > levels[result["risk"]]:
                result.update(risk=risk, message=message)
        result["conflict"] = "present" if result["risk"] in ("slow", "stop") else "clear"
        current_ids = {item["track_id"] for item in objects if item["class_id"] == 0}
        if result["risk"] == "none" and self.last_risk and self.last_risk["track_id"] not in current_ids:
            if timestamp_ns - self.last_risk["timestamp_ns"] <= 500_000_000:
                result.update(risk="watch", conflict="unknown", message="刚才的行人暂时不可见，请注意观察", source="brief_missing_track")
        critical = next((item for item in result["evidence"] if item["risk"] == result["risk"] and item["risk"] != "none"), None)
        if critical:
            self.last_risk = dict(critical, timestamp_ns=timestamp_ns)
        elif result["source"] != "brief_missing_track":
            self.last_risk = None
        return result


class AdjacentTrafficMonitor:
    def update(self, objects, lane, width, height):
        result = {}
        boundaries = lane.get("boundaries", [])
        for side in ("left", "right"):
            evidence, conflicts, unknown_motion = [], [], False
            for item in objects:
                if item["class_id"] not in (1, 2, 3, 5, 7):
                    continue
                offset = item["lane_offset"]
                in_side = -1.55 < offset < -0.35 if side == "left" else 0.35 < offset < 1.55
                if not in_side:
                    continue
                box = item["box"]
                near = box[3] >= height * 0.79
                closing = item["closing_speed"] is not None and item["closing_speed"] > 0.035 and box[3] > height * 0.71
                conflict = near or closing
                evidence.append({"track_id": item["track_id"], "box": list(box), "near": near,
                                 "closing_speed": item["closing_speed"], "conflict": conflict})
                if conflict:
                    conflicts.append(item["track_id"])
                if box[3] > height * 0.71 and item["closing_speed"] is None:
                    unknown_motion = True
            geometry = lane.get("state") == "estimated"
            center = width * 0.5
            bottoms = sorted(boundary[2] for boundary in boundaries)
            ego_left = max((value for value in bottoms if value < center), default=None)
            ego_right = min((value for value in bottoms if value >= center), default=None)
            outer = max((value for value in bottoms if ego_left is not None and value < ego_left - width * 0.05), default=None) if side == "left" else min((value for value in bottoms if ego_right is not None and value > ego_right + width * 0.05), default=None)
            exists = True if geometry and outer is not None else None
            clear = False if conflicts else True if geometry and exists and not unknown_motion else None
            result[side] = {"exists": exists, "visible_clear": clear, "conflicting_tracks": conflicts,
                            "evidence": evidence, "source": "front_video_tracked_geometry",
                            "scope": "visible_front_area_only"}
        return result


class GlyphTemplates:
    def __init__(self, cv2, numpy):
        self.cv2, self.numpy = cv2, numpy
        self.templates = {}
        for name in ("straight", "left", "right", "straight_left", "straight_right", "circle"):
            mask = numpy.zeros((100, 100), dtype=numpy.uint8)
            if name == "circle":
                cv2.circle(mask, (50, 50), 37, 255, -1)
            else:
                if "straight" in name:
                    cv2.arrowedLine(mask, (50, 88), (50, 12), 255, 12, tipLength=0.36)
                if "left" in name or "right" in name:
                    destination = 12 if "left" in name else 88
                    if "straight" in name:
                        cv2.line(mask, (50, 88), (50, 54), 255, 12)
                        cv2.arrowedLine(mask, (50, 54), (destination, 30), 255, 12, tipLength=0.55)
                    else:
                        cv2.arrowedLine(mask, (88 if name == "left" else 12, 50), (destination, 50), 255, 12, tipLength=0.36)
            self.templates[name] = self.normalize(mask)

    def normalize(self, mask):
        points = self.cv2.findNonZero(mask)
        if points is None:
            return None
        left, top, width, height = self.cv2.boundingRect(points)
        return self.cv2.resize(mask[top:top + height, left:left + width], (48, 48), interpolation=self.cv2.INTER_NEAREST) > 0

    def classify(self, mask, combined=False):
        normalized = self.normalize(mask)
        if normalized is None:
            return "unknown", 0.0
        if not combined:
            normalized = self.cv2.morphologyEx(normalized.astype(self.numpy.uint8), self.cv2.MORPH_CLOSE,
                                              self.numpy.ones((3, 3), dtype=self.numpy.uint8)) > 0
            fill = float(self.numpy.mean(normalized))
            row_widths = self.numpy.sum(normalized, axis=1)
            column_widths = self.numpy.sum(normalized, axis=0)
            top = float(self.numpy.mean(row_widths[5:22]))
            bottom = float(self.numpy.mean(row_widths[27:44]))
            left = float(self.numpy.mean(column_widths[5:22]))
            right = float(self.numpy.mean(column_widths[27:44]))
            if 0.64 <= fill <= 0.90 and abs(top - bottom) < 7 and abs(left - right) < 7:
                return "circle", round(fill, 3)
            if 0.22 <= fill < 0.63:
                if top > bottom * 1.35 and top - bottom > 6:
                    return "straight", round(min(0.95, 0.65 + (top - bottom) / 100), 3)
                if left > right * 1.35 and left - right > 6:
                    return "left", round(min(0.95, 0.65 + (left - right) / 100), 3)
                if right > left * 1.35 and right - left > 6:
                    return "right", round(min(0.95, 0.65 + (right - left) / 100), 3)
        names = ("straight", "left", "right", "straight_left", "straight_right") if combined else ("straight", "left", "right")
        scores = sorted(((2 * float(self.numpy.sum(normalized & self.templates[name])) /
                          max(1, int(self.numpy.sum(normalized)) + int(self.numpy.sum(self.templates[name]))), name)
                         for name in names), reverse=True)
        score, name = scores[0]
        if score < 0.68 or score - scores[1][0] < 0.08:
            return "unknown", round(score, 3)
        return name, round(score, 3)


class DirectionalSignalMonitor:
    def __init__(self, scene, cv2, numpy):
        self.scene, self.cv2, self.numpy = scene, cv2, numpy
        self.glyphs = GlyphTemplates(cv2, numpy)
        self.reset()

    def reset(self):
        self.history = {}
        self.last_ns = None
        self.context_active = False

    def update(self, image, detections, timestamp_ns):
        from traffic_light import classify_region, paired_lamps
        if self.last_ns is not None and (timestamp_ns <= self.last_ns or timestamp_ns - self.last_ns > 1_000_000_000):
            self.reset()
        self.last_ns = timestamp_ns
        cv2, numpy = self.cv2, self.numpy
        height, width = image.shape[:2]
        region = [round(value * (width if index % 2 == 0 else height)) for index, value in enumerate(self.scene["search_region"])]
        candidates = []
        for detection in detections:
            if detection["class_id"] != 9:
                continue
            left, top, right, bottom = detection["box"]
            if (right - left < (bottom - top) * 1.4 or not region[0] < (left + right) / 2 < region[2]
                    or not region[1] < (top + bottom) / 2 < region[3]):
                continue
            candidates.extend(classify_region(image, (left - 5, top - 5, right + 5, bottom + 5), cv2, numpy, self.scene))
        source = "detected_horizontal_signal"
        if not candidates:
            candidates = paired_lamps(classify_region(image, region, cv2, numpy, self.scene), self.scene)
            source = "paired_signal_geometry"
        unique = []
        for candidate in candidates:
            if any(box_iou(candidate["box"], previous["box"]) > 0.5 for previous in unique):
                continue
            left, top, right, bottom = candidate["box"]
            patch = image[top:bottom, left:right]
            hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
            color = candidate["color"]
            if color == "green":
                thresholds = self.scene.get("color_thresholds", {})
                mask = cv2.inRange(hsv, (thresholds.get("green_hue_min", 45), 110, 160),
                                   (thresholds.get("green_hue_max", 100), 255, 255))
            elif color == "yellow":
                mask = cv2.inRange(hsv, (15, 110, 160), (38, 255, 255))
            else:
                mask = cv2.inRange(hsv, (0, 110, 160), (12, 255, 255)) | cv2.inRange(hsv, (165, 110, 160), (179, 255, 255))
            direction, score = self.glyphs.classify(mask) if min(patch.shape[:2]) >= 8 else ("unknown", 0.0)
            unique.append(dict(candidate, direction=direction, shape_score=score, source=source))
        by_direction = {direction: [] for direction in ("straight", "left", "right")}
        for candidate in unique:
            directions = by_direction if candidate["direction"] == "circle" else [candidate["direction"]]
            for direction in directions:
                if direction in by_direction:
                    by_direction[direction].append(candidate)
        states = {}
        self.context_active = self.context_active or bool(unique)
        for direction, evidence in by_direction.items():
            colors = {candidate["color"] for candidate in evidence}
            color = next(iter(colors)) if len(colors) == 1 else "unknown"
            previous = self.history.get(direction, {})
            count = previous.get("count", 0) + 1 if previous.get("pending") == color and color != "unknown" else 1
            history = dict(previous, pending=color, count=count)
            if color != "unknown" and color != previous.get("state"):
                history["state"] = "unknown"
            if color != "unknown" and count >= 2:
                history.update(state=color, evidence_ns=timestamp_ns, boxes=[list(candidate["box"]) for candidate in evidence])
            if len(colors) > 1:
                history.update(state="unknown", evidence_ns=timestamp_ns, boxes=[])
            ambiguous_change = any(candidate["direction"] == "unknown"
                                   and candidate["color"] != previous.get("state", "unknown")
                                   and any(box_iou(candidate["box"], box) > 0.2 for box in previous.get("boxes", []))
                                   for candidate in unique)
            if ambiguous_change:
                history.update(state="unknown", boxes=[])
            age = (timestamp_ns - history.get("evidence_ns", -10**12)) / 1e9
            retained = not evidence and age <= 1.5
            state = history.get("state", "unknown") if age <= 1.5 else "unknown"
            states[direction] = {"state": state, "retained": retained and state != "unknown",
                                 "evidence_age_seconds": round(max(0, age), 3) if age < 100 else None,
                                 "boxes": history.get("boxes", []) if state != "unknown" else [],
                                 "source": "brief_signal_memory" if retained else source if evidence else "unconfirmed"}
            self.history[direction] = history
        return {"state": states["straight"]["state"], "directions": states, "candidates": unique,
                "source": source if unique else "none", "region": region,
                "context_active": self.context_active,
                "action": "OBSERVE", "direction_method": "colored_glyph_templates"}


class RoadContextMonitor:
    def __init__(self, cv2, numpy):
        self.cv2, self.numpy = cv2, numpy
        self.glyphs = GlyphTemplates(cv2, numpy)
        self.reset()

    def reset(self):
        self.arrow_pending, self.arrow_count = "unknown", 0
        self.last_arrow, self.last_arrow_ns = "unknown", None
        self.previous_gray, self.previous_ns, self.previous_points = None, None, None
        self.turn_pending, self.turn_started_ns = "unknown", None
        self.turn_distance, self.turn_samples = 0.0, 0
        self.confirmed_turn, self.turn_evidence_ns = "unknown", None

    def camera_motion(self, image, detections, timestamp_ns):
        cv2, numpy = self.cv2, self.numpy
        height, width = image.shape[:2]
        working = cv2.resize(image, (480, 270))
        gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)
        velocity = None
        if self.previous_gray is not None and self.previous_points is not None:
            elapsed = (timestamp_ns - self.previous_ns) / 1e9
            if 0 < elapsed <= 0.6:
                points, status, errors = cv2.calcOpticalFlowPyrLK(self.previous_gray, gray, self.previous_points, None)
                if points is not None:
                    good = (status[:, 0] > 0) & (errors[:, 0] < 20)
                    old = self.previous_points[good, 0]
                    new = points[good, 0]
                    if len(old) >= 20:
                        displacements = (new[:, 0] - old[:, 0]) / elapsed
                        left = displacements[old[:, 0] < 210]
                        right = displacements[old[:, 0] > 270]
                        if len(left) >= 7 and len(right) >= 7:
                            left_velocity, right_velocity = float(numpy.median(left)), float(numpy.median(right))
                            if left_velocity * right_velocity > 0:
                                velocity = (left_velocity + right_velocity) / 2
        mask = numpy.zeros_like(gray)
        mask[55:175] = 255
        mask[:, 220:260] = 0
        for detection in detections:
            if detection["class_id"] == 9:
                continue
            left, top, right, bottom = detection["box"]
            cv2.rectangle(mask, (round(left * 480 / width) - 3, round(top * 270 / height) - 3),
                          (round(right * 480 / width) + 3, round(bottom * 270 / height) + 3), 0, -1)
        self.previous_points = cv2.goodFeaturesToTrack(gray, maxCorners=180, qualityLevel=0.02, minDistance=7, mask=mask)
        self.previous_gray, self.previous_ns = gray, timestamp_ns
        turn = "left" if velocity is not None and velocity > 60 else "right" if velocity is not None and velocity < -60 else "unknown"
        if turn != self.turn_pending:
            self.turn_pending, self.turn_started_ns = turn, timestamp_ns
            self.turn_distance, self.turn_samples = 0.0, 0
        if turn != "unknown":
            self.turn_samples += 1
            if self.turn_samples > 1:
                self.turn_distance += abs(velocity) * elapsed
        if turn != "unknown" and self.turn_samples >= 3 and (timestamp_ns - self.turn_started_ns >= 600_000_000 or self.turn_distance >= 60):
            self.confirmed_turn, self.turn_evidence_ns = turn, timestamp_ns
        if self.turn_evidence_ns is None or timestamp_ns - self.turn_evidence_ns > 1_500_000_000:
            self.confirmed_turn = "unknown"
        return {"direction": self.confirmed_turn, "lateral_background_pixels_per_second": None if velocity is None else round(velocity, 2),
                "source": "two_sided_background_optical_flow"}

    def update(self, image, lane, detections, timestamp_ns):
        cv2, numpy = self.cv2, self.numpy
        height, width = image.shape[:2]
        working = cv2.resize(image, (960, round(height * 960 / width)))
        scale = 960 / width
        gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(working, cv2.COLOR_BGR2HSV)
        contrast = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, numpy.ones((25, 25), dtype=numpy.uint8))
        paint = cv2.inRange(hsv, (0, 0, 135), (179, 70, 255)) & cv2.inRange(contrast, 18, 255)
        for detection in detections:
            if detection["class_id"] == 9:
                continue
            left, top, right, bottom = [round(value * scale) for value in detection["box"]]
            cv2.rectangle(paint, (left - 3, top - 3), (right + 3, bottom + 3), 0, -1)
        paint[:round(working.shape[0] * 0.67)] = 0
        paint[round(working.shape[0] * 0.89):] = 0
        lines = cv2.HoughLinesP(paint, 1, numpy.pi / 180, 35, minLineLength=120, maxLineGap=12)
        stop_rows = []
        if lines is not None:
            for encoded in lines.reshape(-1, 4):
                left, top, right, bottom = [int(value) for value in encoded]
                if abs(bottom - top) < 10 and abs(right - left) > 960 * 0.22:
                    stop_rows.append((top + bottom) / 2 / working.shape[0])
        near_stop_line = max(stop_rows, default=0) >= 0.76
        motion = self.camera_motion(image, detections, timestamp_ns)
        polygon = lane.get("ego_polygon", [])
        arrow, score = "unknown", 0.0
        if lane.get("state") == "estimated" and len(polygon) == 4:
            source = numpy.array(polygon, dtype=numpy.float32) * scale
            destination = numpy.array([[0, 0], [160, 0], [160, 240], [0, 240]], dtype=numpy.float32)
            warped = cv2.warpPerspective(paint, cv2.getPerspectiveTransform(source, destination), (160, 240))
            warped[:, :25] = 0
            warped[:, 135:] = 0
            count, labels, stats, _ = cv2.connectedComponentsWithStats(warped)
            for index in range(1, count):
                left, top, component_width, component_height, area = [int(value) for value in stats[index]]
                if not 12 < component_width < 100 or not 35 < component_height < 180 or area < 130:
                    continue
                mask = (labels[top:top + component_height, left:left + component_width] == index).astype(numpy.uint8) * 255
                candidate, candidate_score = self.glyphs.classify(mask, combined=True)
                if candidate != "unknown" and candidate_score > score:
                    arrow, score = candidate, candidate_score
        self.arrow_count = self.arrow_count + 1 if arrow == self.arrow_pending else 1
        self.arrow_pending = arrow
        if arrow != "unknown" and self.arrow_count >= 3:
            self.last_arrow, self.last_arrow_ns = arrow, timestamp_ns
        remembered = self.last_arrow if self.last_arrow_ns is not None and timestamp_ns - self.last_arrow_ns < 2_000_000_000 else "unknown"
        direction = motion["direction"] if motion["direction"] != "unknown" else remembered if remembered in ("left", "right") else "straight"
        return {"stop_line_near": near_stop_line, "stop_line_rows": stop_rows,
                "lane_arrow": remembered, "arrow_shape_score": score, "direction": direction,
                "direction_source": "observed_camera_turn" if motion["direction"] != "unknown" else "observed_road_arrow" if direction != "straight" else "straight_default",
                "camera_motion": motion,
                "distance_method": "image_proximity; no_metric_distance"}
