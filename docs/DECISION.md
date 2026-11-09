# Windows 行为决策状态机与工程优先级

## 当前运行入口：Windows 本地视频自动识别与判断

当前入口是 `run_video_decision.cmd` / `video_driving.py`，Windows 本地视频使用 `video_behavior.py` 的 `windows-video-behavior-v2.1`，接入 YOLOv5 检测、YOLOP 车道分割、目标跟踪和分级驾驶提示。当前规则、模型契约、完整视频验证和限制见 [WINDOWS_VIDEO_V2.md](WINDOWS_VIDEO_V2.md)。本文 B～D 节及后续接口规划保留旧 `driving_decision.py` 的人工模拟／板端适配说明，两套状态含义应分别使用。

### A. 播放真实视频并自动识别

在资源管理器双击 `run_video_decision.cmd`，从文件对话框选择视频；也可在 PowerShell 指定已有原片：

```powershell
cd 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host'
.\run_video_decision.cmd 'D:\buduinetdiskdownload\video_20260929_112507.mp4'
```

直接 Python 启动的等效命令：

```powershell
py -3.11 video_driving.py 'D:\buduinetdiskdownload\video_20260929_112507.mp4'
```

当前窗口默认原速播放＋后台分析，优先 DirectML GPU；左侧显示当前视频，右侧显示同帧检测结果及分析延迟。需要逐采样帧核查时使用 `--playback offline`，无窗口默认离线模式。当前 GPU/CPU 耗时、过期处理和比赛要求差距见 [WINDOWS_PERFORMANCE.md](WINDOWS_PERFORMANCE.md)。

程序使用现有 Python 3.11、OpenCV 和 NumPy；不要求 CUDA 或训练环境。中文叠加使用 Pillow 和 Windows 字体，未取得中文绘制依赖时显示英文信息。已有 `models/yolov5s.onnx` 已下载并执行真实推理验证；只有模型文件缺失、需要恢复时才执行：

```powershell
py -3.11 windows_object_detector.py --download
```

默认加载 `windows_video_112507.json`。它记录信号搜索区、车道处理区域、相机中心和近端车道参考行，没有人工时间轴。行人冲突来自轨迹与路径几何，旧的大矩形冲突区不再参与 Windows 视频决策。换视频后应核对相机参数，必要时用 `T` 调整信号区域。

| 按键 | 用途 |
| --- | --- |
| `Space` | 暂停/恢复视频；暂停保持当前画面，不新增观测 |
| `R` | 从 `--start` 指定位置重新播放，并重置感知和决策历史；默认从头播放 |
| `S` | 保存当前叠加 JPG 与同帧结果 JSON |
| `T` | 框选交通灯搜索区，记录区域；区域本身不证明灯与本车车道已自动关联 |
| `A` / `D` | 立即判断一次左/右变道，显示依据；再次按键重新判断 |
| `K` | 清除上一次变道请求 |
| `Q` / `Esc` | 退出 |

先点击视频窗口取得键盘焦点，`A/D/K` 支持大小写且暂停时可用。框选区域后用 Enter/Space 确认；取消框选保留原配置。变道提示保留按键时的判断，后续标为历史结果；它不会随着新画面自动改成允许。

每次运行默认新建 `video_decision_runs/日期时间_随机标识/`，保存 `config.json`、逐帧 `observations.jsonl`、`summary.json`、`first_frame.jpg`；变道请求另写 `lane_requests.jsonl`，`S` 保存同帧 JPG/JSON。汇总记录决策版本、源码指纹和车道模型哈希。需要导出叠加视频时添加 `--save-video`，生成 `annotated.mp4`。可用 `--output-dir` 指定目录，重复使用同一目录会覆盖本轮固定文件。

```powershell
py -3.11 video_driving.py 'D:\buduinetdiskdownload\video_20260929_112507.mp4' --save-video
py -3.11 video_driving.py 'D:\buduinetdiskdownload\video_20260929_112507.mp4' --headless --seconds 12 --sample-fps 5 --output-dir video_decision_runs/check_12s
```

模型来自 [Ultralytics YOLOv5 v7.0 官方资源](https://github.com/ultralytics/yolov5/releases/download/v7.0/yolov5s.onnx)，为 COCO80 检测模型，真实输出 `[1,25200,85]`。`models/yolov5s.onnx.json` 保存类别顺序、预处理、来源和 SHA256：`8bc25a47abdbe2fd23e30eb948746bf856b031ea3cc815166687f52db9f8c659`。Windows 当前优先 DirectML GPU、保留 OpenCV CPU 回退，RGB/255、640×640 letterbox 和按类别 NMS；这是电脑端 ONNX 模型，不是直接在 Windows 加载板端 `.rknn`。

车道分割另外使用官方 YOLOP 的 BDD100K 预训练模型 `models/yolop-640-640.onnx`，对应清单为 `.onnx.json`，没有本地重新训练。Windows 当前优先 ONNX Runtime DirectML GPU，CPU 保留为回退与基线。`summary.json` 区分分析耗时、显示 FPS 和分析延迟，不能视为板端实时性能。

本轮完整视频证据在 `video_decision_runs/v21_verified_*/`，规则回归命令为 `py -3.11 verify_video_behavior.py`。旧 `windows_video_v1/` 的高比例 STOP/WAIT 是旧版误报基线，不能代表当前版本。输出数量也不是人工标注准确率。

| 当前观测/判断 | 已接入内容与边界 |
| --- | --- |
| 人、车辆、交通灯 | 真实 YOLO 检测框；远距离小灯仍会漏检，COCO 模型不输出灯色或箭头方向 |
| 灯色 | 红/黄/绿及圆灯、直行/左右箭头形状原型；太小或冲突的证据保留未知 |
| 本车信号关联 | 水平灯框优先、ROI 内成对灯兜底，按方向选择；完整的灯与车道语义关联尚未完成 |
| 信号漏检 | 最近颜色保留最多 1.5 秒；过期但仍处于已见信号上下文时显示注意观察，不自动恢复为已确认绿灯 |
| 起始尚未看到灯 | 未发现需要减速/停车的冲突时显示 NORMAL，并附注信号未确认；它不证明道路无灯或全面安全 |
| 当前车道与虚实线 | YOLOP 分割定位标线，再拟合边界；原图油漆连续性判断实/虚线。遮挡、单短划线或缺少几何时保留未知 |
| 行人冲突 | 跟踪脚底、横向运动与本车路径，区分注意/减速/停车；确认离开立即解除，骑行身份短暂保持以减少漏检误报 |
| 变道/转弯 | A/D 单次回应实线、近车、未知或可见区域未见冲突；仅前视范围。绿灯记录为信号条件，不授予完整转弯许可 |
| 实线违规 | 可拒绝当前实线方向的变道请求；缺少事件当时被跨边界的类型快照时，不输出已实际跨实线的结论 |
| 执行 | 当前为预览建议与日志，尚未下发车辆/FPGA 执行命令 |

### B. 旧人工模拟窗口与规则回归

下面入口用于测试规则边界，窗口中的灯色、线型和行人状态由人工选择，不是本地视频自动识别：

```powershell
cd 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host'
py -3.11 driving_decision_demo.py --log decision_logs/manual.jsonl
```

窗口明确显示“手动模拟输入”。可设置灯色、行人冲突、左右边界、邻道、驾驶请求和转弯许可；每 100 ms 更新。模拟输入用于验证规则，不作为自动识别结果。

| 操作 | 初版结果 |
| --- | --- |
| 保持默认红灯 | `RED_STOP / STOP` |
| 改为绿灯，行人清空、车道有效 | 连续确认后 `GREEN_GO / GO` |
| 绿灯时设置“有冲突行人” | 立即 `YIELD_PEDESTRIAN / STOP` |
| 行人改为未知或短时漏检 | 已确认风险保持；确认清空后才释放 |
| 左实线，点击“请求左变道” | `KEEP_LANE / WAIT`；左变道许可 `forbidden` |
| 点击“模拟左侧实线跨越事件” | `forced_solid_lane_change` 告警；同一事件不重复触发 |
| 改为虚线，但邻道未知 | 许可 `unknown`，保持等待 |
| 虚线、邻道存在且清空，再请求该侧变道 | 连续确认后 `CHANGE_LEFT/CHANGE_RIGHT` |
| 请求左转，但左转许可未知 | `WAIT_TURN / WAIT`；明确许可后再确认 |
| 黄灯并模拟正在行驶 | `YELLOW_WAIT`，按比赛演示规则输出 `yellow_forced_drive` |
| 勾选暂停输入 | `WAIT_INPUT / WAIT`，恢复后重新确认 |

`KEEP_LANE / WAIT` 表示拒绝当前变道请求并保持车道约束，不是额外要求停车。左/右实线也不自动禁止合法路口转弯，转弯许可单独输入。

#### 无窗口规则回放

```powershell
py -3.11 driving_decision_demo.py --headless --output decision_logs/replay.jsonl
```

默认读取 `decision_scenarios.json`：6 组场景、76 帧、24 个带期望的检查帧。成功返回 0，期望不符返回 1，输入/运行错误返回 2。也可显式指定 `--replay decision_scenarios.json`。GUI 和回放的 `--log/--output` 覆盖指定输出文件；实时预览日志使用追加模式。

每条 JSONL 包含输入观测、输出决策、模拟/回放来源。回放支持 `scenarios[].frames[]`，每项含完整 `observation` 和部分 `expected` 字段；`repeat` 自动递增帧号和时间，期望检查最后一次。跨线事件时间不随 repeat 刷新。

### C. 接入已有 RK3568 真实检测预览

先按 `COLOR.md` 恢复板端 matched 服务和到当前 Windows 的端口转发。Windows 的 `127.0.0.1` 只指向 Windows 本机；如果转发只存在 Ubuntu 虚拟机，需建立 Windows 可达的转发或使用可达的服务地址。

安装已有预览依赖（仅在缺失时）：

```powershell
py -3.11 -m pip install opencv-python numpy
py -3.11 preview_stream.py --host 127.0.0.1 --port 9001 --road-scene traffic_lane_scene.json --traffic-roi-fallback --decision-config decision_preview.example.json --decision-log decision_logs/live.jsonl
```

示例配置保留 `signal_bound=false`、`pedestrian_conflict_roi=null`；因此默认会等待，提示缺失证据。需针对当前场景明确：

- `signal_required`：本车是否处于需要遵守该信号的受控场景；普通无灯道路可为 false。
- `signal_bound`：人工确认现有灯色 ROI 对应本车目标动作后再为 true，日志标记为 `scene_configuration`。
- `pedestrian_conflict_roi`：归一化 `[left, top, right, bottom]`，圈定本车路径/横穿冲突区域；不是整张图里的所有行人。

ROI 内真实 person 的脚底中心或框交叠触发冲突；没有 ROI 时保留未知。适配器支持可选 `pedestrian_use_lane_corridor`，用已确认本车 `ego_polygon` 限制行人脚底，走廊不可用时回退 ROI 并记录来源；现有 matched 示例仍应结合实际车道质量选择是否启用。只消费真实 matched 检测，不将显示外推框作为风险证据。当前几何规则需要实际场景校验，后续再补目标跟踪、横穿预测和路径几何。

板端 matched 预览适配侧未接入 Windows 本地入口的线型原型，当前线型、邻道、转弯许可和本车运动均保留未知；规则边界仍可在模拟窗口验证。运行时不使用视频人工时间轴推断行为。灯色依然只有红/绿/未知，黄灯规则目前由模拟输入验证。

预览中按 `S` 可保存带动作、原因和告警的 `_decision.png`。当前接入发生在显示循环，接收端只保留最新帧，显示忙时可能跳过中间观测；该初版用于建议和回放验证。后续实际执行需把决策移到独立逐帧消费队列，并测量丢帧和事件遗漏。

### D. 内核行为与接口

`DrivingDecisionMonitor(config=None).update(observation)` 不依赖图像、OpenCV、Tkinter、NPU 或训练框架。主要状态有 `WAIT_INPUT`、`WAIT_SIGNAL`、`WAIT_PEDESTRIAN`、`WAIT_LANE`、`WAIT_CONFIRM`、`RED_STOP`、`YIELD_PEDESTRIAN`、`GREEN_GO`、`ROAD_GO`、`KEEP_LANE`、`WAIT_TURN` 和 `YELLOW_WAIT`。

默认输入超时 500 ms，行人清空确认 3 帧，通行条件确认 3 帧，告警显示保持 2 秒；三帧清空和三帧通行确认的第一帧重叠，因此起步绿灯且清空通常第 5 帧通行。重复帧不累计确认。会话改变、帧/时间回退、大间隔重置历史；同一会话按实际跨线 `event_seq` 去重。

变道事件必须提供当时被跨越边界的 `crossed_boundary_type`、事件时间和序号，不能用新车道当前边界替代。事件缺时间、来自未来或过期时不生成违规告警。红灯行驶本身不等同于越线；`red_light_crossing` 需要 `actual.crossed_stop_line=true`。

输出含 `version`、帧身份、`state`、`recommended_action`、`permissions`、`reasons`、`alarm_codes`、`new_alarm_codes`、`event_seq`、`input_sources`。`permissions` 区分 allowed/forbidden/unknown；输出是规则结论，`confidence=null`，不伪造识别准确率。

已完成验证：6 组场景回放、27 项内核/真实 Tk 控件回归、真实内核适配器检查和预览循环模拟。尚未完成板端在线视频验收和 FPGA 命令输出。

## 1. 当前目标

用户已澄清当前目标：优先复用现有 YOLO 检测和车道/灯色原型，在 C++/Python 中实现礼让行人、实线约束、红灯停车和绿灯条件通行的行为判断。此阶段无需先重训车辆/行人检测模型，也无需为编写规则准备 CUDA 环境。

Windows 可以完成视频回放、规则编写和测试。Python 更便于复用现有 `traffic_light.py`、`lane_detection.py` 和预览逻辑。稳定后再迁移到 RK3568，按实测瓶颈决定是否改写 C++。

本文记录 Windows 本地视频自动识别、独立状态机及后续实施顺序。真实视频观测和预览建议已接通，实际控制输出尚未接通。

## 2. 感知、状态与决策分工

| 层 | 输入 | 输出 | 当前工程情况 |
| --- | --- | --- | --- |
| 感知 | 当前图像、COCO 检测框 | 行人/车辆/灯框、灯色、当前车道区域、变道证据 | Windows 本地 ONNX 与板端 RKNN 链路；车道/灯色继续使用 Python 原型 |
| 场景状态 | 同帧感知、连续历史、场景约束 | 本车相关信号、行人冲突、左右边界类型、动作请求和观测有效性 | 需要补充关联、跟踪和缺失字段 |
| 行为决策 | 有效场景状态、导航/按键意图 | 停车、礼让、等待、通行、变道许可、告警及原因 | Windows 视频入口已显示真实观测决策；缺少邻道/转弯等证据时保留未知 |
| 输出与执行 | 决策及事件序号 | 预览提示、日志、FPGA LED/箭头/告警 | 统一动作回 FPGA 尚未完成 |

“检测到交通灯”不包含灯色，“检测到行人”不等于行人与本车路径冲突。现有 `GO_SIGNAL_ONLY` 只表示绿灯观测，不能直接作为最终通行许可。

场景需明确 `signal_required`：受控路口需要本车信号，普通道路不能因没有交通灯而一律等待。只检查当前请求动作所需的约束，例如保持车道直行不要求先知道两侧邻道是否可变道。

## 3. 能复用的观测与缺口

| 目标 | 可复用输入 | 仍需补充 |
| --- | --- | --- |
| 红停绿行 | COCO 灯框、`TrafficLightMonitor` 的红/绿/未知、多帧确认与超时 | 本车信号关联；黄灯和箭头；信号过期/冲突处理 |
| 礼让行人 | COCO `person` 框和同帧坐标 | 行人轨迹、横穿/冲突区域、本车路径、短时漏检保持和解除条件 |
| 实线约束 | 本车区域、边界轨迹、LEFT/RIGHT 变道事件及序号 | 左右边界的 solid/dashed/unknown；跨线方向与对应边界关联 |
| 转弯许可 | 灯色、当前区域、外部动作请求 | 车道导向、对应箭头/信号、转弯路径及行人/障碍物冲突 |
| 违规告警 | 实际观测的变道事件 | 动作请求和实际行为、模拟/识别来源、告警触发与事件去重 |

总车道数继续作为辅助。行为规则优先使用当前区域、边界和冲突证据。

## 4. 第一版规则与优先级

先输出提示和结构化日志，规则条件必须来自明确的观测或标记来源的模拟输入。

| 条件 | 建议状态/动作 | 说明 |
| --- | --- | --- |
| 本车有效信号为红 | `STOP` | 记录当前信号和停车原因 |
| 行人与本车目标路径存在有效冲突 | `YIELD_PEDESTRIAN` 或 `STOP` | 绿灯期间也要处理冲突；路边行人不能一律作为冲突 |
| 信号、路径或关键约束未知/过期 | `WAIT` | 不把丢帧或未知直接转换成通行许可 |
| 请求跨越实线变道 | `lane_change_allowed=false` | 保留停车/通行主状态，同时拒绝该变道请求 |
| 已实际跨越实线 | `forced_solid_lane_change` 告警 | 必须结合变道事件和被跨越边界；按事件序号去重 |
| 请求跨越虚线，邻道存在且无有效冲突 | 可以给出变道许可 | 虚线只解除线型约束，不证明邻道安全 |
| 本车有效绿灯，目标动作合法，路径与冲突证据有效 | `GO` 或相应转向建议 | 不允许单凭绿灯跳过其他条件 |

多个原因应同时记录，主动作按规则优先级生成，告警和变道许可独立输出。停止/礼让解除需重新确认；短时漏检不应立即清除行人风险。

违规跨线判断要保存跨越前的边界 ID 和线型，并与变道事件绑定。不能在变道完成后拿新车道的边界线型判定刚才的跨越是否合法。

实线约束与路口转弯许可应分别判断。第一版“实线不能转弯”应拆成“不得跨实线变道”和“路口转弯是否符合车道导向/对应信号”。如果题目另规定某类实线车道只能直行，将其作为显式比赛规则配置，不能仅从任意实线推断整条道路禁止转弯。

## 5. 状态机接口与后续扩展

`driving_decision.py` 已通过纯数据接口接入现有感知。初版使用 `timestamp_ns` 作为源时间，接入端用接收单调时间计算 `fresh`；下表保留后续跟踪、导航与信号方向关联的扩展字段。

| 输入组 | 建议字段 |
| --- | --- |
| 帧身份 | `session_id`, `frame_seq`, `source_timestamp_ns`, `receive_monotonic_ns`, `fresh` |
| 本车相关信号 | `signal_required`, `light_state`, `light_direction`, `light_lane_binding`, `light_confidence` |
| 车道 | `lane_position`, `lane_polygon`, `left_boundary_type`, `right_boundary_type`, `lane_confidence` |
| 行人和邻道 | `pedestrian_tracks`, `pedestrian_conflict`, `adjacent_lane_exists`, `adjacent_lane_clear` |
| 请求与实际行为 | `requested_maneuver`, `actual_motion`, `lane_change`, `lane_change_event_seq` |
| 来源 | `observed`, `manual_simulation`, `navigation`，以及每项证据/配置版本 |

建议输出：

```text
frame_seq, timestamp_ns, decision_version
state, recommended_action
lane_change_allowed, turn_left_allowed, turn_right_allowed
alarm_codes, reasons, confidence
event_seq, input_sources
```

变道/转弯许可采用 `allowed/forbidden/unknown`，保留未知与明确禁止的区别。板端源时间用于轨迹和事件顺序，Windows 接收端的单调时钟用于新鲜度；不能直接相减两台机器的时间。重连、时间回退、重播和帧超时需清理历史，并按 `(session_id, event_seq)` 去重。

按键或回放 JSON 可以模拟灯色、边界类型和驾驶动作，用于先验证规则。模拟结果必须明确标记，不能当作自动识别效果。视频人工时间轴只能用于评估；自动模式不能按视频时间直接输出预设动作。

## 6. Windows 验证流程

| 步骤 | 工作 | 验收 |
| --- | --- | --- |
| 1 | 用结构化事件/模拟输入验证纯状态机 | 红灯、绿灯行人冲突、未知信号、实线请求、实线实际跨越等规则有明确期望输出 |
| 2 | Windows 本地视频通过 `video_driving.py` 接入真实检测与几何原型 | 在同帧显示灯色、车道、行人冲突、建议动作和原因；记录未知与场景假设 |
| 3 | 补跟踪、冲突 ROI、状态保持/解除和超时 | 对漏检、抖动、时间回退、视频重播、转弯与真实变道分别回归 |
| 4 | 记录每帧观测和决策 JSON/CSV | 相同输入可重复得到相同决定；能追溯误判源于感知还是规则 |
| 5 | 移至 RK3568，接 FPGA 输出 | 不依赖 Windows/Ubuntu 分析也能输出动作状态；LED/箭头与日志一致 |

Windows 不能直接加载当前 `.rknn` 使用本机 NVIDIA GPU推理。电脑端可以使用 PyTorch/ONNX 同类模型，或者回放保存的板端检测结果。最终还需用真实板端观测验证，避免两端检测差异被误认为规则错误。

## 7. 更新后的工程优先级

| 优先级 | 工作 | 何时训练 |
| --- | --- | --- |
| P0 | 固定现有输入、帧身份、检测模型和契约 | 先保留可用模型 |
| P1 | Windows 状态机、视频回放、行人冲突和可解释预览 | 规则开发不要求训练 |
| P1 | 补齐左右边界类型、信号方向/对应关系等必要观测 | 先验证算法和样本，可靠性不足时针对性训练 |
| P2 | RK3568 迁移、FPGA LED/箭头/按键/告警闭环 | 按实机精度和性能决定优化 |
| P3 | 使用 BDD100K/自采数据优化车道或交通灯模型 | 针对回归集证明的感知缺口 |
| P4 | 四路、夜间、复杂路口和导航扩展 | 基础行为闭环稳定后推进 |

BDD100K 可作为车道/可行驶区域感知数据来源；具体能训练什么取决于本机图片与对应标注，不承担红灯停车、礼让或禁止跨线的规则定义。

## 8. 本地 BDD100K 核查

用户数据目录：`E:\BaiduNetdiskDownload\车牌数据集\BDD100K`。本轮只读检查，未解压、转换或开始训练。

| 本地内容 | 核查结果 | 用途 |
| --- | --- | --- |
| `bdd100k_images_` | 10k 图片包，train 7000、val 1000、test 2000 | 原始图像；其拆分与 100k 标签不能直接按同名目录配对 |
| `bdd100k_labels` | 100k 旧格式 JSON；抽查含 `frames[0].objects`、`lane/*`、`poly2d`、`attributes.style` | 提取车道曲线和 solid/dashed；不是现成 YOLO segmentation TXT |
| `bdd100k_drivable_maps` | train/val 可行驶区域图；直接/替代区域与背景不包含边界虚实线类型 | 可训练道路区域，辅助本车走廊/行人道路位置，不能代替车道线标注 |
| 抽查图像/区域图 | 1280×720 | 可用于预训练；板端 RGB565/1920×1080 校准仍应使用真实链路数据 |

按文件名只做配对候选检查：10k/train 中 2976 张对应 100k/train JSON，454 张对应 100k/val JSON；10k/val 与本地 100k JSON 文件名没有交集；10k/test 中 830 张对应 100k/test JSON。候选数量不是已确认有效车道标注数量，尚未逐个检查所有 JSON 的车道对象。

配对 train/`00067cfb-e535423e.json` 抽查包含 `lane/single white` 的 dashed/solid，以及 crosswalk/road curb；配对 val/`b1d0091f-f2c2d2ae.json` 抽查也包含车道对象。因此可以整理一批车道训练/验证数据，但不能直接把现有 10k 的 train/val 文件夹填进之前的示例配置。

后续处理顺序：按图像文件名匹配 JSON → 保留 100k 标签原 train/val/test 隔离 → 排除缺失/无效车道标注 → 按类别筛选车道标线、路缘和斑马线 → 正确处理 `poly2d` 的直线/曲线指令 → 按选定车道网络生成标签。车道曲线若转为分割 mask/窄多边形，需要明确线宽与光栅化规则，不把开放曲线直接当作填充多边形。也可补齐对应的 100k 图片包。

首先使用已有观测完成决策层。BDD100K 优先用于回归和补充已证实的车道感知缺口；最终测试视频与测试图片不用于训练或反复调参。
