class BoundaryTypeMonitor:
    def __init__(self, cv2, numpy, confirm_frames=3, require_corridor=True, hold_ms=0):
        self.cv2, self.numpy = cv2, numpy
        self.confirm_frames = confirm_frames
        self.require_corridor = require_corridor
        self.hold_ms = hold_ms
        self.reset()

    def reset(self):
        self.previous = {}
        self.confirmed = {}
        self.last_timestamp_ns = None
        self.last_change_event_seq = None

    def _same_boundary(self, previous, boundary, track_id, width, height):
        if not previous:
            return False
        if previous.get("track_id") is not None and track_id is not None and previous["track_id"] != track_id:
            return False
        stored = previous["boundary"]
        anchor_y = height * 0.8
        current_anchor = boundary[0] + (boundary[2] - boundary[0]) * (anchor_y - boundary[1]) / max(1, boundary[3] - boundary[1])
        stored_anchor = stored[0] + (stored[2] - stored[0]) * (anchor_y - stored[1]) / max(1, stored[3] - stored[1])
        return abs(stored[2] - boundary[2]) < width * 0.03 and abs(stored_anchor - current_anchor) < width * 0.03

    def _expire_side(self, side, timestamp_ns):
        previous = self.previous.get(side)
        if previous and timestamp_ns - previous["last_valid_observation_ns"] > 500_000_000:
            self.previous.pop(side, None)
        confirmed = self.confirmed.get(side)
        if confirmed and timestamp_ns - confirmed["timestamp_ns"] > self.hold_ms * 1000000:
            self.confirmed.pop(side, None)

    def classify(self, image, boundary, detections):
        cv2, numpy = self.cv2, self.numpy
        height, width = image.shape[:2]
        scale = min(1.0, 960 / width)
        working = cv2.resize(image, (round(width * scale), round(height * scale)))
        hsv = cv2.cvtColor(working, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)
        contrast = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, numpy.ones((17, 17), dtype=numpy.uint8))
        white = cv2.inRange(hsv, (0, 0, 120), (179, 75, 255))
        white &= cv2.inRange(contrast, 16, 255)
        yellow = cv2.inRange(hsv, (15, 65, 100), (40, 255, 255))
        paint = white | yellow
        top_x, top_y, bottom_x, bottom_y = (value * scale for value in boundary)
        start, end = max(0, round(top_y)), min(working.shape[0] - 1, round(bottom_y))
        unknown = {"type": "unknown", "reason": "insufficient_visible_marking", "coverage": 0.0}
        if end - start < 50 or bottom_y <= top_y:
            return unknown
        rows = numpy.arange(start, end + 1)
        columns = numpy.rint(top_x + (rows - top_y) * (bottom_x - top_x) / (bottom_y - top_y)).astype(int)
        valid = (columns >= 4) & (columns < working.shape[1] - 4)
        for detection in detections:
            if detection["class_id"] not in (0, 1, 2, 3, 5, 7):
                continue
            left, top, right, bottom = (value * scale for value in detection["box"])
            valid &= ~((rows >= top - 4) & (rows <= bottom + 4) &
                       (columns >= left - 6) & (columns <= right + 6))
        if float(numpy.mean(valid)) < 0.65:
            return dict(unknown, reason="boundary_occluded")
        support = numpy.zeros(len(rows), dtype=bool)
        for offset in range(-4, 5):
            safe_columns = numpy.clip(columns + offset, 0, working.shape[1] - 1)
            support |= paint[rows, safe_columns] > 0
        support &= valid
        kernel = numpy.ones((5, 1), dtype=numpy.uint8)
        support = cv2.morphologyEx(support.astype(numpy.uint8)[:, None], cv2.MORPH_CLOSE, kernel)[:, 0] > 0
        coverage = float(numpy.mean(support[valid]))
        transitions = numpy.flatnonzero(numpy.diff(numpy.r_[False, support, False].astype(int)))
        runs = [(int(first), int(last)) for first, last in zip(transitions[::2], transitions[1::2])
                if last - first >= 7]
        gaps = [second[0] - first[1] for first, second in zip(runs, runs[1:])
                if numpy.all(valid[first[1]:second[0]])]
        maximum_gap = max(gaps, default=0)
        result = dict(unknown, coverage=round(coverage, 3), visible_runs=len(runs),
                      maximum_internal_gap=maximum_gap, sampled_rows=len(rows))
        if coverage >= 0.88 and maximum_gap <= max(8, len(rows) * 0.06):
            result.update(type="solid", reason="continuous_paint_support")
        elif 0.20 <= coverage <= 0.78 and len(runs) >= 2 and maximum_gap >= max(14, len(rows) * 0.10):
            result.update(type="dashed", reason="separated_paint_runs")
        return result

    def update(self, image, lane, detections, timestamp_ns, camera_center=0.5):
        if self.last_timestamp_ns is not None and (
                timestamp_ns <= self.last_timestamp_ns or timestamp_ns - self.last_timestamp_ns > 500_000_000):
            self.reset()
        self.last_timestamp_ns = timestamp_ns
        result = dict(lane)
        result.update(left_boundary_type="unknown", right_boundary_type="unknown",
                      boundary_type_source="observed_geometry_prototype", boundary_type_evidence={})
        if self.require_corridor and lane.get("state") != "estimated":
            self.previous.clear()
            self.confirmed.clear()
            return result
        change_event_seq = lane.get("lane_change_event_seq", 0)
        if self.last_change_event_seq is not None and change_event_seq != self.last_change_event_seq:
            self.previous.clear()
            self.confirmed.clear()
        self.last_change_event_seq = change_event_seq
        center = image.shape[1] * camera_center
        boundaries = lane.get("boundaries", [])
        sides = {
            "left": max((index for index, boundary in enumerate(boundaries) if boundary[2] < center),
                        key=lambda index: boundaries[index][2], default=None),
            "right": min((index for index, boundary in enumerate(boundaries) if boundary[2] >= center),
                         key=lambda index: boundaries[index][2], default=None),
        }
        for side, boundary_index in sides.items():
            self._expire_side(side, timestamp_ns)
            if boundary_index is None:
                result["boundary_type_evidence"][side] = {"type": "unknown", "reason": "no_current_boundary",
                                                         "observed_type": "unknown", "held": False}
                confirmed = self.confirmed.get(side)
                if (confirmed and self.hold_ms > 0 and
                        timestamp_ns - confirmed["timestamp_ns"] <= self.hold_ms * 1000000):
                    evidence = dict(confirmed["evidence"], type=confirmed["type"],
                                    reason="recent_boundary_observation", held=True,
                                    observed_type="unknown", current_reason="no_current_boundary",
                                    age_ms=round((timestamp_ns - confirmed["timestamp_ns"]) / 1e6, 1))
                    result[f"{side}_boundary_type"] = confirmed["type"]
                    result["boundary_type_evidence"][side] = evidence
                elif confirmed:
                    self.confirmed.pop(side, None)
                if self.hold_ms == 0:
                    self.previous.pop(side, None)
                continue
            boundary = boundaries[boundary_index]
            track_ids = lane.get("boundary_track_ids", [])
            track_id = track_ids[boundary_index] if boundary_index < len(track_ids) else None
            top_x, top_y, bottom_x, bottom_y = boundary
            observed_bottom = min(bottom_y, lane.get("boundary_observation_bottom", bottom_y))
            observed_x = top_x + (bottom_x - top_x) * (observed_bottom - top_y) / max(1, bottom_y - top_y)
            evidence = self.classify(image, [top_x, top_y, observed_x, observed_bottom], detections)
            previous = self.previous.get(side, {})
            continuous = self._same_boundary(previous, boundary, track_id, image.shape[1], image.shape[0])
            confirmed = self.confirmed.get(side)
            if confirmed and not self._same_boundary(confirmed, boundary, track_id, image.shape[1], image.shape[0]):
                self.confirmed.pop(side, None)
            observed_type = evidence["type"]
            evidence.update(observed_type=observed_type, held=False, age_ms=0, track_id=track_id)
            if observed_type in ("solid", "dashed"):
                count = (previous.get("count", 0) + 1
                         if continuous and previous.get("pending_type") == observed_type else 1)
                pending_type = observed_type
                if confirmed and confirmed["type"] != observed_type:
                    self.confirmed.pop(side, None)
                self.previous[side] = {"boundary": list(boundary), "track_id": track_id,
                                       "pending_type": pending_type, "count": count,
                                       "last_valid_observation_ns": timestamp_ns}
            else:
                if self.hold_ms == 0 or not continuous:
                    self.previous.pop(side, None)
                count = self.previous.get(side, {}).get("count", 0)
                pending_type = self.previous.get(side, {}).get("pending_type")
            evidence.update(confirmation_frames=count)
            result["boundary_type_evidence"][side] = evidence
            if (observed_type in ("solid", "dashed") and pending_type == observed_type and count >= self.confirm_frames):
                self.confirmed[side] = {"type": pending_type, "timestamp_ns": timestamp_ns,
                                        "boundary": list(boundary), "track_id": track_id, "evidence": dict(evidence)}
                result[f"{side}_boundary_type"] = pending_type
            else:
                confirmed = self.confirmed.get(side)
                if (observed_type == "unknown" and confirmed and self.hold_ms > 0 and
                        timestamp_ns - confirmed["timestamp_ns"] <= self.hold_ms * 1000000):
                    result[f"{side}_boundary_type"] = confirmed["type"]
                    result["boundary_type_evidence"][side] = dict(evidence, type=confirmed["type"],
                                                                    reason="recent_boundary_observation",
                                                                    current_reason=evidence["reason"],
                                                                    held=True,
                                                                    age_ms=round((timestamp_ns - confirmed["timestamp_ns"]) / 1e6, 1))
        return result
