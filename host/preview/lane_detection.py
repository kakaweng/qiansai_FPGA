import math


class LaneMonitor:
    def __init__(self, scene, cv2, numpy):
        self.cv2, self.numpy = cv2, numpy
        self.config = dict(scene.get("lane_detection", {}))
        self.processing_width = int(self.config.get("processing_width", 960))
        self.region = self.config.get("region", [0.0, 0.60, 1.0, 0.87])
        self.vanishing_point = self.config.get("vanishing_point", [0.46, 0.60])
        self.camera_center = float(self.config.get("camera_center", 0.50))
        self.confirm_frames = int(self.config.get("confirm_frames", 2))
        self.geometry_timeout_ms = int(self.config.get("geometry_timeout_ms", 350))
        self.change_confirm_frames = int(self.config.get("change_confirm_frames", 3))
        self.change_hold_ms = int(self.config.get("change_hold_ms", 1200))
        if not 320 <= self.processing_width <= 1920 or not 1 <= self.confirm_frames <= 30:
            raise ValueError("invalid lane processing width or confirmation count")
        if not 0 <= self.geometry_timeout_ms <= 1000:
            raise ValueError("lane geometry timeout must be between 0 and 1000 milliseconds")
        if not 2 <= self.change_confirm_frames <= 30 or not 500 <= self.change_hold_ms <= 5000:
            raise ValueError("invalid lane change confirmation count or display duration")
        if (len(self.region) != 4 or not all(math.isfinite(value) for value in self.region) or
                not 0 <= self.region[0] < self.region[2] <= 1 or
                not 0 <= self.region[1] < self.region[3] <= 1):
            raise ValueError("lane region must be normalized left, top, right, bottom")
        if (len(self.vanishing_point) != 2 or
                not all(math.isfinite(value) and 0 <= value <= 1 for value in self.vanishing_point) or
                not math.isfinite(self.camera_center) or not 0 < self.camera_center < 1):
            raise ValueError("invalid lane camera geometry")
        self.reset()

    def reset(self):
        self.last_timestamp_ns = self.last_result = None
        self.pending = None
        self.pending_count = 0
        self.horizon = None
        self.last_geometry_ns = None
        self.boundary_tracks = []
        self.next_track_id = 1
        self.tracking_horizon = None
        self.reference_width = None
        self.lane_change_event_seq = 0
        self.lane_change_timestamp_ns = None
        self.last_lane_change = None
        self.last_lane_change_confidence = 0.0
        self.last_lane_change_reason = "none"
        self.corridor_offset_samples = []
        self.corridor_width_reference = None
        self.corridor_stable_side = None
        self.corridor_stable_since_ns = None
        self.corridor_pending_side = None
        self.corridor_pending_since_ns = None
        self.corridor_neutral_since_ns = None
        self.corridor_origin_side = None
        self.corridor_origin_offset = None
        self.corridor_origin_ns = None
        self.corridor_last_valid_ns = None
        self.position_pending = None
        self.position_pending_count = 0

    def _record_lane_change(self, direction, confidence, timestamp_ns, reason="tracked_boundary_crossing"):
        if (self.lane_change_timestamp_ns is not None and
                timestamp_ns - self.lane_change_timestamp_ns < 1200000000):
            return False
        self.lane_change_event_seq += 1
        self.lane_change_timestamp_ns = timestamp_ns
        self.last_lane_change = direction
        self.last_lane_change_confidence = max(0.0, min(1.0, confidence))
        self.last_lane_change_reason = reason
        return True

    def _update_corridor_crossing(self, offset, corridor_width, timestamp_ns, confidence):
        if offset is None or corridor_width is None:
            if (self.corridor_last_valid_ns is not None and
                    timestamp_ns - self.corridor_last_valid_ns > self.geometry_timeout_ms * 1000000):
                self.corridor_offset_samples = []
                self.corridor_stable_side = self.corridor_pending_side = None
                self.corridor_neutral_since_ns = None
            return
        if self.corridor_width_reference is None:
            self.corridor_width_reference = corridor_width
        self.corridor_last_valid_ns = timestamp_ns
        self.corridor_offset_samples.append((timestamp_ns, offset, corridor_width))
        self.corridor_offset_samples = [sample for sample in self.corridor_offset_samples
                                        if timestamp_ns - sample[0] <= 2200000000]
        neutral_limit = 0.10
        side_limit = 0.18
        side = "positive" if offset >= side_limit else "negative" if offset <= -side_limit else None
        if side is None:
            if self.corridor_stable_side is not None:
                stable_elapsed = timestamp_ns - (self.corridor_stable_since_ns or timestamp_ns)
                if stable_elapsed >= 250000000:
                    if self.corridor_neutral_since_ns is None:
                        self.corridor_neutral_since_ns = timestamp_ns
                    if self.corridor_origin_side is None:
                        origin_offset = offset
                        for sample_timestamp, sample_offset, _ in reversed(self.corridor_offset_samples):
                            sample_side = ("positive" if sample_offset >= side_limit else
                                           "negative" if sample_offset <= -side_limit else None)
                            if sample_side == self.corridor_stable_side:
                                origin_offset = sample_offset
                                break
                        self.corridor_origin_side = self.corridor_stable_side
                        self.corridor_origin_offset = origin_offset
                        self.corridor_origin_ns = timestamp_ns
            self.corridor_pending_side = None
            self.corridor_pending_since_ns = None
            return
        if abs(offset) <= neutral_limit:
            return
        if self.corridor_stable_side is None:
            if self.corridor_pending_side != side:
                self.corridor_pending_side = side
                self.corridor_pending_since_ns = timestamp_ns
            if timestamp_ns - self.corridor_pending_since_ns >= 250000000:
                self.corridor_stable_side = side
                self.corridor_stable_since_ns = self.corridor_pending_since_ns
            return
        if side == self.corridor_stable_side:
            if (self.corridor_origin_ns is not None and
                    timestamp_ns - self.corridor_origin_ns > 5000000000):
                self.corridor_origin_side = self.corridor_origin_offset = self.corridor_origin_ns = None
                self.corridor_neutral_since_ns = None
            self.corridor_pending_side = None
            self.corridor_pending_since_ns = None
            return
        if self.corridor_neutral_since_ns is None:
            self.corridor_pending_side = None
            self.corridor_pending_since_ns = None
            return
        if self.corridor_pending_side != side:
            self.corridor_pending_side = side
            self.corridor_pending_since_ns = timestamp_ns
            return
        target_elapsed = timestamp_ns - self.corridor_pending_since_ns
        origin_offset = self.corridor_origin_offset
        net_change = abs(offset - origin_offset) if origin_offset is not None else 0.0
        if (target_elapsed >= 250000000 and origin_offset is not None and
                net_change >= 0.28 and
                timestamp_ns - self.corridor_origin_ns <= 5000000000):
            direction = "left" if self.corridor_stable_side == "positive" and side == "negative" else "right"
            if self._record_lane_change(direction, confidence, timestamp_ns,
                                        "corridor_center_crossing"):
                self.corridor_stable_side = side
                self.corridor_stable_since_ns = self.corridor_pending_since_ns
                self.corridor_origin_side = self.corridor_origin_offset = self.corridor_origin_ns = None
                self.corridor_neutral_since_ns = None
                self.corridor_pending_side = self.corridor_pending_since_ns = None

    def _track_boundaries(self, boundaries, timestamp_ns, width, height):
        horizon = self.horizon
        if self.tracking_horizon is not None and horizon is not None:
            if (abs(horizon[0] - self.tracking_horizon[0]) > width * 0.05 or
                    abs(horizon[1] - self.tracking_horizon[1]) > height * 0.055):
                self.boundary_tracks = []
                self.reference_width = None
        self.tracking_horizon = horizon
        timeout_ns = max(self.geometry_timeout_ms, 350) * 1000000
        tracks = [track for track in self.boundary_tracks
                  if 0 <= timestamp_ns - track["timestamp_ns"] <= timeout_ns]
        reference_width = self.reference_width or width * 0.30
        matching_limit = min(width * 0.10, reference_width * 0.30)
        anchor_y = self.region[1] * height
        candidates = []
        for boundary_index, boundary in enumerate(boundaries):
            anchor_x = boundary["slope"] * anchor_y + boundary["intercept"]
            for track_index, track in enumerate(tracks):
                elapsed_seconds = (timestamp_ns - track["timestamp_ns"]) / 1000000000
                predicted_bottom = track["bottom"] + track.get("velocity", 0) * elapsed_seconds
                distance = abs(boundary["bottom"] - predicted_bottom)
                if (distance <= matching_limit and
                        abs(anchor_x - track["anchor_x"]) <= width * 0.05):
                    candidates.append((distance, boundary_index, track_index))
        assignments, used_tracks = {}, set()
        for _, boundary_index, track_index in sorted(candidates):
            if boundary_index not in assignments and track_index not in used_tracks:
                assignments[boundary_index] = track_index
                used_tracks.add(track_index)
        current_tracks = []
        for boundary_index, boundary in enumerate(boundaries):
            if boundary_index in assignments:
                track = tracks[assignments[boundary_index]]
                elapsed_seconds = (timestamp_ns - track["timestamp_ns"]) / 1000000000
                velocity = (boundary["bottom"] - track["bottom"]) / max(0.001, elapsed_seconds)
                track["velocity"] = 0.5 * velocity + 0.5 * track.get("velocity", velocity)
                track["observations"] += 1
            else:
                track = {"id": self.next_track_id, "observations": 1, "velocity": 0.0,
                         "stable_side": None, "side_pending": None, "side_count": 0,
                         "origin": None, "history": []}
                self.next_track_id += 1
            track.update(bottom=boundary["bottom"], top_x=boundary["top_x"],
                         anchor_x=boundary["slope"] * anchor_y + boundary["intercept"],
                         timestamp_ns=timestamp_ns, quality=boundary["quality"])
            track["history"].append((timestamp_ns, boundary["bottom"]))
            track["history"] = [(record_ns, bottom) for record_ns, bottom in track["history"]
                                if timestamp_ns - record_ns <= 1800000000]
            boundary["track_id"] = track["id"]
            current_tracks.append(track)
        self.boundary_tracks = current_tracks + [track for index, track in enumerate(tracks)
                                                  if index not in used_tracks]
        return current_tracks

    def _update_lane_change(self, current_tracks, ego_pair, timestamp_ns, ego_x, corridor_width):
        if corridor_width is not None:
            if (self.reference_width is not None and
                    not 0.65 <= corridor_width / self.reference_width <= 1.35):
                for track in self.boundary_tracks:
                    track["origin"] = None
            self.reference_width = corridor_width
        if self.reference_width is None:
            return
        margin = self.reference_width * 0.08
        pair_roles = ({ego_pair[0]: "left", ego_pair[1]: "right"} if ego_pair else {})
        for track in current_tracks:
            displacement = track["bottom"] - ego_x
            side = "left" if displacement < -margin else "right" if displacement > margin else None
            if side is None:
                track["side_pending"], track["side_count"] = None, 0
                continue
            track["side_count"] = track["side_count"] + 1 if track["side_pending"] == side else 1
            track["side_pending"] = side
            if track["side_count"] < self.change_confirm_frames:
                continue
            previous_side = track["stable_side"]
            track["stable_side"] = side
            origin = track["origin"]
            if origin is not None and origin["side"] != side:
                movement = track["bottom"] - origin["bottom"]
                elapsed_ns = timestamp_ns - origin["timestamp_ns"]
                direction = "left" if origin["side"] == "left" else "right"
                correct_direction = movement > 0 if direction == "left" else movement < 0
                recent_steps = [following[1] - preceding[1]
                                for preceding, following in zip(track["history"][:-1], track["history"][1:])]
                active_steps = [step for step in recent_steps if abs(step) >= self.reference_width * 0.01]
                directed_steps = [step for step in active_steps
                                  if (step > 0 if direction == "left" else step < 0)]
                monotonic = len(directed_steps) >= max(2, len(active_steps) * 0.65)
                if (correct_direction and abs(movement) >= self.reference_width * 0.30 and
                        300000000 <= elapsed_ns <= 5000000000 and
                        track["observations"] >= self.change_confirm_frames * 2 and monotonic and
                        min(track["quality"], origin["quality"]) >= 0.45 and
                        (self.lane_change_timestamp_ns is None or
                         timestamp_ns - self.lane_change_timestamp_ns >= 1200000000) and
                        self._record_lane_change(direction,
                                                 min(track["quality"], origin["quality"]), timestamp_ns)):
                    track["origin"] = None
                elif elapsed_ns > 5000000000 or not correct_direction:
                    track["origin"] = None
            if (track["id"] in pair_roles and pair_roles[track["id"]] == side and
                    (previous_side is None or track["origin"] is None)):
                track["origin"] = {"side": side, "bottom": track["bottom"],
                                   "timestamp_ns": timestamp_ns, "quality": track["quality"]}
            if track["origin"] is not None and timestamp_ns - track["origin"]["timestamp_ns"] > 5000000000:
                track["origin"] = {"side": side, "bottom": track["bottom"],
                                   "timestamp_ns": timestamp_ns, "quality": track["quality"]}

    def _paint_mask(self, image, detections, coordinate_scale, segmentation=None):
        cv2, numpy = self.cv2, self.numpy
        height, width = image.shape[:2]
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        kernel_size = max(9, int(self.config.get("contrast_kernel_size", round(width / 64))) | 1)
        contrast = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT,
                                    cv2.getStructuringElement(cv2.MORPH_RECT,
                                                              (kernel_size, kernel_size)))
        white = cv2.inRange(hsv, (0, 0, self.config.get("white_value_min", 170)),
                             (179, 65, 255))
        white &= cv2.inRange(contrast, self.config.get("white_contrast_min", 24), 255)
        yellow = cv2.inRange(hsv, (15, 75, 100), (40, 255, 255))
        mask = white | yellow
        if segmentation is not None:
            mask = cv2.resize(segmentation, (width, height), interpolation=cv2.INTER_NEAREST)
        left, top, right, bottom = [round(value * (width if index % 2 == 0 else height))
                                    for index, value in enumerate(self.region)]
        region_mask = numpy.zeros_like(mask)
        region_mask[top:bottom, left:right] = 255
        mask &= region_mask
        for detection in detections or []:
            if detection.get("class_id") not in (0, 1, 2, 3, 5, 7):
                continue
            if detection.get("score", 0) < 0.25:
                continue
            box = detection.get("box", ())
            if len(box) != 4:
                continue
            box_left, box_top, box_right, box_bottom = [round(value * coordinate_scale) for value in box]
            margin = max(2, round(width * 0.003))
            cv2.rectangle(mask, (box_left - margin, box_top - margin),
                          (box_right + margin, box_bottom + margin), 0, -1)
        component_count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        clean = numpy.zeros_like(mask)
        for component_index in range(1, component_count):
            component_left, component_top, component_width, component_height, area = stats[component_index]
            if area < 15 or component_height < self.config.get("marking_min_height", 12) or component_width > width * 0.55:
                continue
            component = labels[component_top:component_top + component_height,
                               component_left:component_left + component_width] == component_index
            rows, columns = numpy.nonzero(component)
            covariance = numpy.cov(numpy.column_stack((columns, rows)), rowvar=False)
            eigenvalues, eigenvectors = numpy.linalg.eigh(covariance)
            if (eigenvalues[1] / max(0.01, eigenvalues[0]) < self.config.get("marking_min_elongation", 7) or
                    abs(eigenvectors[1, 1]) < 0.19):
                continue
            clean[component_top:component_top + component_height,
                  component_left:component_left + component_width][component] = 255
        mask = clean
        return mask, yellow, (left, top, right, bottom)

    def _boundaries(self, mask, yellow, region):
        cv2, numpy = self.cv2, self.numpy
        height, width = mask.shape
        reference_y = max(region[3] - 1, round(self.config.get("reference_row", self.region[3]) * height) - 1)
        lines = cv2.HoughLinesP(mask, 1, numpy.pi / 360, threshold=18,
                               minLineLength=max(22, round(height * 0.050)),
                               maxLineGap=max(14, round(height * 0.045)))
        if lines is None:
            return []
        raw_segments = []
        for encoded_line in lines.reshape(-1, 4):
            first_x, first_y, second_x, second_y = [float(value) for value in encoded_line]
            vertical_span = abs(second_y - first_y)
            if vertical_span < height * 0.016:
                continue
            slope = (second_x - first_x) / (second_y - first_y)
            intercept = first_x - slope * first_y
            if abs(slope) > 5:
                continue
            raw_segments.append({"slope": slope, "intercept": intercept,
                                 "support": vertical_span,
                                 "top": min(first_y, second_y), "end": max(first_y, second_y)})
        candidates = []
        strongest = sorted(raw_segments, key=lambda value: value["support"], reverse=True)[:48]
        for first_index, first in enumerate(strongest):
            for second in strongest[first_index + 1:]:
                if first["slope"] * second["slope"] >= 0:
                    continue
                intersection_y = ((second["intercept"] - first["intercept"]) /
                                  (first["slope"] - second["slope"]))
                intersection_x = first["slope"] * intersection_y + first["intercept"]
                if 0.38 * width < intersection_x < 0.61 * width and 0.55 * height < intersection_y < 0.83 * height:
                    candidates.append((intersection_x, intersection_y))
        best_score = 0.0
        horizon_x, horizon_y = self.vanishing_point[0] * width, self.vanishing_point[1] * height
        for candidate_x, candidate_y in candidates:
            score = sum(segment["support"] for segment in strongest
                        if abs(segment["slope"] * candidate_y + segment["intercept"] - candidate_x) < width * 0.035
                        and segment["top"] > candidate_y + height * 0.018)
            if self.horizon is not None:
                distance = abs(candidate_y - self.horizon[1]) / height + abs(candidate_x - self.horizon[0]) / width
                score *= max(0.65, 1 - distance * 2)
            else:
                distance = abs(candidate_x - self.vanishing_point[0] * width) / width
                score *= max(0.65, 1 - distance * 3)
            if score > best_score:
                best_score, horizon_x, horizon_y = score, candidate_x, candidate_y
        if best_score >= height * 0.20:
            if self.horizon is not None:
                horizon_x = self.horizon[0] * 0.65 + horizon_x * 0.35
                horizon_y = self.horizon[1] * 0.65 + horizon_y * 0.35
            self.horizon = (horizon_x, horizon_y)
        elif self.horizon is not None:
            horizon_x, horizon_y = self.horizon
        display_top = max(region[1], round(horizon_y + height * 0.035))
        segments = []
        for segment in raw_segments:
            slope, intercept = segment["slope"], segment["intercept"]
            projected_horizon = slope * horizon_y + intercept
            projected_bottom = slope * reference_y + intercept
            if (abs(projected_horizon - horizon_x) > width * 0.055 or
                    abs(projected_bottom - horizon_x) < width * 0.010 or
                    segment["top"] <= horizon_y + height * 0.012 or
                    not -0.65 * width <= projected_bottom <= 1.65 * width):
                continue
            segments.append(dict(segment, bottom=projected_bottom))
        groups = []
        for segment in sorted(segments, key=lambda value: value["bottom"]):
            if groups and abs(segment["bottom"] - groups[-1]["bottom"]) < width * 0.027:
                group = groups[-1]
                total_support = group["support"] + segment["support"]
                for field in ("slope", "intercept", "bottom"):
                    group[field] = ((group[field] * group["support"] +
                                     segment[field] * segment["support"]) / total_support)
                group["support"] = total_support
                group["top"] = min(group["top"], segment["top"])
                group["end"] = max(group["end"], segment["end"])
            else:
                groups.append(dict(segment))
        accepted = []
        sample_rows = numpy.arange(display_top, region[3], dtype=numpy.int32)
        for group in groups:
            sample_columns = numpy.rint(group["slope"] * sample_rows + group["intercept"]).astype(numpy.int32)
            valid = (sample_columns >= region[0] + 3) & (sample_columns < region[2] - 3)
            valid_rows, valid_columns = sample_rows[valid], sample_columns[valid]
            if len(valid_rows) < height * 0.07:
                continue
            white_presence = numpy.zeros(len(valid_rows), dtype=numpy.bool_)
            yellow_presence = numpy.zeros(len(valid_rows), dtype=numpy.bool_)
            for column_offset in range(-3, 4):
                white_presence |= mask[valid_rows, valid_columns + column_offset] > 0
                yellow_presence |= yellow[valid_rows, valid_columns + column_offset] > 0
            coverage = float(numpy.mean(white_presence))
            if coverage < 0.07 or group["end"] - group["top"] < height * 0.022:
                continue
            group["coverage"] = coverage
            group["yellow"] = float(numpy.mean(yellow_presence))
            group["quality"] = min(0.95, coverage * 1.7 +
                                   (group["end"] - group["top"]) / max(1, region[3] - display_top) * 0.6 +
                                   min(1, group["support"] / (height * 0.30)) * 0.30)
            group["top_x"] = group["slope"] * display_top + group["intercept"]
            group["display_top"] = display_top
            accepted.append(group)
        return accepted

    def update(self, image, detections, timestamp_ns, segmentation=None):
        if timestamp_ns == self.last_timestamp_ns:
            return self.last_result
        if self.last_timestamp_ns is not None and (
                timestamp_ns < self.last_timestamp_ns or
                timestamp_ns - self.last_timestamp_ns > 1000000000):
            self.reset()
        previous_result = self.last_result
        self.last_timestamp_ns = timestamp_ns
        height, width = image.shape[:2]
        processing_width = min(width, self.processing_width)
        coordinate_scale = processing_width / width
        working = self.cv2.resize(image, (processing_width, round(height * coordinate_scale)),
                                  interpolation=self.cv2.INTER_AREA)
        mask, yellow, region = self._paint_mask(working, detections, coordinate_scale, segmentation)
        boundaries = self._boundaries(mask, yellow, region)
        current_tracks = self._track_boundaries(boundaries, timestamp_ns,
                                                processing_width, working.shape[0])
        image_boundaries = [[round(boundary["top_x"] / coordinate_scale),
                             round(boundary["display_top"] / coordinate_scale),
                             round(boundary["bottom"] / coordinate_scale),
                             round(max(region[3] - 1, round(self.config.get("reference_row", self.region[3]) * working.shape[0]) - 1) / coordinate_scale)] for boundary in boundaries]
        result = {"state": "unknown", "lane_count": None, "ego_lane": None,
                  "confidence": 0.0, "boundaries": image_boundaries, "ego_polygon": [],
                  "offset": None, "changed": False, "reason": "no_ego_lane_pair",
                  "confidence_kind": "geometry_support", "lane_position": "unknown",
                  "lateral_position": "unknown", "lane_change": "unknown",
                  "lane_change_confidence": 0.0, "lane_change_reason": "insufficient_tracking",
                  "lane_change_event_seq": self.lane_change_event_seq,
                  "lane_change_timestamp_ns": self.lane_change_timestamp_ns,
                  "alert": "none"}
        result["perception_source"] = "YOLOP_BDD100K_pretrained" if segmentation is not None else "image_marking_geometry"
        result["boundary_observation_bottom"] = round((region[3] - 1) / coordinate_scale)
        result["boundary_track_ids"] = [boundary["track_id"] for boundary in boundaries]
        ego_x = self.camera_center * processing_width
        ego_pair, valid_corridor_width = None, None
        left_indices = [index for index, boundary in enumerate(boundaries) if boundary["bottom"] < ego_x]
        right_indices = [index for index, boundary in enumerate(boundaries) if boundary["bottom"] >= ego_x]
        if left_indices and right_indices:
            left_index, right_index = left_indices[-1], right_indices[0]
            left_boundary, right_boundary = boundaries[left_index], boundaries[right_index]
            corridor_width = right_boundary["bottom"] - left_boundary["bottom"]
            top_width = right_boundary["top_x"] - left_boundary["top_x"]
            quality = min(left_boundary["quality"], right_boundary["quality"])
            if (0.09 * processing_width <= corridor_width <= 0.66 * processing_width and
                    0 < top_width < corridor_width * 0.60 and quality >= 0.30):
                self.last_geometry_ns = timestamp_ns
                valid_corridor_width = corridor_width
                ego_pair = (left_boundary["track_id"], right_boundary["track_id"])
                result["ego_polygon"] = [[image_boundaries[left_index][0], image_boundaries[left_index][1]],
                                         [image_boundaries[right_index][0], image_boundaries[right_index][1]],
                                         [image_boundaries[right_index][2], image_boundaries[right_index][3]],
                                         [image_boundaries[left_index][2], image_boundaries[left_index][3]]]
                result["offset"] = round((ego_x - (left_boundary["bottom"] +
                                                         right_boundary["bottom"]) / 2) / corridor_width, 3)
                result["confidence"] = round(quality, 3)
                result["reason"] = "current_lane_geometry"
                first_boundary, last_boundary = boundaries[0], boundaries[-1]
                left_edge = ((first_boundary["coverage"] >= 0.70 and
                              first_boundary["quality"] >= 0.80 and first_boundary["yellow"] >= 0.60 and
                              first_boundary["bottom"] < ego_x - 0.20 * corridor_width) or
                             (first_boundary["coverage"] >= 0.82 and
                              first_boundary["quality"] >= 0.90 and
                              first_boundary["bottom"] < ego_x - 0.80 * corridor_width))
                right_edge = (last_boundary["coverage"] >= 0.82 and
                              last_boundary["quality"] >= 0.90 and
                              last_boundary["bottom"] > ego_x + 0.80 * corridor_width)
                gaps = [following["bottom"] - preceding["bottom"]
                        for preceding, following in zip(boundaries[:-1], boundaries[1:])]
                coherent = all(0.60 * corridor_width <= gap <= 1.50 * corridor_width for gap in gaps)
                if left_edge and coherent:
                    result["ego_lane"] = left_index + 1
                    result["reason"] = "left_road_edge_evidence"
                if left_edge and right_edge and coherent and 2 <= len(boundaries) - 1 <= 6:
                    result["lane_count"] = len(boundaries) - 1
                    result["reason"] = "both_road_edges_evidence"
                if result["ego_lane"] == 1:
                    result["lane_position"] = "left"
                elif result["ego_lane"] is not None:
                    result["lane_position"] = ("right" if result["ego_lane"] == result["lane_count"] else
                                               "center" if result["lane_count"] is not None else "numbered")
                position_signature = (result["ego_lane"], result["lane_count"], result["lane_position"])
                self.position_pending_count = (self.position_pending_count + 1
                                               if self.position_pending == position_signature else 1)
                self.position_pending = position_signature
                if self.position_pending_count < self.confirm_frames:
                    result["ego_lane"] = result["lane_count"] = None
                    result["lane_position"] = "unknown"
                previous_polygon = previous_result.get("ego_polygon", []) if previous_result else []
                continuous_corridor = (len(previous_polygon) == 4 and
                                       abs(result["ego_polygon"][2][0] - previous_polygon[2][0]) <
                                       corridor_width / coordinate_scale * 0.22 and
                                       abs(result["ego_polygon"][3][0] - previous_polygon[3][0]) <
                                       corridor_width / coordinate_scale * 0.22)
                self.pending_count = self.pending_count + 1 if self.pending == ego_pair or continuous_corridor else 1
                if continuous_corridor:
                    result["ego_polygon"] = [[round(previous_coordinate * 0.45 + current_coordinate * 0.55)
                                              for previous_coordinate, current_coordinate in zip(previous_point, current_point)]
                                             for previous_point, current_point in zip(previous_polygon,
                                                                                       result["ego_polygon"])]
                self.pending = ego_pair
                if self.pending_count >= self.confirm_frames:
                    result["state"] = "estimated"
                else:
                    result["reason"] = "confirming_lane_geometry"
            else:
                result["reason"] = "implausible_lane_geometry"
                self.pending, self.pending_count = None, 0
        else:
            if (self.last_geometry_ns is None or
                    timestamp_ns - self.last_geometry_ns > self.geometry_timeout_ms * 1000000):
                self.pending, self.pending_count = None, 0
            self.position_pending, self.position_pending_count = None, 0
        self._update_lane_change(current_tracks, ego_pair, timestamp_ns, ego_x, valid_corridor_width)
        hold_ns = self.geometry_timeout_ms * 1000000
        if (result["state"] == "unknown" and not result["ego_polygon"] and previous_result and
                previous_result.get("ego_polygon") and self.last_geometry_ns is not None and
                0 <= timestamp_ns - self.last_geometry_ns <= hold_ns):
            for field in ("ego_polygon", "offset"):
                result[field] = previous_result[field]
            result["confidence"] = round(previous_result["confidence"] * 0.8, 3)
            result["reason"] = "recent_corridor_no_current_pair"
        effective_corridor_width = valid_corridor_width
        if effective_corridor_width is None and len(result.get("ego_polygon", [])) == 4:
            effective_corridor_width = abs(result["ego_polygon"][2][0] - result["ego_polygon"][3][0]) * coordinate_scale
        self._update_corridor_crossing(result["offset"], effective_corridor_width, timestamp_ns,
                                       result["confidence"])
        if result["ego_polygon"] and result["offset"] is not None:
            result["lateral_position"] = ("left" if result["offset"] < -0.18 else
                                          "right" if result["offset"] > 0.18 else "center")
            result["lane_change"] = "none"
            result["lane_change_reason"] = "no_confirmed_boundary_crossing"
        if (self.lane_change_timestamp_ns is not None and
                0 <= timestamp_ns - self.lane_change_timestamp_ns <= self.change_hold_ms * 1000000):
            result["lane_change"] = self.last_lane_change
            result["lane_change_confidence"] = round(self.last_lane_change_confidence, 3)
            result["lane_change_reason"] = self.last_lane_change_reason
            result["alert"] = "lane_change_" + self.last_lane_change
        result["lane_change_event_seq"] = self.lane_change_event_seq
        result["lane_change_timestamp_ns"] = self.lane_change_timestamp_ns
        previous_signature = ((previous_result["state"], previous_result["ego_lane"], previous_result["lane_count"],
                               previous_result.get("lane_change_event_seq", 0))
                              if previous_result else ("unknown", None, None, 0))
        result["changed"] = (result["state"], result["ego_lane"], result["lane_count"],
                             result["lane_change_event_seq"]) != previous_signature
        self.last_result = result
        return result


def draw_lane(image, result, cv2, scale=1.0, header_y=40):
    import numpy

    color = (80, 220, 80) if result.get("state") == "estimated" else (0, 190, 255)
    polygon = result.get("ego_curve_polygon") or result.get("ego_polygon", [])
    if polygon:
        coordinates = numpy.rint(numpy.asarray(polygon) * scale).astype(numpy.int32)
        overlay = image.copy()
        cv2.fillPoly(overlay, [coordinates], color)
        cv2.addWeighted(overlay, 0.18, image, 0.82, 0, image)
    for boundary in ([] if result.get("boundary_curves") else result.get("boundaries", [])):
        top_x, top_y, bottom_x, bottom_y = [round(value * scale) for value in boundary]
        cv2.line(image, (top_x, top_y), (bottom_x, bottom_y), (0, 240, 255), 2, cv2.LINE_AA)
    if header_y is None:
        return image
    lane = result.get("ego_lane")
    state = ("TRACKED" if result.get("state") == "estimated" else
             "RECENT" if result.get("reason") == "recent_corridor_no_current_pair" else
             "CONFIRMING" if polygon else "UNKNOWN")
    lane_label = f"{lane} from left" if lane is not None else "highlighted area" if polygon else "UNKNOWN"
    road_position = result.get("lane_position", "unknown")
    position_label = f" | Road position: {road_position.upper()}" if road_position in ("left", "center", "right") else ""
    text = f"Current lane: {lane_label} | {state} {result.get('confidence', 0):.0%}{position_label}"
    lane_change = result.get("lane_change", "unknown")
    change_label = (f"{lane_change.upper()} DETECTED {result.get('lane_change_confidence', 0):.0%}"
                    if lane_change in ("left", "right") else "no crossing detected" if lane_change == "none" else "UNKNOWN")
    change_text = f"Lane change: {change_label} | In-lane position: {result.get('lateral_position', 'unknown').upper()}"
    cv2.rectangle(image, (0, header_y), (image.shape[1], header_y + 64), (0, 0, 0), -1)
    cv2.putText(image, text, (10, header_y + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.53, color, 2)
    cv2.putText(image, change_text, (10, header_y + 52), cv2.FONT_HERSHEY_SIMPLEX, 0.50,
                (0, 200, 255) if lane_change in ("left", "right") else color, 2)
    return image
