import argparse
import json
import sys
import time
import uuid
from pathlib import Path


ACTION_LABELS = {
    "STOP": "停车",
    "WAIT": "等待确认",
    "GO": "继续通行",
    "CHANGE_LEFT": "向左变道",
    "CHANGE_RIGHT": "向右变道",
    "TURN_LEFT": "左转",
    "TURN_RIGHT": "右转",
}

VALUE_LABELS = {
    "red": "红灯", "yellow": "黄灯", "green": "绿灯", "unknown": "未知",
    "present": "有冲突行人", "clear": "确认无冲突",
    "estimated": "车道有效", "solid": "实线", "dashed": "虚线",
    "yes": "是", "no": "否", "keep_lane": "保持当前车道",
    "change_left": "请求向左变道", "change_right": "请求向右变道",
    "turn_left": "请求左转", "turn_right": "请求右转",
    "allowed": "允许", "forbidden": "禁止",
}


def expectation_errors(expected, actual, prefix="decision"):
    errors = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{prefix}: 期望字典，实际为 {actual!r}"]
        for field, value in expected.items():
            if field not in actual:
                errors.append(f"{prefix}.{field}: 缺少输出字段")
            else:
                errors.extend(expectation_errors(value, actual[field], f"{prefix}.{field}"))
    elif expected != actual:
        errors.append(f"{prefix}: 期望 {expected!r}，实际 {actual!r}")
    return errors


class JsonlWriter:
    def __init__(self, paths):
        self.streams = []
        destinations = sorted({path.resolve() for path in paths if path is not None})
        for destination in destinations:
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.streams.append(destination.open("w", encoding="utf-8"))

    def write(self, record):
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for stream in self.streams:
            stream.write(line)
            stream.flush()

    def close(self):
        for stream in self.streams:
            stream.close()


def run_replay(path, log_paths):
    from driving_decision import DrivingDecisionMonitor

    document = json.loads(path.read_text(encoding="utf-8"))
    scenarios = document.get("scenarios", [document])
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("回放文件需要非空 scenarios 或 frames")
    monitor = DrivingDecisionMonitor()
    writer = JsonlWriter(log_paths)
    frame_count = 0
    assertion_count = 0
    failure_count = 0
    try:
        for scenario_index, scenario in enumerate(scenarios, start=1):
            monitor.reset()
            description = scenario.get("description", f"场景 {scenario_index}")
            frames = scenario.get("frames")
            if not isinstance(frames, list) or not frames:
                raise ValueError(f"{description}: frames 必须为非空列表")
            scenario_failures = 0
            scenario_frame_count = 0
            for frame_index, frame in enumerate(frames, start=1):
                if not isinstance(frame.get("observation"), dict):
                    raise ValueError(f"{description} 第 {frame_index} 帧缺少 observation")
                expected = frame.get("expected", {})
                if not isinstance(expected, dict):
                    raise ValueError(f"{description} 第 {frame_index} 帧 expected 必须为字典")
                repeat = frame.get("repeat", 1)
                step_ns = frame.get("step_ns", 100000000)
                if type(repeat) is not int or repeat < 1 or repeat > 10000:
                    raise ValueError(f"{description} 第 {frame_index} 项 repeat 必须为 1..10000 整数")
                if type(step_ns) is not int or step_ns <= 0:
                    raise ValueError(f"{description} 第 {frame_index} 项 step_ns 必须为正整数")
                for repetition in range(repeat):
                    observation = dict(frame["observation"])
                    original_source = observation.get("source")
                    observation["source"] = "replay"
                    observation["frame_seq"] += repetition
                    observation["timestamp_ns"] += repetition * step_ns
                    decision = monitor.update(observation)
                    current_expected = expected if repetition == repeat - 1 else {}
                    errors = expectation_errors(current_expected, decision)
                    frame_count += 1
                    scenario_frame_count += 1
                    assertion_count += bool(current_expected)
                    failure_count += bool(errors)
                    scenario_failures += bool(errors)
                    writer.write({
                        "mode": "replay", "simulated_input": True,
                        "scenario": description, "frame_index": scenario_frame_count,
                        "template_frame_index": frame_index, "repetition": repetition + 1,
                        "original_observation_source": original_source,
                        "observation": observation, "decision": decision,
                        "expected": current_expected, "verification_errors": errors,
                    })
                    for error in errors:
                        print(f"失败：{description} / 第 {scenario_frame_count} 帧 / {error}", file=sys.stderr)
            result = "通过" if scenario_failures == 0 else "失败"
            print(f"{result}：{description}，{scenario_frame_count} 帧")
    finally:
        writer.close()
    print(f"模拟回放完成：{frame_count} 帧，{assertion_count} 帧包含期望，{failure_count} 帧失败。")
    return 1 if failure_count else 0


def optional_bool(value):
    return {"yes": True, "no": False, "unknown": None}[value]


class DecisionDemo:
    def __init__(self, window, tkinter, ttk, monitor, writer):
        self.window = window
        self.tkinter = tkinter
        self.ttk = ttk
        self.monitor = monitor
        self.writer = writer
        self.variables = {}
        self.session_id = "manual-" + uuid.uuid4().hex[:12]
        self.frame_seq = 0
        self.crossing_seq = 0
        self.crossing = {"event_seq": 0, "direction": "left", "timestamp_ns": 0,
                         "crossed_boundary_type": "unknown"}
        self.stopped = False
        self.timer = None
        window.title("驾驶决策演示 — 手动模拟输入")
        window.geometry("1080x800")
        window.minsize(960, 740)
        window.protocol("WM_DELETE_WINDOW", self.close)
        self.build_interface()
        self.tick()

    def group(self, parent, title, row, column):
        frame = self.ttk.LabelFrame(parent, text=title, padding=10)
        frame.grid(row=row, column=column, sticky="nsew", padx=6, pady=6)
        frame.columnconfigure(1, weight=1)
        return frame

    def choices(self, frame, key, label, values, initial, row):
        variable = self.tkinter.StringVar(value=VALUE_LABELS.get(initial, initial))
        self.variables[key] = (variable, {VALUE_LABELS.get(value, value): value for value in values})
        self.ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
        control = self.ttk.Combobox(frame, textvariable=variable, state="readonly", width=23,
                                    values=[VALUE_LABELS.get(value, value) for value in values])
        control.grid(row=row, column=1, sticky="ew", pady=4)

    def set_value(self, key, value):
        self.variables[key][0].set(VALUE_LABELS.get(value, value))

    def value(self, key):
        variable, lookup = self.variables[key]
        return lookup[variable.get()]

    def build_interface(self):
        header = self.ttk.Frame(self.window, padding=(16, 12))
        header.pack(fill="x")
        self.tkinter.Label(header, text="手动模拟输入 · 没有接入视频或识别模型",
                           foreground="#9c2500", font=("Microsoft YaHei", 16, "bold")).pack(anchor="w")
        self.ttk.Label(header, text="本窗口仅演示状态机。灯色、行人、车道、转弯许可均由下方人工设置。每 100 ms 更新一次。").pack(anchor="w", pady=(4, 0))
        controls = self.ttk.Frame(self.window, padding=(10, 0))
        controls.pack(fill="x")
        controls.columnconfigure(0, weight=1)
        controls.columnconfigure(1, weight=1)
        signal = self.group(controls, "信号灯与行人（模拟）", 0, 0)
        self.choices(signal, "signal_state", "信号灯", ("red", "yellow", "green", "unknown"), "red", 0)
        self.choices(signal, "signal_required", "是否需要遵守信号灯", ("yes", "no"), "yes", 1)
        self.choices(signal, "signal_bound", "灯与当前车道是否关联", ("yes", "no"), "yes", 2)
        self.choices(signal, "pedestrian", "行人冲突", ("present", "clear", "unknown"), "clear", 3)
        lane = self.group(controls, "左右车道边界（模拟）", 0, 1)
        self.choices(lane, "lane_state", "车道状态", ("estimated", "unknown"), "estimated", 0)
        self.choices(lane, "left_boundary", "左侧线型", ("solid", "dashed", "unknown"), "solid", 1)
        self.choices(lane, "right_boundary", "右侧线型", ("solid", "dashed", "unknown"), "solid", 2)
        self.choices(lane, "moving", "车辆是否正在行驶", ("yes", "no", "unknown"), "no", 3)
        adjacent = self.group(controls, "邻道情况（模拟）", 1, 0)
        self.choices(adjacent, "left_exists", "左邻道存在", ("yes", "no", "unknown"), "unknown", 0)
        self.choices(adjacent, "left_clear", "左邻道确认无障碍", ("yes", "no", "unknown"), "unknown", 1)
        self.choices(adjacent, "right_exists", "右邻道存在", ("yes", "no", "unknown"), "unknown", 2)
        self.choices(adjacent, "right_clear", "右邻道确认无障碍", ("yes", "no", "unknown"), "unknown", 3)
        request = self.group(controls, "驾驶请求与路口许可（模拟）", 1, 1)
        self.choices(request, "maneuver", "驾驶请求", ("keep_lane", "change_left", "change_right", "turn_left", "turn_right"), "keep_lane", 0)
        self.choices(request, "left_turn", "左转许可", ("allowed", "forbidden", "unknown"), "unknown", 1)
        self.choices(request, "right_turn", "右转许可", ("allowed", "forbidden", "unknown"), "unknown", 2)
        buttons = self.ttk.Frame(request)
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=4)
        for label, maneuver in (("请求左变道", "change_left"), ("请求右变道", "change_right"), ("保持车道", "keep_lane")):
            self.ttk.Button(buttons, text=label, command=lambda selected=maneuver: self.set_value("maneuver", selected)).pack(side="left", padx=3)
        toolbar = self.ttk.Frame(self.window, padding=(16, 8))
        toolbar.pack(fill="x")
        self.paused = self.tkinter.BooleanVar(value=False)
        self.ttk.Checkbutton(toolbar, text="暂停模拟输入（fresh=false）", variable=self.paused).pack(side="left", padx=(0, 12))
        self.ttk.Button(toolbar, text="模拟左侧实线跨越事件", command=lambda: self.cross_solid("left")).pack(side="left", padx=3)
        self.ttk.Button(toolbar, text="模拟右侧实线跨越事件", command=lambda: self.cross_solid("right")).pack(side="left", padx=3)
        self.ttk.Button(toolbar, text="重置状态机", command=self.reset).pack(side="right")
        result = self.ttk.LabelFrame(self.window, text="决策输出（来自模拟观察）", padding=12)
        result.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        self.action_label = self.tkinter.Label(result, text="等待首帧", font=("Microsoft YaHei", 22, "bold"), anchor="w")
        self.action_label.pack(fill="x")
        self.status_variable = self.tkinter.StringVar()
        self.ttk.Label(result, textvariable=self.status_variable, wraplength=980).pack(fill="x", pady=(4, 8))
        text_frame = self.ttk.Frame(result)
        text_frame.pack(fill="both", expand=True)
        self.details = self.tkinter.Text(text_frame, height=10, wrap="word", font=("Consolas", 10), state="disabled")
        scrollbar = self.ttk.Scrollbar(text_frame, command=self.details.yview)
        self.details.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.details.pack(side="left", fill="both", expand=True)

    def cross_solid(self, direction):
        self.crossing_seq += 1
        self.crossing = {"event_seq": self.crossing_seq, "direction": direction,
                         "timestamp_ns": time.monotonic_ns(), "crossed_boundary_type": "solid"}

    def reset(self):
        self.monitor.reset()
        self.session_id = "manual-" + uuid.uuid4().hex[:12]
        self.frame_seq = 0
        self.crossing_seq = 0
        self.crossing = {"event_seq": 0, "direction": "left", "timestamp_ns": 0,
                         "crossed_boundary_type": "unknown"}

    def observation(self):
        fresh = not self.paused.get()
        if fresh:
            self.frame_seq += 1
        return {
            "session_id": self.session_id, "frame_seq": self.frame_seq,
            "timestamp_ns": time.monotonic_ns(), "fresh": fresh, "source": "manual_simulation",
            "signal": {"required": optional_bool(self.value("signal_required")),
                       "state": self.value("signal_state"), "bound": optional_bool(self.value("signal_bound"))},
            "pedestrian": {"conflict": self.value("pedestrian")},
            "lane": {"state": self.value("lane_state"),
                     "left_boundary_type": self.value("left_boundary"),
                     "right_boundary_type": self.value("right_boundary"),
                     "left_adjacent_exists": optional_bool(self.value("left_exists")),
                     "right_adjacent_exists": optional_bool(self.value("right_exists")),
                     "left_adjacent_clear": optional_bool(self.value("left_clear")),
                     "right_adjacent_clear": optional_bool(self.value("right_clear"))},
            "request": {"maneuver": self.value("maneuver"), "source": "manual_simulation"},
            "turn": {"left": self.value("left_turn"), "right": self.value("right_turn")},
            "actual": {"moving": optional_bool(self.value("moving"))},
            "lane_change": dict(self.crossing),
        }

    def tick(self):
        if self.stopped:
            return
        try:
            observation = self.observation()
            decision = self.monitor.update(observation)
            self.writer.write({"mode": "manual_simulation", "simulated_input": True,
                               "observation": observation, "decision": decision})
            action = decision.get("recommended_action", "WAIT")
            alarms = decision.get("alarm_codes", [])
            color = "#b32020" if action == "STOP" or alarms else "#a56500" if action == "WAIT" else "#187034"
            self.action_label.configure(text=f"{ACTION_LABELS.get(action, action)}  /  {action}", foreground=color)
            self.status_variable.set(f"状态：{decision.get('state')}  ·  输入：{'暂停' if self.paused.get() else '人工模拟'}  ·  帧：{decision.get('frame_seq')}  ·  事件：{decision.get('event_seq')}  ·  告警：{', '.join(alarms) or '无'}")
            self.details.configure(state="normal")
            self.details.delete("1.0", "end")
            self.details.insert("1.0", json.dumps(decision, ensure_ascii=False, indent=2))
            self.details.configure(state="disabled")
        except Exception as error:
            self.action_label.configure(text="模拟更新失败", foreground="#b32020")
            self.status_variable.set(f"{type(error).__name__}: {error}")
            print(f"模拟更新失败：{error}", file=sys.stderr)
            self.stopped = True
            return
        self.timer = self.window.after(100, self.tick)

    def close(self):
        self.stopped = True
        if self.timer is not None:
            self.window.after_cancel(self.timer)
        self.writer.close()
        self.window.destroy()


def run_gui(log_paths):
    import tkinter
    from tkinter import ttk
    from driving_decision import DrivingDecisionMonitor

    window = tkinter.Tk()
    writer = JsonlWriter(log_paths)
    try:
        DecisionDemo(window, tkinter, ttk, DrivingDecisionMonitor(), writer)
        window.mainloop()
    finally:
        writer.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description="驾驶决策人工模拟与 JSON 回放；不接入视频或识别模型")
    parser.add_argument("--headless", action="store_true", help="无界面回放默认 decision_scenarios.json")
    parser.add_argument("--replay", type=Path, help="指定 JSON 场景回放；无需初始化 Tkinter")
    parser.add_argument("--output", type=Path, help="JSONL 输出路径（文件会被本轮输出覆盖）")
    parser.add_argument("--log", type=Path, help="JSONL 日志路径（文件会被本轮输出覆盖）")
    arguments = parser.parse_args()
    try:
        if arguments.replay is not None or arguments.headless:
            replay_path = arguments.replay or Path(__file__).with_name("decision_scenarios.json")
            return run_replay(replay_path, (arguments.output, arguments.log))
        return run_gui((arguments.output, arguments.log))
    except (OSError, ValueError, KeyError, TypeError, ImportError, RuntimeError) as error:
        print(f"驾驶决策演示无法完成：{type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
