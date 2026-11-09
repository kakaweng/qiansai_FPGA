import copy


DECISION_VERSION = "windows-behavior-v1-20261009"
PERMISSION_NAMES = ("change_left", "change_right", "turn_left", "turn_right")
MANEUVERS = ("keep_lane",) + PERMISSION_NAMES
GROUP_NAMES = ("signal", "pedestrian", "lane", "request", "turn", "actual", "lane_change")


def integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def boolean(value, name, optional=False):
    if optional and value is None:
        return value
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean" + (" or null" if optional else ""))
    return value


def choice(value, values, name):
    if value not in values:
        raise ValueError(f"{name} must be one of {values}")
    return value


class DrivingDecisionMonitor:
    def __init__(self, config=None):
        defaults = {"go_confirm_frames": 3, "release_clear_frames": 3,
                    "input_timeout_ns": 500_000_000, "alarm_hold_ns": 2_000_000_000}
        config = {} if config is None else config
        if not isinstance(config, dict) or set(config) - set(defaults):
            raise ValueError("unknown or invalid decision configuration")
        self.config = dict(defaults, **config)
        for name, value in self.config.items():
            if integer(value, name) == 0:
                raise ValueError(f"{name} must be positive")
        self.reset()

    def reset(self):
        self.session_id = None
        self.last_timestamp_ns = None
        self.last_frame_seq = None
        self.pedestrian_latched = False
        self.clear_count = 0
        self.go_count = 0
        self.go_candidate = None
        self.last_lane_event_seq = 0
        self.alarm_expiry = {}
        self.behavior_alarm_conditions = set()
        self.event_seq = 0
        self.last_result = None

    def _validate(self, observation):
        if not isinstance(observation, dict):
            raise ValueError("observation must be an object")
        session = observation.get("session_id", "default")
        if not isinstance(session, str) or not session:
            raise ValueError("session_id must be a nonempty string")
        integer(observation.get("frame_seq"), "frame_seq")
        integer(observation.get("timestamp_ns"), "timestamp_ns")
        boolean(observation.get("fresh", False), "fresh")
        for name in GROUP_NAMES:
            if not isinstance(observation.get(name, {}), dict):
                raise ValueError(f"{name} must be an object")
        signal = observation.get("signal", {})
        boolean(signal.get("required", True), "signal.required")
        boolean(signal.get("bound", False), "signal.bound")
        choice(signal.get("state", "unknown"), ("red", "yellow", "green", "unknown"), "signal.state")
        choice(signal.get("direction", "unknown"), ("left", "straight", "right", "circle", "unknown"),
               "signal.direction")
        choice(observation.get("pedestrian", {}).get("conflict", "unknown"),
               ("present", "clear", "unknown"), "pedestrian.conflict")
        lane = observation.get("lane", {})
        choice(lane.get("state", "unknown"), ("estimated", "unknown"), "lane.state")
        for side in ("left", "right"):
            choice(lane.get(f"{side}_boundary_type", "unknown"), ("solid", "dashed", "unknown"),
                   f"lane.{side}_boundary_type")
            for field in ("adjacent_exists", "adjacent_clear"):
                boolean(lane.get(f"{side}_{field}"), f"lane.{side}_{field}", optional=True)
            choice(observation.get("turn", {}).get(side, "unknown"), ("allowed", "forbidden", "unknown"),
                   f"turn.{side}")
        choice(observation.get("request", {}).get("maneuver", "keep_lane"), MANEUVERS, "request.maneuver")
        actual = observation.get("actual", {})
        for field in ("moving", "crossed_stop_line"):
            boolean(actual.get(field), f"actual.{field}", optional=True)
        event = observation.get("lane_change", {})
        integer(event.get("event_seq", 0), "lane_change.event_seq")
        choice(event.get("direction", "unknown"), ("left", "right", "none", "unknown"), "lane_change.direction")
        choice(event.get("crossed_boundary_type", "unknown"), ("solid", "dashed", "unknown"),
               "lane_change.crossed_boundary_type")
        if event.get("timestamp_ns") is not None:
            integer(event["timestamp_ns"], "lane_change.timestamp_ns")
        sources = observation.get("input_sources", {})
        if not isinstance(sources, dict):
            raise ValueError("input_sources must be an object")
        return session

    def _permissions(self, observation):
        lane = observation.get("lane", {})
        signal = observation.get("signal", {})
        permissions = {}
        for side in ("left", "right"):
            boundary = lane.get(f"{side}_boundary_type", "unknown")
            exists = lane.get(f"{side}_adjacent_exists")
            clear = lane.get(f"{side}_adjacent_clear")
            if boundary == "solid" or exists is False or clear is False:
                permissions[f"change_{side}"] = "forbidden"
            elif boundary == "dashed" and exists is True and clear is True and lane.get("state") == "estimated":
                permissions[f"change_{side}"] = "allowed"
            else:
                permissions[f"change_{side}"] = "unknown"
            permission = observation.get("turn", {}).get(side, "unknown")
            if signal.get("required", True) and signal.get("direction", "unknown") not in (side, "circle", "unknown"):
                permission = "forbidden"
            permissions[f"turn_{side}"] = permission
        return {name: permissions[name] for name in PERMISSION_NAMES}

    def _alarm(self, code, timestamp_ns, new_alarms):
        self.alarm_expiry[code] = timestamp_ns + self.config["alarm_hold_ns"]
        new_alarms.append(code)

    def _alarms(self, observation, new_alarms):
        timestamp_ns = observation["timestamp_ns"]
        signal = observation.get("signal", {})
        actual = observation.get("actual", {})
        conditions = set()
        if signal.get("required", True) and signal.get("bound", False):
            if signal.get("state") == "yellow" and actual.get("moving") is True:
                conditions.add("yellow_forced_drive")
            if signal.get("state") == "red" and actual.get("crossed_stop_line") is True:
                conditions.add("red_light_crossing")
        for code in sorted(conditions):
            if code not in self.behavior_alarm_conditions:
                self._alarm(code, timestamp_ns, new_alarms)
            else:
                self.alarm_expiry[code] = timestamp_ns + self.config["alarm_hold_ns"]
        self.behavior_alarm_conditions = conditions
        event = observation.get("lane_change", {})
        event_seq = event.get("event_seq", 0)
        if event_seq <= self.last_lane_event_seq:
            return
        self.last_lane_event_seq = event_seq
        event_time = event.get("timestamp_ns")
        if event_time is None or not 0 <= timestamp_ns - event_time <= self.config["input_timeout_ns"]:
            return
        if event.get("direction") in ("left", "right") and event.get("crossed_boundary_type") == "solid":
            self._alarm("forced_solid_lane_change", timestamp_ns, new_alarms)

    def _emit(self, observation, state, action, reasons, permissions, new_alarms):
        sources = copy.deepcopy(observation.get("input_sources", {}))
        for name in GROUP_NAMES:
            sources.setdefault(name, observation.get(name, {}).get("source", observation.get("source", "unavailable")))
        signature = (state, action, tuple(reasons), tuple(permissions.items()), tuple(sorted(self.alarm_expiry)))
        previous = self.last_result
        previous_signature = None if previous is None else (
            previous["state"], previous["recommended_action"], tuple(previous["reasons"]),
            tuple(previous["permissions"].items()), tuple(previous["alarm_codes"]))
        changed = signature != previous_signature or bool(new_alarms)
        if changed:
            self.event_seq += 1
        result = {
            "version": DECISION_VERSION, "session_id": observation.get("session_id", "default"),
            "frame_seq": observation["frame_seq"], "timestamp_ns": observation["timestamp_ns"],
            "state": state, "recommended_action": action, "reasons": reasons,
            "permissions": permissions, "alarm_codes": sorted(self.alarm_expiry),
            "new_alarm_codes": new_alarms, "changed": changed, "event_seq": self.event_seq,
            "input_sources": sources, "fresh": observation.get("fresh", False),
            "mode": "advisory_only", "confidence": None,
            "confidence_kind": "rule_result; no_calibrated_probability",
        }
        self.last_result = copy.deepcopy(result)
        return result

    def update(self, observation):
        session = self._validate(observation)
        timestamp_ns = observation["timestamp_ns"]
        frame_seq = observation["frame_seq"]
        reset_reason = None
        if self.session_id is not None and self.session_id != session:
            reset_reason = "session_changed"
        elif self.last_timestamp_ns is not None:
            if timestamp_ns < self.last_timestamp_ns or frame_seq < self.last_frame_seq:
                reset_reason = "timeline_reset"
            elif timestamp_ns - self.last_timestamp_ns > self.config["input_timeout_ns"]:
                reset_reason = "input_gap"
        if reset_reason:
            self.reset()
        self.session_id = session
        fresh = observation.get("fresh", False)
        duplicate = self.last_timestamp_ns is not None and (
            timestamp_ns == self.last_timestamp_ns or frame_seq == self.last_frame_seq)
        if not fresh or duplicate:
            self.go_count = 0
            self.go_candidate = None
            self.clear_count = 0
            return self._emit(observation, "WAIT_INPUT", "WAIT",
                              ["stale_input" if not fresh else "duplicate_frame"],
                              dict.fromkeys(PERMISSION_NAMES, "unknown"), [])
        self.last_timestamp_ns = timestamp_ns
        self.last_frame_seq = frame_seq
        self.alarm_expiry = {code: expiry for code, expiry in self.alarm_expiry.items() if expiry > timestamp_ns}
        new_alarms = []
        self._alarms(observation, new_alarms)
        conflict = observation.get("pedestrian", {}).get("conflict", "unknown")
        if conflict == "present":
            self.pedestrian_latched = True
            self.clear_count = 0
        elif conflict == "clear":
            self.clear_count += 1
            if self.clear_count >= self.config["release_clear_frames"]:
                self.pedestrian_latched = False
        else:
            self.clear_count = 0
        signal = observation.get("signal", {})
        required = signal.get("required", True)
        bound = signal.get("bound", False)
        light = signal.get("state", "unknown")
        lane = observation.get("lane", {})
        maneuver = observation.get("request", {}).get("maneuver", "keep_lane")
        permissions = self._permissions(observation)
        reasons = [] if reset_reason is None else [reset_reason]
        red = required and bound and light == "red"
        if red:
            reasons.append("red_signal")
        if self.pedestrian_latched:
            reasons.append("pedestrian_conflict" if conflict == "present" else "pedestrian_clearance_pending")
        if red or self.pedestrian_latched:
            state, action = ("RED_STOP" if red else "YIELD_PEDESTRIAN"), "STOP"
            permissions = dict.fromkeys(PERMISSION_NAMES, "forbidden")
        elif required and (not bound or light == "unknown"):
            state, action = "WAIT_SIGNAL", "WAIT"
            reasons.append("signal_not_bound" if not bound else "signal_unknown")
        elif required and light == "yellow":
            state, action = "YELLOW_WAIT", "WAIT"
            reasons.append("yellow_signal")
        elif required and maneuver in ("keep_lane", "change_left", "change_right") and signal.get("direction") in ("left", "right"):
            state, action = "WAIT_TURN", "WAIT"
            reasons.append("signal_direction_mismatch")
        elif conflict != "clear" or self.clear_count < self.config["release_clear_frames"]:
            state, action = "WAIT_PEDESTRIAN", "WAIT"
            reasons.append("pedestrian_unknown" if conflict != "clear" else "pedestrian_clearance_pending")
        elif lane.get("state", "unknown") != "estimated":
            state, action = "WAIT_LANE", "WAIT"
            reasons.append("lane_unknown")
        elif maneuver != "keep_lane" and permissions[maneuver] != "allowed":
            state, action = ("KEEP_LANE" if maneuver.startswith("change_") else "WAIT_TURN"), "WAIT"
            reasons.append(f"{maneuver}_{permissions[maneuver]}")
            if maneuver.startswith("change_"):
                side = maneuver.split("_", 1)[1]
                if lane.get(f"{side}_boundary_type") == "solid":
                    reasons.append(f"{side}_solid_boundary")
        else:
            self.go_count = self.go_count + 1 if self.go_candidate == maneuver else 1
            self.go_candidate = maneuver
            if self.go_count < self.config["go_confirm_frames"]:
                state, action = "WAIT_CONFIRM", "WAIT"
                reasons.append("go_confirmation_pending")
            else:
                state = "GREEN_GO" if required else "ROAD_GO"
                action = "GO" if maneuver == "keep_lane" else maneuver.upper()
                reasons.append("green_signal_and_clear_path" if required else "uncontrolled_road_clear_path")
        if action not in ("GO", "CHANGE_LEFT", "CHANGE_RIGHT", "TURN_LEFT", "TURN_RIGHT"):
            permissions = {name: "unknown" if permission == "allowed" else permission
                           for name, permission in permissions.items()}
            if state != "WAIT_CONFIRM":
                self.go_count = 0
                self.go_candidate = None
        return self._emit(observation, state, action, reasons, permissions, new_alarms)
