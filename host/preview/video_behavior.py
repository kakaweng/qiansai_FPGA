import copy


class VideoBehaviorMonitor:
    VERSION = "windows-video-behavior-v2.1"

    def __init__(self):
        self.reset()

    def reset(self):
        self.last_result = None
        self.event_seq = 0
        self.request_seq = 0
        self.last_request = None
        self.red_near_direction = None

    def update(self, pedestrian, signal, road, lane, adjacent, frame_seq, timestamp_ns, session_id):
        direction = road["direction"]
        selected = signal["directions"][direction]
        light = selected["state"]
        signal_pending = bool(signal["candidates"]) or signal.get("context_active", False)
        state, action, message = "NORMAL", "NORMAL", "正常行驶状态：未发现需要减速或停车的冲突"
        reasons, notes = [], []
        if lane.get("state") != "estimated":
            notes.append("车道线暂未确认；正常行驶提示不因此进入等待")
        if light == "unknown":
            notes.append("前方信号尚未确认，请注意观察" if signal_pending else "当前未确认本车相关信号")
        elif selected["retained"]:
            notes.append("信号暂时不可见，保留最近确认结果")
        risk = pedestrian["risk"]
        if light == "red":
            near = road["stop_line_near"] or any(box[3] >= signal["region"][3] * 0.95 for box in selected["boxes"])
            if near:
                self.red_near_direction = direction
            near = near or self.red_near_direction == direction
            state, action, message = ("RED_STOP", "STOP", "接近路口，停车等待红灯") if near else ("RED_APPROACH", "SLOW", "前方红灯，注意减速、准备停车")
            reasons.append("selected_direction_red")
        elif light == "yellow":
            state, action, message = "YELLOW_APPROACH", "SLOW", "前方黄灯，注意减速、准备停车"
            reasons.append("selected_direction_yellow")
        else:
            self.red_near_direction = None
        if risk == "stop":
            state, action, message = "PEDESTRIAN_STOP", "STOP", pedestrian["message"]
            reasons.append("near_pedestrian_path_conflict")
        elif risk == "slow" and action != "STOP":
            state, action, message = "PEDESTRIAN_SLOW", "SLOW", pedestrian["message"]
            reasons.append("distant_pedestrian_crossing")
        elif risk == "watch":
            if action == "NORMAL":
                state, action, message = "PEDESTRIAN_WATCH", "WATCH", pedestrian["message"]
            else:
                notes.append(pedestrian["message"])
            reasons.append("pedestrian_watch_only")
        if action == "NORMAL" and light == "green":
            message = "本车对应方向为绿灯，未发现需要礼让的路径冲突"
        if action == "NORMAL" and light == "unknown" and signal_pending:
            state, action, message = "SIGNAL_UNCONFIRMED", "WATCH", "前方信号尚未确认，请注意观察"
        request = copy.deepcopy(self.last_request)
        if request:
            request["age_seconds"] = round((timestamp_ns - request["timestamp_ns"]) / 1e9, 2)
            request["historical"] = timestamp_ns != request["timestamp_ns"]
        signature = (state, action, message, tuple(notes))
        previous = self.last_result
        changed = previous is None or signature != (previous["state"], previous["recommended_action"], previous["message"], tuple(previous["notes"]))
        if changed:
            self.event_seq += 1
        permissions = {f"change_{side}": self.change_evaluation(side, lane, adjacent, action)["status"] for side in ("left", "right")}
        permissions.update({f"turn_{side}": "signal_green" if signal["directions"][side]["state"] == "green" else
                            "forbidden" if signal["directions"][side]["state"] == "red" else "unknown" for side in ("left", "right")})
        result = {"version": self.VERSION, "state": state, "recommended_action": action,
                  "message": message, "reasons": reasons, "notes": notes, "permissions": permissions,
                  "alarm_codes": [], "new_alarm_codes": [], "changed": changed, "event_seq": self.event_seq,
                  "frame_seq": frame_seq, "timestamp_ns": timestamp_ns, "session_id": session_id,
                  "mode": "advisory_only", "direction": direction, "lane_request": request,
                  "input_sources": {"pedestrian": pedestrian["source"], "signal": selected["source"],
                                    "direction": road["direction_source"], "adjacent": "front_video_tracked_geometry"}}
        self.last_result = copy.deepcopy(result)
        return result

    def change_evaluation(self, side, lane, adjacent, action):
        boundary = lane.get(f"{side}_boundary_type", "unknown")
        evidence = lane.get("boundary_type_evidence", {}).get(side, {})
        traffic = adjacent[side]
        name = "左" if side == "left" else "右"
        reasons = []
        if evidence.get("held"):
            boundary = "unknown"
        if boundary == "solid":
            reasons.append(f"{name}侧为实线，禁止跨越")
        if traffic["visible_clear"] is False:
            reasons.append(f"{name}侧可见邻道车辆较近或正在接近，暂缓变道")
        if action == "STOP":
            reasons.append("当前需要停车或礼让，暂缓变道")
        if reasons:
            status = "forbidden"
        elif boundary == "unknown":
            status = "unknown"
            reasons.append(f"{name}侧线型为暂存结果，需等待当前帧重新确认" if evidence.get("held")
                           else f"{name}侧线型尚未确认，暂时无法判断")
        elif traffic["exists"] is not True or traffic["visible_clear"] is None:
            status = "unknown"
            reasons.append(f"{name}侧为虚线，但邻道范围或车辆运动尚未确认")
        else:
            status = "visible_clear"
            reasons.append(f"{name}侧为虚线，可见区域未发现变道冲突，请确认侧后方")
        return {"status": status, "message": "；".join(reasons), "reasons": reasons,
                "boundary_type": boundary, "traffic": copy.deepcopy(traffic), "scope": "visible_front_area_only",
                "line_constraint": "crossable" if boundary == "dashed" else "not_crossable" if boundary == "solid" else "unknown",
                "boundary_evidence": copy.deepcopy(evidence)}

    def request_change(self, side, result):
        self.request_seq += 1
        evaluation = self.change_evaluation(side, result["lane"], result["adjacent"], result["decision"]["recommended_action"])
        self.last_request = dict(evaluation, request_seq=self.request_seq, side=side,
                                 timestamp_ns=result["decision"]["timestamp_ns"], frame_seq=result["decision"]["frame_seq"],
                                 acknowledged=True, mode="single_frame_request_with_recent_motion")
        result["decision"]["lane_request"] = copy.deepcopy(self.last_request)
        return copy.deepcopy(self.last_request)

    def cancel_request(self, result):
        self.last_request = None
        result["decision"]["lane_request"] = None
