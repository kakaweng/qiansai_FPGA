# RK3568 与 FPGA PCIe 分阶段验证

2026-11-02新增FPGA暗光增强v2：下载独立位流 `06_hdmi_loop_1/hdmi_loop.lowlight_v2.sbit`，KEY1切至暗光，物理HDMI左上显示默认 `LOW 2`，KEY2/3增减三档强度。新增轻量保边降噪、连续增益与高光保护；原方向键、LF01灯及三路采集保留。**本轮增强物理HDMI输出及原彩色UDP抓图，PCIe→RK识别/Ubuntu预览继续原始RGB565；无需重训或更新预览。** 操作和参考对照见 [LOWLIGHT_V2.md](../../06_hdmi_loop_1/LOWLIGHT_V2.md)。218,573个像素/时序向量、实际顶层和按键验证通过，PDS生成成功；HDMI像素域同域setup/hold通过，整个工程仍有其他域/跨域时序违例，上板验收待完成，详见 [LOWLIGHT_V2_BUILD.md](../../06_hdmi_loop_1/LOWLIGHT_V2_BUILD.md)。旧位流保持可回退。

2026-11-02新增辅助驾驶实体灯反馈：三业务灯表示左/直/右请求、模拟通行、停车、行人礼让、实线违规报警及未知状态；原KEY1～4和KEY5～7保留。新联调位流为 `06_hdmi_loop_1/hdmi_loop.drive_feedback_v1.sbit`；下载并恢复PCIe及当前前视/按键服务后，执行 `sudo -v && bash /mnt/hgfs/pcie_host/deploy_fpga_feedback.sh`，再用 `preview_front_feedback.sh` 开启同一预览的后台灯光回传。完整灯义、逐灯测试和边界见 [FPGA_DRIVE_FEEDBACK.md](FPGA_DRIVE_FEEDBACK.md)。PDS生成及本地RTL/36项相关Python回归通过，当前时序仍有违例、尚未实机验收；旧位流保留。

2026-11-02赛题演示准备：新增独立的**三路纯传输模式**，复用留存ethernet_v6静态程序，不加载识别模型；入口 `sudo -v && bash /mnt/hgfs/pcie_host/deploy_three_video_rk3568.sh start`，预览 `bash /mnt/hgfs/pcie_host/preview_three_video.sh`，切回单路用同一部署脚本的 `restore-front`。完整重启/Windows输入指令及指标边界见 [THREE_VIDEO_DISPLAY.md](THREE_VIDEO_DISPLAY.md)。已导出9组11个干净演示视频并完整解码，浏览 `demo_cases_20261102/index.html`，赛题映射与改进建议见 [COMPETITION_DEMO_PLAN.md](COMPETITION_DEMO_PLAN.md)。本轮6项相关回归及Bash语法通过，未直接连接RK，不能据本机测试声称实机FPS/总延迟已通过。DevEco本地化方案见 [DEVECO_LOCAL_APP_PLAN.md](DEVECO_LOCAL_APP_PLAN.md)。

2026-11-02方向键预览反馈修正：冻结推演方向已更新时，绘制路线与按钮仍引用普通路口旧方向；现统一为冻结方向。关闭旧窗口后重新执行`bash /mnt/hgfs/pcie_host/preview_front_fpga_keys.sh`，开头应显示`FPGA_KEY_PREVIEW_VERSION route-display-v2`。仅更新预览，无需重烧v2、重启RK或更换模型。新增BASELINE/EVENT/APPLIED及连接异常日志；独立观察按键用`python3 /mnt/hgfs/pcie_host/fpga_junction_keys.py --watch --seconds 20`。25项相关测试通过；日志解释见[FPGA_JUNCTION_KEYS.md](FPGA_JUNCTION_KEYS.md)。

2026-11-02 FPGA路口方向键v2：保留KEY1～4原视频与暗光增强功能，改用KEY5（J16）左转、KEY6（J19）直行、KEY7（H20）右转；独立只读BAR0按键通道，原N分析/G播放及J/I/L保留。新位流为 `06_hdmi_loop_1/hdmi_loop.junction_keys_v2.sbit`，上一版占用原按键的v1作废；下载并让RK重新枚举PCIe后，执行 `sudo -v && bash /mnt/hgfs/pcie_host/deploy_fpga_junction_keys.sh`，通过后打开 `preview_front_fpga_keys.sh`。安装说明见 [FPGA_JUNCTION_KEYS.md](FPGA_JUNCTION_KEYS.md)。原三路位流及配套ethernet_v6程序位置见 [FIRMWARE_CATALOG.md](FIRMWARE_CATALOG.md)。新增顶层新旧按键隔离仿真通过，v2构建及验证记录见FPGA目录的 `JUNCTION_KEYS_BUILD_V2.md`。

2026-11-02冻结暂停误报修正：原视频已暂停时，板端YOLO框、HOLD颜色及车道混色仍在刷新，旧灰度比较可能不断重置稳定计时。预览现按已知绘制调色板与混色方式排除这些变化，保留真实移动/慢漂移检查和同源时间配对，并避免重复刷新增加开销。**关闭旧预览，执行 `bash /mnt/hgfs/pcie_host/update_frozen_junction_preview.sh`，通过后重新打开 `bash /mnt/hgfs/pcie_host/preview_pie_anticipation.sh`。** 无需重训、切换RK服务或修改FPGA。Windows完整180项回归通过；0004的53秒/162秒加入动态模拟板端叠加，两处均在暂停后约0.92秒完成快照；RK实机待确认。详见 [FROZEN_JUNCTION_PREVIEW.md](FROZEN_JUNCTION_PREVIEW.md)。

2026-11-02新增HDMI冻结路口预演：播放时保留行人运动，Windows暂停源视频后，在预览按M进入、N收齐当前识别并固定场景，J/I/L选择方向、G播放三秒推演，B暂停全场景、R重播、M返回实时。人物沿暂停前运动继续示意，车辆与人物预测交汇时礼让；原实时识别及变道请求保留。针对0004的162秒侧方横道死区增加仅限快照的位置估计，未改变实时关联规则。**关闭旧预览，执行 `bash /mnt/hgfs/pcie_host/update_frozen_junction_preview.sh`，看到 `FROZEN_JUNCTION_PREVIEW_PASS` 后打开 `preview_pie_anticipation.sh`。** 复用板端服务与模型，不改FPGA。Windows174项回归和53秒/162秒实际源画面模拟延迟回放通过，实机待验收；操作与限制见 [FROZEN_JUNCTION_PREVIEW.md](FROZEN_JUNCTION_PREVIEW.md)。

2026-11-02行人可见性与当前行为修正：针对 `set01/video_0004` 53 秒横穿者在主场景消失，撤销未定位行人的小窗隔离；真实检测人形保留在2.5D中，虚环表示位置估计，连续背景补偿运动确认横穿并显示方向，可触发本地预演停车礼让。路口左下新增当前行为和独立的 PIE 提前预测状态；不把运动规则冒充训练模型输出。Windows 163 项回归通过，0004 红衣行人 12 个已交付采样均可见、保持同一编号，53 秒附近显示横穿和礼让；非 RK3568 验收。**关闭旧预览，运行 `bash /mnt/hgfs/pcie_host/update_junction_alignment_preview.sh`，通过后运行 `bash /mnt/hgfs/pcie_host/preview_pie_anticipation.sh`。** 仅更新预览，复用板端模型与服务。说明、标注对照和限制见 [PEDESTRIAN_BEHAVIOR_PREVIEW.md](PEDESTRIAN_BEHAVIOR_PREVIEW.md)。

2026-11-02横道与行人空间关联修正：根据 PIE set01 的 `video_0003`、`video_0004` 对照，移除“行人画面左右位置直接决定横道”和邻近人群归属扩散；改用同源分割横道带与脚点关联，允许新几何纠正旧归属，排除图像极下沿反光。未定位行人的显示以顶部行为版为准。近处位置未确定时不视为路线清空；路口仍为含推测的模板。验证、范围及限制见 [JUNCTION_SPATIAL_ASSOCIATION.md](JUNCTION_SPATIAL_ASSOCIATION.md)；此条取代下方 11-01 的按画面左右分配人群规则。

2026-11-02更新脚本测试修正：修复Ubuntu行人协议回归偶发 `Controller condition not met`：测试的浮点秒转整数毫秒可能截掉1ms，形成“行人时间晚于帧时间”的非法报文。改为整数毫秒贯穿构造，正式程序时间校验保持。已遇到 `JUNCTION_ROUTE_FAILED line=8` 的环境可重新执行同一个 `update_junction_route_rk3568.sh`。

2026-11-02路口路线冲突修正：普通预演沿完整左转/直行/右转路线检查相关横道与行人，确认冲突停车礼让，疑似冲突/过期结果停车确认；短时漏检保持，至少两次新观测且跨度500ms确认清空后恢复。板端脚点匹配补足有双侧像素支持的小空洞。**关闭旧预览，执行 `sudo -v && bash /mnt/hgfs/pcie_host/update_junction_route_rk3568.sh`，看到 `JUNCTION_ROUTE_PASS` 后打开 `preview_pie_anticipation.sh`。** 复用已有模型；范围、证据期限和验证说明见 [JUNCTION_ROUTE_CONFLICT.md](JUNCTION_ROUTE_CONFLICT.md)。

2026-11-01识别恢复修正：针对YOLO框和普通2.5D目标消失、车道一直待确认，已按实机日志修复前视YOLO排队前取旧帧，以及三模型分析间隔超过500ms导致车道确认不断重置的问题；增加灰色HOLD框与普通视图独立目标保持。**关闭旧预览后执行 `sudo -v && bash /mnt/hgfs/pcie_host/repair_front_perception_rk3568.sh`，看到 `FRONT_PERCEPTION_REPAIR_PASS` 后打开 `preview_pie_anticipation.sh`。本轮包含板端修改，仅重开预览不足以应用。** 模型复用，完整说明见 [FRONT_PERCEPTION_REPAIR.md](FRONT_PERCEPTION_REPAIR.md)。

2026-11-01路口位置修正：修复中间偏右的人群被放到车前横道的问题，右侧人群映射到右转出口横道；连续跟踪保持横道归属，新出现的同侧、相近深度行人参考已有群体，位置预测限于对应横道。方向按钮不会搬动人群。本次仅更新预览，已安装感知增强版本时关闭窗口后重新运行 `bash /mnt/hgfs/pcie_host/preview_pie_anticipation.sh` 即可，无需重编译板端。

2026-11-01最新修正：M路口视图改为“感知增强预演”，允许道路模板补全、目标平滑及短时预测，始终标注“含推测”。完整四向横道替代碎片mask；稳定车道布局最多保持5秒；人车短时漏检不立即消失，最多外推1秒、2.6秒后清除。板端新增仅供展示的 `FWD1 / display_world` 通道，提供最多2.5秒内的目标，避免慢推理结果在预览前就被清空；驾驶规则继续使用原新鲜度条件。G/B/R仅操作本地路口动画，未知感知可用示意布局继续预演；A/D变道请求仍提交RK3568。**关闭旧预览后执行 `sudo -v && bash /mnt/hgfs/pcie_host/update_junction_presentation_rk3568.sh`，看到 `JUNCTION_PRESENTATION_PASS` 后再运行 `bash /mnt/hgfs/pcie_host/preview_pie_anticipation.sh`。** 复用已有模型，不重训、不修改FPGA。开发机连续回放和协议测试通过，RK3568实际效果待更新验收；范围及命令见 [OBSERVED_JUNCTION_RK3568.md](OBSERVED_JUNCTION_RK3568.md)。

2026-10-28：针对三路辅助识别接入后同片车道跳变，已修正v6计算调度：排队车道优先、获计算机会后取最新帧，等待期间继续替换pending；目标检测保留有界执行机会。原模型、500/350/750ms时序阈值不变。统一预览后台状态轮询改为100ms，超时显示“已过期”。本地竞争/取消/错误释放测试与ARM64编译通过，实机改善待验收。应用C更新需重跑`integrate_drive_assist_rk3568.sh`，详细步骤见[LANE_SCHEDULING_FIX.md](LANE_SCHEDULING_FIX.md)。

2026-10-28：驾驶辅助与识别画面已加入同一预览窗口，入口`bash /mnt/hgfs/pcie_host/preview_drive_assist.sh`。支持中文状态、按钮及空格/A/D/K等快捷键，请求仍由RK3568判断，HTTP不阻塞视频刷新。更换Windows任意视频源使用原启动器的弹窗或`--front/--right`参数，操作见[UNIFIED_DRIVE_PREVIEW.md](UNIFIED_DRIVE_PREVIEW.md)。本轮无需重训或重启板端第六版服务。

2026-10-28：用户确认同步v5三路输出正常；按要求跳过区域标定，新增左右车辆检测、板端变道请求反馈、实线报警和模拟车况中文面板v6。入口`sudo -v && bash /mnt/hgfs/pcie_host/integrate_drive_assist_rk3568.sh`，说明见[DRIVE_ASSIST_RK3568.md](DRIVE_ASSIST_RK3568.md)。本地线程/HTTP状态机/页面脚本测试和ARM64交叉编译通过，v6实机效果与帧率待验收；视觉工具中止，未完成页面视觉验收。复用现有模型，侧方未标定时不给出安全变道许可，保留同步v5回退。

2026-10-28：新增RK3568发起的录像控制第一阶段，操作见[SYNC_CONTROL_RK3568.md](SYNC_CONTROL_RK3568.md)。Windows使用`run_controlled_source.cmd`等待指令，Ubuntu运行中转，板端面板控制准备/开始/停止/重播并校验实际收到的两路源时间标记；已通过本地控制/接收模拟联调和ARM64交叉编译，真实HDMI/FPGA验收待执行。三路预览继续显示最新画面，侧方识别和驾驶放行尚未接入；原v4保留回退。

2026-10-28：HDMI前视与以太网右视的通用源时间轴播放器见[SYNC_HDMI_ETHERNET.md](SYNC_HDMI_ETHERNET.md)。Windows启动`run_sync_hdmi_ethernet.cmd`，可选任意数据集的对应本地视频，支持媒体PTS/CSV、共同暂停跳转和右侧偏移；OV5640继续手动对齐。该阶段同步素材输出，实际FPGA接收和HDMI扫描延迟仍需测量，不改变现有板端识别服务。

HDMI + OV5640 双源 PCIe DMA、RK3568 拼接及 Ubuntu 查看命令见 [MULTIVIDEO.md](MULTIVIDEO.md)。该阶段需要重新生成双源 FPGA 位流，并使用新的 `pango_pcie_multivideo` 驱动和配套程序；先验收 OV5640 单路快照，再验收双源快照和连续预览。

## 当前入口：Windows 本地视频自动识别与驾驶建议

双击 `run_video_decision.cmd` 选择本地视频，或在 PowerShell 指定已有原片：

```powershell
cd 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host'
.\run_video_decision.cmd 'D:\buduinetdiskdownload\video_20260929_112507.mp4'
```

等效命令为 `py -3.11 video_driving.py 'D:\buduinetdiskdownload\video_20260929_112507.mp4'`。Windows 本机运行 YOLOv5 交通目标检测、微调后的 LR-ASPP 车道线型分割、轨迹分析和驾驶提示，在分析帧中叠加实线／虚线／其他标线及本车左右边界曲线，下方显示线型、分方向信号、行人风险和单次变道判断。启动与画面含义见 [`LANE_PREVIEW_WINDOWS.md`](LANE_PREVIEW_WINDOWS.md)。当前行为版本为 `windows-video-behavior-v2.1`；原型车道策略见 [`LANE_BOUNDARY_STRATEGY.md`](LANE_BOUNDARY_STRATEGY.md)，旧 YOLOP 可用 `--lane-source yolop` 明确选择；无需启动 RK3568 或端口转发，FPGA 控制尚未接通。

2026-10-13 已基于 MobileNetV3 LR-ASPP 官方预训练权重完成 25 轮车道线型微调，并接入 Windows 预览；默认使用第 24 轮最佳 ONNX。数据转换、独立 CUDA 环境、进度、检查点和导出命令见 [`LANE_FINETUNING.md`](LANE_FINETUNING.md)；训练状态以 `runs/lane_style/bdd_lraspp_v1_20261013/run.json` 为准。当前完成模型接入和左右边界初版，目标视频效果与 RK3568 部署仍待验收。

本地 OmniTraffic 成都路口场景的结构、检查报告和预览工具见 [`OMNITRAFFIC_USAGE.md`](OMNITRAFFIC_USAGE.md)。该包包含仿真路口图像、车辆车道状态、交通相位和问答，未提供虚实线像素标签，需补标后才能用于当前线型分割训练。

| 快捷键 | 操作 |
| --- | --- |
| `Space` / `R` | 暂停恢复 / 从头重播并清空历史（指定 `--start` 时从该位置重播） |
| `S` | 保存当前叠加截图与同帧 JSON |
| `T` | 框选信号搜索区域 |
| `A` / `D` | 单次判断左 / 右变道，立即显示原因；暂停时也能使用 |
| `K` | 清除变道请求 |
| `Q` / `Esc` | 退出 |

先点击视频窗口取得焦点。`A/D/K` 支持大小写；变道结果保存按键当时的判断，后续标为历史结果，再按才重新判断。默认配置 `windows_video_112507.json` 针对首个原片，换视频需核对信号区域和相机参数。运行输出保存在 `video_decision_runs/日期时间_随机标识/`，包括 `observations.jsonl`、`lane_requests.jsonl`（发起请求后生成）、配置、汇总和截图；添加 `--save-video` 导出 `annotated.mp4`。

模型清单分别是 `models/yolov5s.onnx.json` 和 `models/yolop-640-640.onnx.json`，记录来源、哈希、类别顺序、预处理、输出布局、精度与解码方式。YOLOP 使用已有 **BDD100K** 预训练权重，本轮没有重新训练，也没有用本机 BDD100K 训练。YOLOv5 官方 ONNX 实际输入/输出是 FP16，YOLOP 是 FP32；均未做本地 INT8 转换。

**性能修正：**预览默认原速播放，`--backend auto` 优先使用 ONNX Runtime DirectML GPU，失败时明确提示并回退 CPU。此电脑已安装 `onnxruntime-directml` 并在 RTX 4060 上验证；三个视频完整分析耗时从约 386～410 ms 降到 85～100 ms。左侧播放当前视频，右侧显示对应分析帧及延迟，检测框不跨帧叠加。后台只保留最新待分析帧，结果过期显示待确认；播放流畅不等于每帧都完成识别。完整实现、性能记录和比赛要求差距见 [WINDOWS_PERFORMANCE.md](WINDOWS_PERFORMANCE.md)。

无窗口默认离线逐采样帧分析；加 `--playback offline` 可在窗口中逐帧核查。离线采样默认 5 fps，`--save-video` 导出同帧分析回放；实时模式导出当前画面与分析画面的组合录像。GPU 或 Windows 的结果不能作为 RK3568 NPU、多路低延迟与 FPGA 输出已经验收的证明。

当前回归入口为 `py -3.11 verify_video_behavior.py`；三个原片的本轮完整回放输出目录为 `video_decision_runs/v21_verified_112507/`、`v21_verified_113421/`、`v21_verified_113539/`。测试范围、实测结果和剩余问题见 [WINDOWS_VIDEO_V2.md](WINDOWS_VIDEO_V2.md)。旧 `windows_video_v1/` 保留为历史误报基线。

行人提示区分注意、减速准备礼让、停车礼让；确认离开路径后立即解除，骑行者不按行人停车规则处理。普通行驶不因线型未知而等待，变道证据不足则明确显示无法判断。邻道判断仅覆盖前视可见区域，条件满足时提醒确认侧后方。红/黄/绿及圆灯、直行/左右箭头使用颜色和形状原型，远小箭头、遮挡标线和自动信号关联仍存在缺口；未知信号显示注意观察，绿灯状态不等于转弯安全许可。

旧 `driving_decision_demo.py` 人工模拟和 `decision_scenarios.json` 回放仅用于规则验证。`preview_stream.py --decision-config decision_preview.example.json --decision-log decision_logs/live.jsonl` 保留 RK3568 matched 真实检测接入，需板端服务与可达的转发。训练继续用于补齐感知缺口。

## Windows 训练与模型契约

Windows CUDA 训练、车道/交通灯数据标注、ONNX 导出、Ubuntu INT8 转换及 RK3568 验收步骤见 [TRAINING_WINDOWS.md](TRAINING_WINDOWS.md)。训练不需要在 RK3568 上进行。

当前 YOLOv5 COCO 基线已记录在 `model_manifest.json` 和 `labels/coco80.txt`。执行 `python validate_model_manifest.py` 可核对标签、anchors、输入输出、阈值与 C/Python 实现指纹；旧 RKNN 二进制未在本工程取得，默认通过仅代表源码契约一致。新模型的导出和转换脚本位于 `training/`，会生成版本、哈希及解释配置清单。车道分割和专用灯模型需要适配解码器与结果协议后上板。

## 下一阶段：HDMI 真实彩色识别

彩色 DMA、连续 RKNN 识别与实时窗口已在用户实机运行。2026-10-07 的录屏显示原图出现横向循环错位，文字方向没有镜像；已修正 FPGA 采集暂停/恢复和完整帧发布逻辑，本地仿真通过，仍需重新生成并烧录位流验证跳动消除。完整升级与恢复步骤见 [COLOR.md](COLOR.md)。

| 项目 | 彩色版本 |
| --- | --- |
| FPGA 输入 | 原始 HDMI RGB；PCIe 帧缓存独立于 KEY1 的灰度/边缘显示模式 |
| 传输像素 | 1920×1080 RGB565LE，每帧 4,147,200 字节，保留 R/G/B 三个真实颜色分量 |
| YOLO 输入 | RGB565 展开为 RGB，等比缩放为 640×360，上下各补 140 行，组成 640×640×3 UINT8 |
| 模型 | 复用已经跑通的 `yolov5s-640-640.rknn` INT8 / COCO 80 类，不需要重新训练或转换 |
| 显示 | 延续 `matched` 同帧模式；彩色图像与真实检测框对应同一帧，支持彩色截图 |
| 传输方式 | 继续使用 USB/HDC 和端口 9001；彩色数据量是灰度的两倍，FPS 需重新测量 |
| 回退 | 顶层 `PCIE_RGB565=0` 重新生成位流，或恢复旧灰度位流；新版软件兼容灰度 |

RGB565 使用红/蓝各 5 位、绿色 6 位，不等同于完整 RGB888 的颜色精度。下文已验收的灰度里程碑与旧阶段步骤保留为历史记录；升级彩色以 `COLOR.md` 为准。

彩色恢复时，编译、发送和启动统一使用 `pcie_rknn_probe_rgb565`；板端 `--help` 应显示 `result_version=4 result_header=88`。仅在 Ubuntu 编译成功不会更新板端程序，必须发送新产物，并从 `/data/local/tmp/pcie_rknn_probe_rgb565` 启动。

如果启动报 `Listen socket: Address already in use`，先按 [COLOR.md 第 4 节](COLOR.md#4-终端-a启动彩色同帧识别) 部署并运行新版 `stop_rknn_server.sh`，按 `/proc/PID/comm` 清理 RKNN 程序并确认板端 9001 端口已释放；新版端口检查不依赖 `awk`，不能仅凭 `pidof` 空输出判断旧服务已退出。

### 下次启动：当前彩色版本的操作入口

当前使用 RGB565LE 位流、`pcie_rknn_probe_rgb565` 和 USB/HDC 端口 9001。日常重启后按 [COLOR.md](COLOR.md) 的第 3、4、5 节恢复；第 1、2 节用于更新位流或重新构建软件，不必每次启动都执行。

| 顺序 | 终端 | 查看章节与操作 |
| --- | --- | --- |
| 1 | A | `COLOR.md` 第 3 节：设置 HDC/TARGET，部署彩色产物，加载驱动；确认 `RGB565LE` 和 `record_bytes=4147240` |
| 2 | A | 第 4 节：需要重新启动服务时先部署并运行新版 `stop_rknn_server.sh`；确认 `9001 is free`，再检查板端 `_rgb565 --help` 中的 `result_header=88` |
| 3 | A | 第 4 节：选择后台启动命令（包含 `--daemon --log`），只启动一个 `pcie_rknn_probe_rgb565` 实例；保留 PID 和日志路径 |
| 4 | A | 第 4 节末尾：执行 `fport ls`；缺少本目标的 `tcp:9001 tcp:9001` 映射时才创建 |
| 5 | B | 第 5 节：运行 `preview_stream.py --host 127.0.0.1 --port 9001 --require-detections --profile` |

需要直接观察板端错误时，改用第 4 节的前台命令：先设置好转发，再启动服务并保持终端 A 运行。前台和后台命令二选一；从前台切到后台时先停止前台实例。只关闭了预览窗口、板端服务仍正常运行时，直接执行第 5 节重新连接即可。

仅修改源码、驱动或位流时才重新编译、签名或烧录。再次启动必须使用 `_rgb565` 文件名。下文“重启 RK3568 后的调试流程”及其他灰度阶段命令保留为历史参考，当前彩色启动以本入口和 `COLOR.md` 为准。

## 比赛要求对应的下一阶段路线图

### 当前完成度

首个场景已按用户指定缩小为 `video_20260929_113539.mp4` 的前 30 秒，只做红灯/绿灯及停车/通行提示。新增 Ubuntu 预览分析入口、本视频区域配置和离线验证；操作及边界见 [TRAFFIC.md](TRAFFIC.md)。本机颜色分析约 10.1 秒确认红灯、26.2 秒确认绿灯；RK3568/HDMI 实机效果仍需验收，灯色状态尚未返回 FPGA。

第二个场景 `video_20260929_112507.mp4` 已记录 0～38 秒的车道总数、本车车道、9～13 秒箭头绿灯及 24/28/38 秒事件。Ubuntu 预览使用 `--road-scene traffic_lane_scene.json --traffic-roi-fallback`，从同帧图像提取车道线/本车区域并判断红绿灯，支持组合截图和离线评分；同步与启动命令见 [LANE.md](LANE.md)。按本轮目标，车道输出优先高亮当前区域、记录车道内横向位置，并用连续边界轨迹或走廊中心跨越确认左/右变道事件；总车道数为辅助字段，未知时不阻止当前区域显示。道路相对位置及从左编号仍需道路边界证据，不能把车道内居中当作道路中间车道。人工时间轴不参与运行时识别；变道实片结果与限制见 `LANE.md`。该版状态尚未回传 FPGA，HDMI 实机效果仍需验收。

目前已经完成的是“单路 HDMI 彩色输入到 RK3568 NPU 识别，再回传到 Ubuntu 实时显示”的传输和识别闭环：

| 比赛要求 | 当前状态 | 还缺的部分 |
| --- | --- | --- |
| 图像采集与显示 | HDMI 1920×1080 RGB565LE 经 FPGA DDR、PCIe、RK3568 传输；Ubuntu 可显示同帧彩色图像和检测框 | FPGA/板端 HDMI 输出上的最终行为叠加和告警尚未闭环验收 |
| MCU/FPGA 或 ARM/FPGA 通信 | PCIe 数据通道和连续帧传输已打通 | 用于车道、场景、动作、LED 和告警的控制命令协议尚未定型 |
| 图像处理与识别 | NPU 连续识别人、车、交通灯等 COCO 类别；Ubuntu 原型已增加红/绿灯色、当前车道区域及边界/走廊跨越观测 | 黄色灯、箭头方向、车道用途、行为/违规决策和板端状态回传仍需完成 |
| 高阶多路采集 | 当前是单路视频 | 前后左右四路同步、带宽规划和多路调度尚未开始 |
| 高阶复杂场景 | 尚未建立夜间、复杂路口和障碍物数据集 | 需要场景数据、规则和专用模型/算法验证 |
| AI 实时决策 | Windows 本地视频 v2.1 已接入分级行人提示、分方向信号、单次变道判断和日志回归；板端仍为检测与同帧预览 | 车道/信号可靠性完善、RK3568 迁移、实际动作输出及闭环验收 |

当前板端模型是通用 COCO 检测模型，只输出目标框。Windows 视频入口已额外接入 BDD 微调 LR-ASPP 线型模型、左右边界曲线、灯色/形状原型、目标轨迹及行为提示；目标摄像头线型表现和灯与车道关联仍需验证与优化。Windows 结果尚未迁移到 RK3568，也未形成实际动作与 FPGA 输出闭环。

### 推荐实施顺序

| 阶段 | 目标 | 主要工作 | 完成标志 |
| --- | --- | --- | --- |
| 0. 固化基线 | 保护已经跑通的链路 | 保存当前彩色位流、驱动、`pcie_rknn_probe_rgb565`、模型和启动日志；固定 `matched` 协议 | 重启后能按 [COLOR.md 第 3～5 节](COLOR.md) 恢复，连续运行不崩溃 |
| 1. 建立场景数据集 | 让规则有可重复输入 | 准备路口红绿灯、三车道、道路地图、导航道路、行人闯红灯等 HDMI 视频；为每段记录车道、灯色和期望动作 | 每个片段有明确的时间轴和人工标注，不再凭窗口印象判断 |
| 2. 完成行为状态机 | 先把已有观测和明确模拟输入转换为比赛动作 | 在 Windows 实现红灯停车、绿灯条件通行、行人冲突礼让、实线跨越约束；加入状态保持、超时、未知和来源标记 | 结构化回放场景有确定输出；模拟输入与自动识别分开记录 |
| 3. 补齐感知并整合判断 | 从目标框得到规则所需的有效交通状态 | 增加行人冲突 ROI/跟踪、左右边界线型、信号对应关系和箭头；先验证算法，必要时使用 BDD100K/自采数据训练 | 同帧观测接入状态机；每个动作有来源、原因和有效期 |
| 4. 打通控制与输出 | 形成 ARM/FPGA 闭环 | 定义 PCIe BAR/驱动命令或独立控制通道；下发场景、车道、动作、告警和叠加信息；FPGA 驱动 LED 和 HDMI 叠加 | 按键能模拟车道/转向/停车，LED 和画面箭头与行为状态一致 |
| 5. 完成基础任务验收 | 先拿下题目明确的单路场景 | 逐项跑测试方式中的四类视频，记录输入、识别、决策、输出和延迟 | 四类场景都有可回放日志、截图/录像和通过判据 |
| 6. 提升实时性与稳定性 | 保证演示连续运行 | 测量采集、PCIe、预处理、NPU、后处理、决策和输出延迟；处理丢帧、断连、重启和长时间运行 | 规定时长内无崩溃、无旧框残留，决策延迟和有效 FPS 达到目标 |
| 7. 接入以太网 | 替换调试链路并保留同一协议 | 先用同一结果协议把 `127.0.0.1:9001` 改为板端 IP；测量千兆链路的延迟、丢包和重连 | USB/HDC 与以太网能复现同一画面、框和状态 |
| 8. 高阶扩展 | 再做多路和复杂场景 | 前后左右四路采集、夜间/复杂路口、障碍物避让、导航辅助和专用模型 | 在基础任务稳定后再增加，不阻塞单路比赛演示 |

### 阶段 1：先定义比赛场景和状态接口

建议先统一一条结果记录，后续无论算法在 Ubuntu、RK3568 还是 FPGA，都使用同一字段：

```text
frame_seq, timestamp
light_state = unknown|red|yellow|green
ego_lane = 1-based number from left|unknown
lane_position = left|center|right|numbered|unknown
lateral_position = left|center|right|unknown (within the current lane)
lane_change = none|left|right|unknown
lane_change_event_seq, lane_change_timestamp_ns
lane_count = optional count|unknown
objects = class, track_id, box, score, velocity
recommended_action = stop|go|turn_left|straight|turn_right|lane_change|alarm
alarm_code = none|red_light|yellow_forced_drive|forced_lane_change|obstacle
decision_confidence, decision_reason
```

先把比赛提供的 1～4 类视频转成带时间轴的测试集，并人工写出期望结果。例如：红灯期间动作必须为 `stop`；绿灯期间为 `go`；黄灯且检测到强制通行时触发 `yellow_forced_drive`；导航场景中车道指令与 `turn_left/straight/turn_right` 一致。这样可以单独测试算法，不需要每次都连接 FPGA 和窗口。

当前车道区域与本车变道提醒是车道感知的主要输出，总车道数只作辅助。道路相对位置、从左编号和车道内横向位置分别记录。左/右转及变道建议属于下一阶段决策，需先补充信号箭头方向及对应车道、车道转向约束、邻道占用、导航目标、行人和障碍物等证据；当前 `GO_SIGNAL_ONLY` 仅表示灯色观测。

### 阶段 2：Windows 行为状态机

将规则与 `preview_stream.py` 绘图解耦。先在 Windows 用 Python 编写只消费结构化观测的可回放行为引擎，再接入视频和现有感知，稳定后移至 RK3568 用户态服务。设计见 [DECISION.md](DECISION.md)。状态至少区分：

1. `WAIT_INPUT`：图像/必要证据过期、断连或未知时，不输出通行许可。
2. `APPROACH`：观测当前区域、相关信号和前方目标，等待确认。
3. `RED_STOP`：本车有效信号为红时给出停车建议。
4. `YIELD_PEDESTRIAN`：行人与本车目标路径冲突时礼让；绿灯不能覆盖该条件。
5. `GREEN_GO`：本车有效绿灯、目标动作合法且路径冲突已确认解除时，给出通行建议。
6. `YELLOW_CHECK`：按题目规则判断黄灯期间的强制通行行为并告警。
7. `LANE_GUIDANCE`：分别检查跨线变道和路口转弯许可，显示动作与原因。

违规告警与主动作分别输出，并按事件序号去重。每个状态都需进入/退出条件、确认/释放策略和超时；模拟输入必须标记来源，不能作为自动感知效果。

### 阶段 3：补齐感知输入

| 感知项 | 第一版建议 | 原因 |
| --- | --- | --- |
| 人、车、行人 | 保留当前 YOLOv5s INT8 和简单 IoU/质心跟踪 | 已经在 RK3568 NPU 上运行，先复用检测结果 |
| 交通灯位置 | 复用 YOLO 的 `traffic light` 框，必要时限制上方 ROI | 现有模型已经有该 COCO 类别 |
| 红黄绿状态 | 对灯框做 RGB/HSV 分析并连续多帧投票；场景固定后再训练小分类器 | 目标框类别本身不包含灯色，彩色输入已经为此准备好 |
| 车道 | 复用本车区域和跨线观测，补左右边界的实线/虚线/未知；必要时使用 BDD100K 优化 | 状态机需要线型证据，不要求先可靠统计所有车道 |
| 运动和变道 | 为车辆/自车区域保存轨迹、方向和速度；用滞回阈值判断变道 | 单帧框无法判断“正在行驶”或“强制变道” |
| 障碍物 | 先用车辆、行人、障碍物区域和 TTC/距离近似 | 复杂避障属于高阶任务，基础任务先做可解释规则 |

交通灯和车道算法必须保留 `unknown` 状态。识别不确定时不能直接下达“通行”或“转向”命令，应保持安全动作并触发诊断提示。

### 阶段 4：控制和显示闭环

当前 `hdmi_loop.v` 的按键已有图像模式、阈值和 UDP 上传等功能；不能直接把它们当作比赛车道按键而不修改协议。应先建立明确的控制映射：

| 控制方向 | 建议内容 |
| --- | --- |
| RK3568 → FPGA | `scene_id`、目标车道、推荐动作、告警码、箭头方向、状态序号和校验 |
| FPGA → RK3568 | 按键事件、当前模式、LED 状态、命令确认和故障状态 |
| 按键 | 先映射左转/直行/右转或场景切换；原有阈值、UDP 功能改为组合键或调试模式 |
| LED | LED0 左转、LED1 直行、LED2 右转的比赛映射，另设告警闪烁优先级 |
| HDMI 输出 | 原图上叠加箭头、红色告警框、状态文字；输入画面和 PCIe 识别画面必须标记同一 `frame_seq` |

第一版可以先让 Ubuntu 窗口显示行为箭头和告警，验证规则；最终演示仍应把状态送回 FPGA/HDMI/LED，才能完成题目要求的图像输出和软硬件协同闭环。

### 四类基础测试的验收表

| 测试场景 | 必须识别 | 必须判断 | 必须输出 |
| --- | --- | --- | --- |
| 三车道路口视频 | 车辆、交通灯、当前车道 | 红灯停车、绿灯通行、黄灯强制通行告警 | 左/直/右箭头、LED、红色告警 |
| 道路图片/视频虚拟线 | 车道线、车辆轨迹 | 直行、左/右转、强制变道告警 | 箭头、LED、告警码和原因 |
| 带导航真实道路视频 | 车辆、车道和导航指令 | 按导航完成辅助驾驶动作 | 同帧动作箭头、状态和延迟 |
| 行人闯红灯等补充场景 | 行人、车辆、交通灯 | 闯红灯或危险接近 | 告警框、LED/声音接口和事件日志 |

每个场景至少保存：原始视频、带框/带箭头视频、结构化日志、输入帧序号、决策状态、输出命令和异常截图。评审时应能从一条日志解释“为什么在这一帧停车或报警”。

### 暂时不要优先做的工作

- 不要先做四路视频或复杂夜间模型；单路基础场景的决策和输出还没有验收。
- 不要先把目标检测模型换成更大的模型；当前瓶颈是灯色、车道和行为规则，不是 YOLO 框是否已经能画出来。
- 不要把 USB/HDC 预览 FPS 当作最终行为 FPS；先定义决策延迟、有效识别帧率和告警响应时间。
- 不要让窗口显示结果成为唯一控制源；最终动作、LED 和 HDMI 叠加需要由可记录、可确认的状态接口驱动。

### 2026-10-07：彩色画面横向跳动修复

| 项目 | 结论与操作 |
| --- | --- |
| 录屏现象 | 右侧内容绕回左侧，局部出现接缝；图像/检测序号相同、`delta=0.0 ms`、没有重连 |
| 采集问题 | 旧逻辑在 DMA 读帧时直接屏蔽 HDMI DE，并可能在行中间恢复；帧缓存选择仍随 HDMI VS 切换，可能选到未完整写入的缓存 |
| FPGA 修改 | 只在帧边界暂停和恢复；完整写完所有行才发布帧；DMA 等待已暂停且写事务排空后锁定完整缓存 |
| 本地验证 | RGB565 逐像素行顺序、1920 像素行宽与 RAM 回绕、首帧等待、跨帧暂停/恢复通过；灰度/彩色完整 DDR/PCIe 两帧回归通过 |
| 实机更新 | 重新综合、布局布线、时序检查和生成 `06_hdmi_loop_1/hdmi_loop.pds` 位流；烧录后重启 RK3568，恢复驱动及已验证的 `pcie_rknn_probe_rgb565` 服务 |
| 验收 | 静止且左右不对称的 HDMI 图案连续显示数分钟，无横向绕回/接缝；再播放运动视频确认；重新记录采集与识别 FPS |

本次修复在 FPGA RTL 中生效，Ubuntu 启动命令不能替代新位流。帧边界等待可能增加采集耗时，更新后的帧率需要实测。

## 里程碑：基础识别链路已打通（2026-10-05）

用户已在实机上确认连续识别与实时带框窗口运行，显示效果符合当前调试预期。基础框架的端到端功能已完成：

```text
HDMI 输入 → FPGA 帧缓存 / DDR → PCIe DMA → RK3568 stream 设备
                                                    ↓
                                     YOLOv5s / RKNN / NPU 连续推理
                                                    ↓
                                      同帧图像 + 检测框 + 耗时
                                                    ↓
                                       TCP / USB HDC 转发 :9001
                                                    ↓
                                 Ubuntu 实时窗口：框、类别、置信度
```

| 已完成项目 | 当前证据与状态 |
| --- | --- |
| PCIe 基础读写与 DMA | BAR0、小块 DMA、测试图案及 HDMI 帧搬运逐步验收通过 |
| 图像正确性 | 1920×1080 GRAY8 图像内容正常，连续采集的图像确实变化，30 条记录验证通过 |
| RKNN 单帧识别 | 实机输出汽车、人物、背包等类别、置信度与原图坐标；修复 240/255 通道误判 |
| 连续识别 | 模型上下文复用，统一读取 stream，连续推理并发送结果 |
| 实时带框窗口 | 用户确认运行与效果；同帧模式中图像和检测结果来自同一帧，按 `S` 可保存原图和带框图 |
| 当前运行环境 | RKNN API `2.3.2`，NPU 驱动 `0.8.2`，模型 `yolov5s-640-640.rknn` |

此里程碑表示基础功能链路已实机打通。以太网传输、长时间稳定性、持续识别帧率和特定场景的准确率仍需后续验证。

### 为什么没有专门训练也能识别

当前使用已有测试包中的 `model/yolov5s-640-640.rknn`，模型文件已包含训练得到的参数；RKNN 负责在 RK3568 NPU 上执行推理。我们目前完成的是采集、预处理、推理、框解码和显示的集成，尚未进行项目专属模型训练。

当前后处理按标准 YOLOv5 的 COCO 80 类输出解码，人物、汽车、公交车、背包等常见目标可以直接识别。这套现成模型已能支持基础框架验证；是否满足最终需求，要结合目标类别、真实拍摄场景以及误检/漏检、延迟和帧率要求判断。

| 需求 | 现成模型的适用性 |
| --- | --- |
| 检测人物、汽车等 COCO 常见类别 | 可以先直接使用，在真实场景评估效果 |
| 检测项目自定义物体、特定零件或缺陷 | 通常需要标注数据并训练或微调，再转换为匹配 RK3568 的 RKNN 模型 |
| 区分具体人员、车型、车牌文字 | 当前通用目标检测不直接完成这些任务，需要对应的识别模型或后续处理 |
| 降低灰度、弱光、小目标等场景的误检/漏检 | 先评估当前模型；有明确不足时再针对实际输入准备数据或调整方案 |

当前输入为 GRAY8，预处理复制成三个相同通道，不会恢复颜色。当前置信度阈值为 `0.25`、按类别 NMS 阈值为 `0.45`；出现检测框不等于每个框均已完成准确率验收。窗口中的置信度是该框的模型分数，不是整个项目的准确率；持续运行推理也不会自动训练模型或学习新类别。

### 接下来要完善的事项

| 优先级 | 工作 | 验收方向 |
| --- | --- | --- |
| 1 | 以太网直连传输 | 确认 VMware 桥接、板端 IP 与千兆协商；用同一识别服务经以太网持续显示并测量帧率、断连情况 |
| 2 | 长时间稳定性与重启恢复 | 逐步延长运行时间，验证窗口退出/重连、服务重启、设备重启后的恢复流程 |
| 3 | 帧率与延迟 | 收集采集、预处理、NPU、输出获取、后处理和发送耗时；优化经测量确认的瓶颈 |
| 4 | 识别效果 | 在实际目标、空场景、小目标和不同光照下核对框位置、类别、误检和漏检，再决定是否训练专属模型 |
| 5 | 使用细节 | 完善日志、截图、配置与启动流程；按需要推进 FPGA 帧边界、持续 DMA 和丢帧统计 |

连续识别的详细说明见 [RKNN.md](RKNN.md)；原始图像预览见 [PREVIEW.md](PREVIEW.md)；分段性能测量见 [TIMING.md](TIMING.md)。本文末尾保留以太网桥接和 IP 配置步骤。

## 2026-10-07：同帧识别优先预览

用户反馈较快的原始画面配合较慢的异步检测，在运动视频中效果不理想。本轮将识别服务默认预览模式改为 `matched`：完成一帧推理后，发送该帧原图和它自己的检测框，窗口收到完整记录后才更新画面。

| 行为 | 同帧模式 `--preview-mode matched` |
| --- | --- |
| 图像与框 | 严格对应同一帧；窗口显示 `Mode matched`，图像序号和检测序号相同，`delta=0.0 ms` |
| 没有目标 | 正常显示当前图像并清空框；不跳过漏检帧，不保留上一帧框 |
| 跟踪 | 直接画真实推理结果，关闭框的速度预测；`--track-max-age-ms` 仅影响 `async` 模式 |
| 流水线 | 采集、推理和发送继续并行；优先处理最新帧，替换尚未发送的旧结果，减少积压 |
| 性能 | 保留量化输出与 objectness 筛选；流式默认关闭全张量 min/max 扫描，`--profile` 不再触发该扫描 |
| 窗口 | 仅在新图像、检测或状态到达时重绘；重复显示旧图像不计入 FPS |

这项调整改善框与画面的对应关系，不改变模型、置信度阈值或输入的灰度格式。实际漏检仍会显示。新的同帧 FPS 尚需 RK3568 实测；它受完整识别周期和传输影响，不能用此前约 18 FPS 的原始采集速度代替。需要对照旧模式时，在板端启动命令中使用 `--preview-mode async`。

### 本轮升级：Ubuntu 终端 A

先退出终端 B 的旧窗口。同步源文件并构建；本轮无需更新 FPGA 位流或内核模块：

```bash
mkdir -p "$HOME/pcie_host"
cp -v /mnt/hgfs/pcie_host/{pcie_rknn_probe.c,build_rknn_probe.sh,preview_stream.py,rknn_stream.py,draw_detections.py,capture_stream.py,frame_to_png.py,frame_format.py,frame_checksum.c,build_checksum.sh} "$HOME/pcie_host/"
cd "$HOME/pcie_host"
bash build_checksum.sh
RKNN_RUNTIME="$HOME/rknn-yolov5-test/lib/librknnrt.so" bash build_rknn_probe.sh

export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
sudo "$HDC" -t "$TARGET" shell 'killall pcie_stream_server pcie_rknn_probe 2>/dev/null || true'
sudo "$HDC" -t "$TARGET" shell pidof pcie_stream_server pcie_rknn_probe
```

确认最后一条命令不再输出 PID，再继续部署。刚重启的板子需先按后文 A2 恢复驱动，并确认 stream 节点存在：

```bash
sudo "$HDC" -t "$TARGET" shell ls -l /dev/pango_pcie_stream_0002:21:00.0
sudo "$HDC" -t "$TARGET" file send "$HOME/pcie_host/pcie_rknn_probe" /data/local/tmp/pcie_rknn_probe
sudo "$HDC" -t "$TARGET" shell chmod 755 /data/local/tmp/pcie_rknn_probe

RUN_TAG="rknn_matched_$(date +%Y%m%d_%H%M%S)"
REMOTE_RKNN_LOG="/data/local/tmp/${RUN_TAG}.log"
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/rknn-yolov5-test/glibc/ld-linux-aarch64.so.1 \
    --library-path /data/local/tmp/rknn-yolov5-test/lib:/data/local/tmp/rknn-yolov5-test/glibc \
    /data/local/tmp/pcie_rknn_probe \
    --model /data/local/tmp/rknn-yolov5-test/model/yolov5s-640-640.rknn \
    --runtime /data/local/tmp/rknn-yolov5-test/lib/librknnrt.so \
    --device /dev/pango_pcie_stream_0002:21:00.0 \
    --serve --daemon --bind 0.0.0.0 --port 9001 --preview-mode matched --profile \
    --log "$REMOTE_RKNN_LOG"
sudo "$HDC" -t "$TARGET" shell tail -n 20 "$REMOTE_RKNN_LOG"
sudo "$HDC" -t "$TARGET" fport ls
```

日志应出现 `preview_mode=matched`。本轮继续使用 USB/HDC；若没有 `9001` 转发，再执行：

```bash
sudo "$HDC" -t "$TARGET" fport tcp:9001 tcp:9001
```

### 打开窗口：Ubuntu 图形桌面终端 B

```bash
cd "$HOME/pcie_host"
set -o pipefail
python3 -u preview_stream.py --host 127.0.0.1 --port 9001 \
    --require-detections --profile 2>&1 | tee rknn_preview_matched.log
```

运行 30～60 秒后，在终端 A 收集板端日志；终端 B 可在退出窗口后查看预览日志：

```bash
# 终端 A
sudo "$HDC" -t "$TARGET" shell cat "$REMOTE_RKNN_LOG" | \
    grep -E 'RKNN stream timing:|RKNN preview timing:' | tail -n 20
# 终端 B
grep -E 'Detection result:|Preview timing:|Display timing:' rknn_preview_matched.log | tail -n 20
```

`Detection` 按完整检测记录到达间隔计算，`Receive` 按完整图像记录到达间隔计算，`Display` 只计新图像更新；单次频率为 `1 / 两次更新间隔（秒）`。同帧模式中接收和检测频率一致，显示频率可能因窗口负载或主动取最新结果略低。`Source interval` 是选中帧的源时间间隔；`skipped source sequences` 表示为减少积压而未发送的采集序号，不等同于传输损坏或模型漏检。`frame_age_ms` 使用板端同一时钟，表示采集时间戳到发送完成的耗时，不包含 Ubuntu 收取和绘图时间。

### 已连接但只有采集日志、没有识别画面

本轮实机反馈出现 `Connected`，板端也已打印 `listening` 和 `client connected`，但日志持续只有 `PCIe frame verified`，没有进入预处理。这说明 TCP 已连通；重复执行 `fport` 不能解决此问题。

已修复采集双缓冲的调度缺陷：旧代码总是选第一个可写槽，即使它已装有唯一待推理帧，也立刻清掉 `ready` 并覆盖。推理线程可能一直等不到可取帧。新代码先使用空闲槽，两个槽均有待处理帧时才覆盖较旧帧；推理中的槽不覆盖。采集退出时会唤醒等待中的推理线程。采集与推理各用独立日志开关，避免采集受推理开关影响逐帧刷屏。

修复通过本地 ARM64 严格编译，以及实际采集/取帧代码的 150 次线程交接测试；RK3568 实机恢复仍需验证。重新复制 `pcie_rknn_probe.c`，编译并部署，再按本节的 `matched` 命令启动。Python、模型、FPGA 和驱动无需因此再次修改。

通过 glibc 加载器启动时，`pidof pcie_rknn_probe` 的空输出不能单独证明进程已退出。使用启动信息中的实际 PID 核对；以下 `2344` 只是本次故障的 PID，重启或重新启动后必须换成新值：

```bash
sudo "$HDC" -t "$TARGET" shell \
    'cat /proc/2344/comm; tr "\000" " " < /proc/2344/cmdline; echo'
```

确认命令行确实包含 `/data/local/tmp/pcie_rknn_probe` 后，可按实际 PID 停止旧实例：

```bash
sudo "$HDC" -t "$TARGET" shell kill -TERM 2344
sleep 3
sudo "$HDC" -t "$TARGET" shell ls -d /proc/2344
```

必须确认旧进程退出后才覆盖板端文件。若旧缺陷使它停止采集后仍卡在等待，且 `/proc/2344/cmdline` 仍确认为该旧实例，再执行 `kill -KILL 2344` 并复查。不要对重启后复用的 PID 执行这些停止命令。

新实例第一次接收客户端时，日志会依次出现：

```text
RKNN first inference: seq=...
RKNN stage: preprocess complete; setting inputs
RKNN stage: inputs complete; running NPU
RKNN stage: NPU complete; getting outputs
RKNN stage: outputs complete; decoding YOLO
RKNN stream timing: ...
RKNN preview timing: mode=matched ...
```

如果仍无画面，用最后到达的阶段区分取帧、输入设置、NPU、输出获取与解码阻塞。关键日志查询：

```bash
sudo "$HDC" -t "$TARGET" shell cat "$REMOTE_RKNN_LOG" | \
    grep -E 'listening|client|first inference|RKNN stage:|timing:|failed|Acquire|Send|sender' | tail -n 40
```

## 重启 RK3568 后的调试流程

本节是灰度阶段的历史流程，使用默认 `pcie_rknn_probe` 文件名。当前 RGB565LE 彩色版本请按本文顶部“下次启动：当前彩色版本的操作入口”和 `COLOR.md` 第 3、4、5 节操作。

**下次重启后优先按本节恢复连续识别窗口。只重启而没有改代码时，复用已验证的 `.ko` 和可执行程序，不需要重新编译或重新签名。** 内核模块和后台服务不会随重启自动恢复；USB 端口转发也需要重新检查。

先让 FPGA 使用已经验证的 HDMI/PCIe 位流，保持 HDMI 有有效输入。如果 FPGA 也掉电，需确认该位流已恢复；仅重启 RK3568 不应盲目更换位流。

| 终端 | 用途 | 本节端口 |
| --- | --- | --- |
| Ubuntu 终端 A | HDC、驱动恢复、部署、启动和查看日志 | 板端识别服务 TCP `9001` |
| Ubuntu 图形桌面终端 B | 实时识别窗口 | USB 使用 `127.0.0.1:9001`；以太网使用板端 IP 的 `9001` |

### A1. 设置变量、恢复共享目录并检查文件

以下命令在 Ubuntu 终端 A 执行。新开的控制终端需要重新设置 HDC/TARGET；`TARGET` 如果发生变化，以 `list targets` 的实际输出为准。

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"

sudo mkdir -p /mnt/hgfs
if ! mountpoint -q /mnt/hgfs; then
    sudo vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other
fi

ls -l /mnt/hgfs/pcie_host/README.md
cd "$HOME/pcie_host"
ls -l pango_pcie_hdmi_frame_test.ko pcie_rknn_probe \
    preview_stream.py rknn_stream.py draw_detections.py

sudo "$HDC" list targets
sudo "$HDC" -t "$TARGET" shell uname -r
sudo "$HDC" -t "$TARGET" shell lspci -nn
sudo "$HDC" -t "$TARGET" shell lsmod | grep pango_pcie
```

已验证的内核为 `5.10.184`，PCIe 设备 ID 为 `0755:0755`。`lspci` 缺少 `pci.ids` 的提示不影响按数值 ID 检查。设备未枚举时先恢复 PCIe/FPGA 状态；本地文件缺失或已改源码时，先使用后面的“改源码后重新编译”步骤。

### A2. 停止旧服务并恢复驱动

先关闭旧预览窗口，再停止可能存在的旧服务器。刚重启时没有这些进程是正常的。

```bash
sudo "$HDC" -t "$TARGET" shell \
    'killall pcie_stream_server pcie_rknn_probe 2>/dev/null || true'
sudo "$HDC" -t "$TARGET" shell pidof pcie_stream_server pcie_rknn_probe
```

确认 `pidof` 不再输出 PID 后继续；停止信号发出时正在执行的推理或采集可能需要几秒退出。若还有 PID，稍后重复检查，不覆盖正在运行的程序。

```bash
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pango_pcie_hdmi_frame_test.ko" \
    /data/local/tmp/pango_pcie_hdmi_frame_test.ko

sudo "$HDC" -t "$TARGET" shell \
    'if [ -d /sys/module/pango_pcie_hdmi_frame_test ]; then echo "HDMI module already loaded"; else insmod /data/local/tmp/pango_pcie_hdmi_frame_test.ko profile=1; fi'

sudo "$HDC" -t "$TARGET" shell ls -l \
    /dev/pango_pcie_stream_0002:21:00.0
sudo "$HDC" -t "$TARGET" shell dmesg | \
    grep -E 'HDMI frame DMA passed|HDMI stream capture ready|probe of|timed out' | tail -n 12
```

必须确认 stream 节点存在。`insmod` 没有输出本身不能证明 PCIe probe 成功。如果模块已加载但节点不存在，先查看失败日志，不继续启动窗口。其他测试模块占用 PCIe 设备时，先卸载实际占用的模块，不强制卸载。

### A3. 检查模型环境、部署并启动连续识别

正常重启一般保留 `/data/local/tmp` 中的文件，但重新刷镜像、清理目录等操作可能改变它们，先检查：

```bash
sudo "$HDC" -t "$TARGET" shell ls -l \
    /data/local/tmp/rknn-yolov5-test/model/yolov5s-640-640.rknn \
    /data/local/tmp/rknn-yolov5-test/lib/librknnrt.so \
    /data/local/tmp/rknn-yolov5-test/glibc/ld-linux-aarch64.so.1

sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pcie_rknn_probe" \
    /data/local/tmp/pcie_rknn_probe
sudo "$HDC" -t "$TARGET" shell chmod 755 /data/local/tmp/pcie_rknn_probe

RUN_TAG="rknn_live_$(date +%Y%m%d_%H%M%S)"
REMOTE_RKNN_LOG="/data/local/tmp/${RUN_TAG}.log"
printf '本次板端日志：%s\n' "$REMOTE_RKNN_LOG"

sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/rknn-yolov5-test/glibc/ld-linux-aarch64.so.1 \
    --library-path /data/local/tmp/rknn-yolov5-test/lib:/data/local/tmp/rknn-yolov5-test/glibc \
    /data/local/tmp/pcie_rknn_probe \
    --model /data/local/tmp/rknn-yolov5-test/model/yolov5s-640-640.rknn \
    --runtime /data/local/tmp/rknn-yolov5-test/lib/librknnrt.so \
    --device /dev/pango_pcie_stream_0002:21:00.0 \
    --serve --daemon --bind 0.0.0.0 --port 9001 --preview-mode matched --profile \
    --log "$REMOTE_RKNN_LOG"

sudo "$HDC" -t "$TARGET" shell pidof pcie_rknn_probe
sudo "$HDC" -t "$TARGET" shell tail -n 20 "$REMOTE_RKNN_LOG"
```

看到新日志中的 `RKNN detection server listening on 0.0.0.0:9001` 后再打开窗口。没有客户端时，后台服务等待连接，不打印持续识别日志。模型或运行库缺失时先恢复此前验证通过的完整 `rknn-yolov5-test` 包，包括 `glibc` 依赖，不更换不匹配的库。

### A4. USB 方式检查转发；以太网方式检查网络

当前已经通过的是 USB/HDC 识别窗口。USB 方式先查看是否已有 `9001` 转发：

```bash
sudo "$HDC" -t "$TARGET" fport ls
```

如果没有转发，执行：

```bash
sudo "$HDC" -t "$TARGET" fport tcp:9001 tcp:9001
```

已有正确的转发时复用它。若提示 `TCP Port listen failed`，先查看现有转发和本地端口占用，不反复重启识别服务：

```bash
ss -ltnp 'sport = :9001'
```

以太网方式不执行 `fport`。先按本文末尾步骤配置 VMware 桥接和 IP；板端临时 IP 在重启后需重新检查。确认 Ubuntu 可以 `ping` 板端，且板端有线接口协商为千兆，再执行以太网窗口命令。

### B1. 打开实时带框窗口

在 Ubuntu 图形桌面另开终端 B。USB/HDC 方式：

```bash
cd "$HOME/pcie_host"
LOCAL_RKNN_PREVIEW_LOG="$HOME/pcie_host/rknn_preview_$(date +%Y%m%d_%H%M%S).log"
set -o pipefail
python3 -u preview_stream.py --host 127.0.0.1 --port 9001 \
    --require-detections --profile 2>&1 | tee "$LOCAL_RKNN_PREVIEW_LOG"
```

以太网方式把连接地址换成板端实际 IP，例如 `192.168.50.20`；USB 与以太网命令只选一个运行：

```bash
cd "$HOME/pcie_host"
BOARD_IP="192.168.50.20"
LOCAL_RKNN_PREVIEW_LOG="$HOME/pcie_host/rknn_preview_eth_$(date +%Y%m%d_%H%M%S).log"
set -o pipefail
python3 -u preview_stream.py --host "$BOARD_IP" --port 9001 \
    --require-detections --profile 2>&1 | tee "$LOCAL_RKNN_PREVIEW_LOG"
```

| 窗口操作或指标 | 用途 |
| --- | --- |
| 框、类别、置信度、`Targets` | 当前已完成识别帧的结果；没有符合阈值的目标时可以没有框 |
| `Mode matched`、`Receive`、`Display`、`Detection`、`infer`、`NPU` | 确认同帧模式并查看接收、显示、检测频率与推理耗时；持续运行的实际值需要日志测量 |
| `Source interval`、序号跳过 | 服务选帧后的源帧间隔；为减少积压而跳过采集帧是正常策略 |
| `S` | 在当前目录的 `preview_snapshots` 保存原始 PGM、原始 PNG 和带框 PNG |
| `Q` / `Esc` / `Ctrl+C` | 关闭预览；板端识别服务保留，等待下一次连接 |

不要同时启动 `pcie_stream_server`、`dd` 或单帧推理来读取同一个 stream；连续识别服务已经统一负责采集和推理。

### A5. 收集日志和结束调试

回到仍保留 `REMOTE_RKNN_LOG` 的终端 A，采样本次服务和驱动耗时：

```bash
sudo "$HDC" -t "$TARGET" shell tail -n 40 "$REMOTE_RKNN_LOG"
sudo "$HDC" -t "$TARGET" shell dmesg | grep 'HDMI timing:' | tail -n 10
sudo "$HDC" -t "$TARGET" file recv \
    "$REMOTE_RKNN_LOG" "$HOME/pcie_host/${RUN_TAG}_board.log"
```

结束本次调试时，先退出终端 B 的窗口，再停止板端服务：

```bash
sudo "$HDC" -t "$TARGET" shell killall pcie_rknn_probe
sudo "$HDC" -t "$TARGET" shell pidof pcie_rknn_probe
```

### 改源码后重新编译（仅在需要时执行）

如果仅重启且已验证文件都在，跳过本节。Windows 源码有更新时，同步到 Ubuntu 本地工作目录再构建；修改了用户态程序不要求重编译驱动，修改驱动或更新内核时需要重新构建并签名 `.ko`。

```bash
mkdir -p "$HOME/pcie_host"
cp -v \
    /mnt/hgfs/pcie_host/Makefile \
    /mnt/hgfs/pcie_host/pango_pcie_bar_test.c \
    /mnt/hgfs/pcie_host/pango_pcie_small_dma_test.c \
    /mnt/hgfs/pcie_host/pango_pcie_frame_dma_test.c \
    /mnt/hgfs/pcie_host/pango_pcie_fpga_pattern_test.c \
    /mnt/hgfs/pcie_host/pango_pcie_hdmi_status_test.c \
    /mnt/hgfs/pcie_host/pango_pcie_hdmi_frame_test.c \
    /mnt/hgfs/pcie_host/pcie_rknn_probe.c \
    /mnt/hgfs/pcie_host/build_rknn_probe.sh \
    /mnt/hgfs/pcie_host/preview_stream.py \
    /mnt/hgfs/pcie_host/rknn_stream.py \
    /mnt/hgfs/pcie_host/draw_detections.py \
    /mnt/hgfs/pcie_host/capture_stream.py \
    /mnt/hgfs/pcie_host/frame_to_png.py \
    /mnt/hgfs/pcie_host/frame_format.py \
    /mnt/hgfs/pcie_host/frame_checksum.c \
    /mnt/hgfs/pcie_host/build_checksum.sh \
    /mnt/hgfs/pcie_host/README.md \
    /mnt/hgfs/pcie_host/RKNN.md \
    "$HOME/pcie_host/"

cd "$HOME/pcie_host"
bash build_checksum.sh
RKNN_RUNTIME="$HOME/rknn-yolov5-test/lib/librknnrt.so" bash build_rknn_probe.sh
```

仅在需要更新驱动时构建并签名：

```bash
export KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10"
export LLVM_DIR="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin"

make KDIR="$KDIR" LLVM_DIR="$LLVM_DIR" && \
    "$KDIR/scripts/sign-file" sha1 \
    "$KDIR/certs/signing_key.pem" \
    "$KDIR/certs/signing_key.x509" \
    pango_pcie_hdmi_frame_test.ko

modinfo -F vermagic pango_pcie_hdmi_frame_test.ko
modinfo -F signer pango_pcie_hdmi_frame_test.ko
```

确认构建成功、模块与板端内核匹配且签名者不为空，再执行 A2/A3。若旧模块仍加载，先停止读取者并卸载旧模块，再加载新版；直接再次 `insmod` 或替换磁盘文件不会更新已经加载的模块。驱动重编译的详细检查见 [TIMING.md](TIMING.md)。

## 分阶段验收记录

| 阶段 | 目标 | 验收标准 |
| --- | --- | --- |
| 1 | 使用官方例程的 BAR0 RAM 验证主机到 FPGA 的 Memory Write 和 FPGA 到主机的 Completion | 已通过：RK3568 内核日志出现 `BAR0 MMIO round-trip passed (3 words)` |
| 2 | FPGA 对 RK3568 的小块 coherent 缓冲区执行 DMA 读写 | 已通过：RK3568 内核日志出现 `64-byte FPGA DMA read/write round-trip passed` |
| 3A | 使用官方 bitstream 对两帧测试图案做分块 DMA 闭环 | 已通过：两帧各 4096 字节校验成功 |
| 3B | FPGA 自行产生测试图案并分块发送一帧 | 已通过：RK3568 日志出现 `FPGA-generated 64x64 frame DMA passed` |
| 4A | 验证 HDMI 工程的 PCIe 帧桥接寄存器 | 已通过：`bytes=2073600`、`signature=0x46524d45`，状态回读通过 |
| 4B | 从 HDMI/DDR 缓冲区传输一帧 | 已通过：2026-10-04 实机出现 `HDMI frame DMA passed: 2073600 bytes`，最终状态 `0x03f40002` |
| 4B 图像验收 | 导出接收到的单帧，检查 HDMI 画面内容 | 已通过：PGM 为 2,073,617 字节，转换校验值与驱动一致（`0x989973b4`），用户确认图像正常 |
| 4C-1 | 同一驱动内按需重复采集，验证画面更新 | 已通过：30 帧文件完整，`frame_seq` 连续，校验值和图像内容均变化 |
| 4C-2-阶段一 | 主机侧连续采集环和帧记录格式 | 已通过：修复 stream 重复打开的缓冲生命周期问题；连续 30 条记录序号 15～44、校验通过、无序号跳号 |
| 4C-2-阶段二 | FPGA 帧边界、持续 DMA、丢帧统计 | 待完成：当前位流仍按主机请求搬运帧缓存 |
| 4D | RK3568 直接读取并通过 TCP 向 Ubuntu 实时预览 | USB/HDC 已实机预览；缓存副本优化后采集约 18 fps，Ubuntu C 校验约 3 ms/帧；以太网直连步骤见本文末尾，尚待实机验证；USB 操作见 [PREVIEW.md](PREVIEW.md)，分段耗时见 [TIMING.md](TIMING.md) |
| 5 | RK3568 将帧交给图像识别流水线 | 2026-10-05 用户确认连续推理、同帧结果传输和实时带框窗口功能通过；基础识别框架里程碑完成，持续性能与准确率仍待评估；操作见 [RKNN.md](RKNN.md) |

## 阶段 1：BAR0 读写闭环

当前工程 `ipcore/pcie_test/pnr/example_design/pango_pcie_top.pds` 编译的顶层位于
`ipcore/pcie_test/example_design/bench/pango_pcie_top.v`。官方例程已经将 BAR0 Memory Write
写入内部 RAM，BAR0 Memory Read 从同一 RAM 返回 Completion。此阶段不需要更改 FPGA RTL；
请烧录该官方例程的 bitstream。设备可枚举及 BAR 已分配说明物理 PCIe 链路已经训练成功，
本阶段验证应用所需的双向 MMIO 事务。

`pango_pcie_bar_test` 匹配 `0755:0755`，在 BAR0 的 `0x200`、`0x204`、`0x210`
写入三个不同的 32 位值，再逐一读回。两个地址位于同一 16 字节 RAM 字，第三个地址
位于下一字，可同时检查数据通路、字节通道与地址译码。此区域仅供本阶段测试，
测试时不要同时运行其它访问 BAR0 的程序。

Ubuntu 虚拟机通过 VMware 的 `/mnt/hgfs/pcie_host` 访问 Windows 工程。
`~/pcie_host` 是独立的本地编译目录；Windows 文件更新后，需要重新复制所需文件。
如果 `/mnt/hgfs` 为空，先执行 `sudo vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other`。
若提示 `mountpoint is not empty` 且能看到源文件，说明共享目录已经挂载，无需重复挂载。

在 Ubuntu 中同步并编译：

```sh
ls -l /mnt/hgfs/pcie_host/pango_pcie_bar_test.c
cp -v /mnt/hgfs/pcie_host/Makefile /mnt/hgfs/pcie_host/pango_pcie_bar_test.c /mnt/hgfs/pcie_host/pango_pcie_small_dma_test.c /mnt/hgfs/pcie_host/pango_pcie_frame_dma_test.c /mnt/hgfs/pcie_host/pango_pcie_fpga_pattern_test.c /mnt/hgfs/pcie_host/pango_pcie_hdmi_status_test.c /mnt/hgfs/pcie_host/README.md "$HOME/pcie_host/"
export PCIE_HOST="$HOME/pcie_host"
cd "$PCIE_HOST"
test -f pango_pcie_bar_test.c
grep -n '^obj-m += pango_pcie_bar_test.o$' Makefile
make KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10" LLVM_DIR="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin"
ls -l pango_pcie_bar_test.ko
```

`KDIR` 必须指向与 RK3568 当前运行的 `5.10.184` 内核匹配的构建目录。
`Module.symvers is missing` 不阻止生成 `.ko`，但加载时仍可能暴露符号或版本不匹配。

编译成功后，通过 HDC 推送并在设备上加载模块：

```sh
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
sudo "$HDC" -t "$TARGET" shell lspci -nn -vv -s 21:00.0
sudo "$HDC" -t "$TARGET" shell cat /sys/bus/pci/devices/0000:21:00.0/resource
sudo "$HDC" -t "$TARGET" file send "$PCIE_HOST/pango_pcie_bar_test.ko" /data/local/tmp/pango_pcie_bar_test.ko
sudo "$HDC" -t "$TARGET" shell ls -l /data/local/tmp/pango_pcie_bar_test.ko
sudo "$HDC" -t "$TARGET" shell id
sudo "$HDC" -t "$TARGET" shell insmod /data/local/tmp/pango_pcie_bar_test.ko
sudo "$HDC" -t "$TARGET" shell dmesg | tail -n 50
```

如果 `insmod` 提示 `Invalid module format`，说明模块使用的 `KDIR`、内核配置或编译器
与设备运行的 `5.10.184` 不一致；请先执行 `uname -a`，再确认 `KDIR` 对应同一套内核。
如果提示 `Permission denied`，请确认当前设备是可加载外部模块的工程版本，并检查
`sudo "$HDC" -t "$TARGET" shell id` 的设备权限。

如果提示 `Key was rejected by service`，并且设备日志包含
`Loading of unsigned module is rejected`，说明设备内核强制验证模块签名；
文件传输和 root 权限已通过，BAR0 测试尚未运行。在 Ubuntu 检查：

```sh
modinfo -F vermagic "$PCIE_HOST/pango_pcie_bar_test.ko"
modinfo -F signer "$PCIE_HOST/pango_pcie_bar_test.ko"
grep -E '^CONFIG_MODULE_SIG|^CONFIG_MODVERSIONS' "$HOME/openharmony/out/kernel/OBJ/linux-5.10/.config"
```

`signer` 为空表示模块未签名。若可取得当前设备内核信任的模块签名私钥和证书，
用同一套内核构建目录中的 `scripts/sign-file` 签名后重新推送；
仅有公钥或使用其它私钥签名均无法通过。若私钥不可用，需要基于当前内核源码
重新构建并部署信任自有密钥的内核；这属于独立的系统镜像变更，先确认签名配置。

当前 RK3568 构建配置为 `CONFIG_MODULE_SIG_FORCE=y`、`CONFIG_MODULE_SIG_HASH="sha1"`、
`CONFIG_MODULE_SIG_KEY="certs/signing_key.pem"`。先在 Ubuntu 检查当前内核构建目录：

```sh
KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10"
ls -l "$KDIR/scripts/sign-file" "$KDIR/certs/signing_key.pem" "$KDIR/certs/signing_key.x509"
```

只有确认私钥和证书属于**设备当前运行的内核镜像**后，才能执行：

```sh
"$KDIR/scripts/sign-file" sha1 "$KDIR/certs/signing_key.pem" "$KDIR/certs/signing_key.x509" "$PCIE_HOST/pango_pcie_bar_test.ko"
modinfo -F signer "$PCIE_HOST/pango_pcie_bar_test.ko"
sudo "$HDC" -t "$TARGET" file send "$PCIE_HOST/pango_pcie_bar_test.ko" /data/local/tmp/pango_pcie_bar_test.ko
sudo "$HDC" -t "$TARGET" shell insmod /data/local/tmp/pango_pcie_bar_test.ko
sudo "$HDC" -t "$TARGET" shell dmesg | tail -n 60
```

私钥不要发送给其他人。若签名后仍被拒绝，请保留设备端 `dmesg`：
这通常表示签名密钥不在当前内核的信任密钥链中。

通过时应看到 `BAR0 MMIO round-trip passed (3 words)`。失败时模块会打印第一个不一致
的 BAR 偏移、期望值及读回值；请同时记录 `lspci -nn -vv -s <设备 BDF>` 和相关 `dmesg`。
测试结束后在 RK3568 上执行 `sudo "$HDC" -t "$TARGET" shell rmmod pango_pcie_bar_test`。
如果设备已绑定其它驱动，先停止该驱动并解除绑定，再加载此模块。同一时刻只让一个驱动管理此设备。

已有 `pango_pcie_dma_test.c` 针对旧视频 DMA 寄存器协议，当前 Makefile 暂不编译它。
该协议还未在官方例程上建立，不能用它判断阶段 1 是否成功。

## 阶段 2：64 字节 DMA 闭环

仍使用官方例程的同一 bitstream。`pango_pcie_small_dma_test` 分配 128 字节的
coherent 缓冲区，前 64 字节填充随机测试数据，后 64 字节填充 `0xa5`。
它先通过 BAR1 命令让 FPGA 对前半区执行 64 字节 Memory Read，将 Completion 数据写入
FPGA 内部 BAR2 RAM；随后让 FPGA 从 BAR2 RAM 对后半区执行 64 字节 Memory Write。
驱动只有在目标区的 64 字节逐字节全部匹配源数据时才报告通过。

这一阶段要求 32 位 DMA 地址，以复用官方仿真验证过的命令路径。官方端点使用
BAR1 `0x100` 写命令、`0x110` 写目标地址低 32 位。64 字节是 16 个 DWORD，
所以命令长度字段为 `15`；Memory Read 命令是 `0x0000000f`，Memory Write 命令是
`0x0100000f`。每次先写命令再写地址，与官方 testbench 顺序一致；BAR1 写之后
读 BAR0，以完成 posted write 的排序与冲刷。

编译前先按前文同步 VMware 共享目录的五个源码文件和 Makefile。Makefile 同时生成
五个测试模块；**设备端一次只能加载其中一个**。卸载阶段 1 模块后，
在 Ubuntu 中编译、签名阶段 2 模块并发送到设备：

```sh
cd "$HOME/pcie_host"
make KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10" LLVM_DIR="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin"
KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10"
"$KDIR/scripts/sign-file" sha1 "$KDIR/certs/signing_key.pem" "$KDIR/certs/signing_key.x509" pango_pcie_small_dma_test.ko
modinfo -F signer pango_pcie_small_dma_test.ko
HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
TARGET="7001005458323933328a26cf4db23900"
sudo "$HDC" -t "$TARGET" shell rmmod pango_pcie_bar_test
sudo "$HDC" -t "$TARGET" file send "$HOME/pcie_host/pango_pcie_small_dma_test.ko" /data/local/tmp/pango_pcie_small_dma_test.ko
sudo "$HDC" -t "$TARGET" shell insmod /data/local/tmp/pango_pcie_small_dma_test.ko
sudo "$HDC" -t "$TARGET" shell dmesg | tail -n 80
```

如果阶段 1 模块已卸载，`rmmod` 可报告模块不存在；继续执行后续命令即可。
成功日志为 `64-byte FPGA DMA read/write round-trip passed`。
失败日志会指出首个不匹配的目标字节；此时提供完整的 `insmod` 输出、
设备端 `dmesg` 和 `lspci -nn -vv -s 21:00.0` 以便定位。

## 阶段 3A：两帧分块 DMA 闭环

阶段 2 已在 RK3568 上通过。阶段 3A 继续使用相同的官方 bitstream，先验证更长的
TLP、分块地址递增和帧边界。`pango_pcie_frame_dma_test` 在主机 coherent 缓冲区生成
两幅不同的 64×64、每像素 8 位的测试图案。每帧 4096 字节，按 256 字节分为 16 块；
每一块都先由 FPGA DMA Read 读入内部 BAR2 RAM，再由 FPGA DMA Write 送回目标帧。
主机逐块等待并比较，最后逐字节比较完整帧。此阶段的数据源仍是主机，下一阶段
3B 才把测试图案生成器移入 FPGA。当前 DMA Read 之后用 100 ms 固定等待，
正式视频流需要增加完成状态或中断，不能依赖该等待时间。

在 Ubuntu 中同步文件，编译并签名新模块：

```sh
cp -v /mnt/hgfs/pcie_host/Makefile /mnt/hgfs/pcie_host/pango_pcie_bar_test.c /mnt/hgfs/pcie_host/pango_pcie_small_dma_test.c /mnt/hgfs/pcie_host/pango_pcie_frame_dma_test.c /mnt/hgfs/pcie_host/pango_pcie_fpga_pattern_test.c /mnt/hgfs/pcie_host/pango_pcie_hdmi_status_test.c /mnt/hgfs/pcie_host/pango_pcie_hdmi_frame_test.c "$HOME/pcie_host/"
cd "$HOME/pcie_host"
make KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10" LLVM_DIR="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin"
KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10"
"$KDIR/scripts/sign-file" sha1 "$KDIR/certs/signing_key.pem" "$KDIR/certs/signing_key.x509" pango_pcie_frame_dma_test.ko
modinfo -F signer pango_pcie_frame_dma_test.ko
```

在 RK3568 上先卸载阶段 2 模块，再推送并加载新模块：

```sh
HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
TARGET="7001005458323933328a26cf4db23900"
sudo "$HDC" -t "$TARGET" shell rmmod pango_pcie_small_dma_test
sudo "$HDC" -t "$TARGET" file send "$HOME/pcie_host/pango_pcie_frame_dma_test.ko" /data/local/tmp/pango_pcie_frame_dma_test.ko
sudo "$HDC" -t "$TARGET" shell insmod /data/local/tmp/pango_pcie_frame_dma_test.ko
sudo "$HDC" -t "$TARGET" shell dmesg | tail -n 40
```

成功时依次看到 `frame 0 complete: 4096 bytes`、`frame 1 complete: 4096 bytes`、
`two-frame chunked DMA round-trip passed`。测试通常需要数秒；若失败，日志会指出
首个不匹配的帧号、块号、字节偏移及读回值。

## 阶段 3B：FPGA 产生测试图案

阶段 3A 的 RK3568 日志已显示两帧各 4096 字节校验成功。阶段 3B 修改了工程中
`ipcore/pcie_test/example_design/rtl/ipsl_pcie_dma_ctrl/ipsl_pcie_dma_rx_top.v`，
给 BAR2 RAM 增加一个主机触发的固定图案填充器。主机向 BAR1 `0x180` 写入
`0x5041544e` 后，FPGA 依次填充 BAR2 的前 16 个 128 位字：第一个字的 16 个
字节均为 `0x00`，第二个均为 `0x01`，依此到 `0x0f`。
`pango_pcie_fpga_pattern_test` 只分配接收缓冲区，不向 FPGA 提供源图像；
它让 FPGA 将这一 256 字节图案重复 DMA Write 到 64×64 灰度帧的 16 个分块，
逐块及整帧核对数据。此阶段验证 FPGA 内部数据源到主机的写入路径。

本阶段**必须重新编译 FPGA 工程并烧录新的 bitstream**；官方原始 `.sbit` 不含
图案填充器。重新生成前先备份当前已验证的官方 `.sbit`。用 Pango Design Suite 打开
`ipcore/pcie_test/pnr/example_design/pango_pcie_top.pds`，重新运行综合、布局布线
和生成位流。输出文件为
`ipcore/pcie_test/pnr/example_design/generate_bitstream/pango_pcie_top.sbit`。
JTAG 重新烧录会复位 FPGA 端点配置，而 RK3568 若一直运行，内核可能仍保存旧的
BAR 资源记录。烧录后保持 FPGA 已配置且供电，重新启动 RK3568，使 PCIe 从头枚举
并重新配置 BAR；即使 `lspci` 还能列出设备，也应执行这一步。重启后确认
`lspci -nn` 仍可枚举到 `0755:0755`，再加载测试模块。

Ubuntu 端同步五个源码文件和 Makefile，编译、签名新模块：

```sh
cp -v /mnt/hgfs/pcie_host/Makefile /mnt/hgfs/pcie_host/pango_pcie_bar_test.c /mnt/hgfs/pcie_host/pango_pcie_small_dma_test.c /mnt/hgfs/pcie_host/pango_pcie_frame_dma_test.c /mnt/hgfs/pcie_host/pango_pcie_fpga_pattern_test.c /mnt/hgfs/pcie_host/pango_pcie_hdmi_status_test.c "$HOME/pcie_host/"
cd "$HOME/pcie_host"
make KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10" LLVM_DIR="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin"
KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10"
"$KDIR/scripts/sign-file" sha1 "$KDIR/certs/signing_key.pem" "$KDIR/certs/signing_key.x509" pango_pcie_fpga_pattern_test.ko
modinfo -F signer pango_pcie_fpga_pattern_test.ko
```

加载新模块前卸载阶段 3A 模块：

```sh
HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
TARGET="7001005458323933328a26cf4db23900"
sudo "$HDC" -t "$TARGET" shell rmmod pango_pcie_frame_dma_test
sudo "$HDC" -t "$TARGET" file send "$HOME/pcie_host/pango_pcie_fpga_pattern_test.ko" /data/local/tmp/pango_pcie_fpga_pattern_test.ko
sudo "$HDC" -t "$TARGET" shell insmod /data/local/tmp/pango_pcie_fpga_pattern_test.ko
sudo "$HDC" -t "$TARGET" shell dmesg | tail -n 40
```

成功日志为 `FPGA-generated 64x64 frame DMA passed`。如失败，请保留
`insmod` 输出和含首个不匹配块号、字节位置的设备日志。

首次实测中，新模块已加载，但第 0 块第 0 字节仍为目标缓冲区初始值 `0xa5`，
随后探测返回 `-110`。Windows 工程中的位流生成时间晚于 RTL 修改时间，配置
日志显示烧录成功，时序报告为 `All Constraints Met`。下一步先用同一份新位流
重新运行已通过的阶段 3A 模块，区分基础 DMA 回归和阶段 3B 新图案路径问题。
首次在未重启 RK3568 的情况下重试阶段 3A 也失败，且设备日志的运行时间连续；
保持 FPGA 新位流已加载并重启 RK3568 后，阶段 3B 成功输出
`FPGA-generated 64x64 frame DMA passed`。这与端点复位后主机未重新枚举的判断
相符。以后每次通过 JTAG 重新烧录 FPGA，均先重启 RK3568，再进行 PCIe 测试。
阶段 3A 可用以下命令再次验证：

```sh
sudo "$HDC" -t "$TARGET" shell reboot
sudo "$HDC" list targets
sudo "$HDC" -t "$TARGET" shell lspci -nn
sudo "$HDC" -t "$TARGET" shell cat /sys/bus/pci/devices/0002:21:00.0/resource
sudo "$HDC" -t "$TARGET" shell insmod /data/local/tmp/pango_pcie_frame_dma_test.ko
sudo "$HDC" -t "$TARGET" shell dmesg | grep -E 'pango_pcie_(frame_dma|fpga_pattern)_test' | tail -n 15
```

等 HDC 重新连接后再执行后续命令。若设备上的阶段 3A `.ko` 文件已删除，
先从 Ubuntu 重新推送。请保留 `insmod` 输出、BAR 资源和以上过滤后的日志。

## 已确认的协议差异

`06_hdmi_loop_1/src/pcie/pcie_frame_dma_bridge.v` 将目标地址和启动命令定义为 BAR1
`0x130`、`0x140`、`0x150`。当前官方例程的 `ipsl_pcie_dma_controller` 实际译码的是
BAR1 `0x100`（DMA 命令）、`0x110`（目标地址低 32 位）和 `0x120`（目标地址高 32 位）。
因此，旧驱动即使链路和 BAR 都正常，也不会启动官方 DMA 控制器。阶段 1 通过后，阶段 2
会先按官方控制器的命令格式发送小块固定数据，再恢复视频桥需要的寄存器协议或统一两端
的寄存器定义。

## 阶段 4A：确认 HDMI 工程的 PCIe 帧桥

阶段 3B 已证明 FPGA 内部图案能经 PCIe 写入 RK3568。下一步先切换到已经集成
HDMI、DDR 和 PCIe 帧桥的 `06_hdmi_loop_1` 工程，用只读测试确认 RK3568 访问到的
确实是该工程的帧桥。此测试不启动 DMA，也不要求 HDMI 输入已有图像。

Windows 端备份当前阶段 3B 的位流，打开 `06_hdmi_loop_1/hdmi_loop.pds`，
重新执行综合、布局布线和位流生成，再烧录新生成的
`06_hdmi_loop_1/generate_bitstream/hdmi_loop.sbit`。核对文件修改时间，确保使用本次修改
之后生成的位流。只重新烧录原有 `.sbit` 不会包含 RTL 修复。保持 FPGA 已配置且供电，
**重启 RK3568 让 PCIe 重新枚举和配置 BAR**。旧工程的时序报告有其它时钟路径
违例，因此这一阶段只验收桥接寄存器；图像和连续传输留待后续单独验证。

本次实机失败返回 `status=0x54321000`、`bytes=3703216246`、
`signature=0x543210fe`、`reserved=0xdcba9876`。其中 `3703216246` 为
`0xdcba9876`，读回的是测试 RAM 数据。HDMI 工程的 `ipsl_pcie_dma.v` 原先根据
当前读地址选择状态字，但 RAM 数据在时钟边沿之后才返回；地址在同一个边沿递增，
导致状态数据被切换回 RAM 数据。修复后，读使能有效时同步锁存状态选择与状态数据，
使状态返回和 RAM 返回对齐。

`bench/pcie_hdmi_status_tb.v`（位于 HDMI 工程的 PCIe 例程目录）使用真实 DMA
模块和读控制器、一拍同步 RAM 行为模型进行回归。修改前，偏移 `0x0` 复现
`0x54321000` 错误；修改后，四个独立 DWORD、普通 RAM、完整 16 字节状态以及跨越
状态/RAM 边界的读取全部通过，每项还检查发送暂停期间数据保持稳定。仿真通过日志为
`HDMI BAR0 status read regression passed`。该测试只用于仿真，不加入综合源文件。

本次只修改 FPGA RTL，已成功加载过的 `pango_pcie_hdmi_status_test.ko` 可以继续
使用，无需重新编译或签名。首次准备模块时，才需要在 Ubuntu 中同步源码、编译并签名：

```sh
cp -v /mnt/hgfs/pcie_host/Makefile /mnt/hgfs/pcie_host/pango_pcie_bar_test.c /mnt/hgfs/pcie_host/pango_pcie_small_dma_test.c /mnt/hgfs/pcie_host/pango_pcie_frame_dma_test.c /mnt/hgfs/pcie_host/pango_pcie_fpga_pattern_test.c /mnt/hgfs/pcie_host/pango_pcie_hdmi_status_test.c "$HOME/pcie_host/"
cd "$HOME/pcie_host"
make KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10" LLVM_DIR="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin"
KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10"
"$KDIR/scripts/sign-file" sha1 "$KDIR/certs/signing_key.pem" "$KDIR/certs/signing_key.x509" pango_pcie_hdmi_status_test.ko
modinfo -F signer pango_pcie_hdmi_status_test.ko
```

RK3568 重启并重新连接 HDC 后：

```sh
HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
TARGET="7001005458323933328a26cf4db23900"
sudo "$HDC" -t "$TARGET" shell lspci -nn
sudo "$HDC" -t "$TARGET" file send "$HOME/pcie_host/pango_pcie_hdmi_status_test.ko" /data/local/tmp/pango_pcie_hdmi_status_test.ko
sudo "$HDC" -t "$TARGET" shell insmod /data/local/tmp/pango_pcie_hdmi_status_test.ko
sudo "$HDC" -t "$TARGET" shell dmesg | grep 'pango_pcie_hdmi_status_test' | tail -n 8
```

成功日志为 `HDMI frame bridge status read passed`，并显示
`bytes=2073600`、`signature=0x46524d45`。若不一致，日志会列出 BAR0 的四个
原始 DWORD，便于区分位流、BAR 映射和寄存器布局问题。

## 阶段 4B：从 HDMI/DDR 缓冲区传输一帧

### 2026-10-04 实机验收结果

用户烧录本轮 DDR 上传修复后的位流，编译、签名并加载新驱动，得到：

```text
HDMI frame capture: bytes=2073600 signature=0x46524d45
HDMI frame DMA start submitted: status=0x000001
HDMI debug: raw=0xd418c988 pcie=WAIT_DDR(1) ddr=WAIT_DATA(2) calib=1 frame_ready=1 active=1 ready=0 cmd_seen=1 done_seen=0 beats=24/128 arvalid=0 arready=0 req=0 busy=0 chunk=27
HDMI frame DMA passed: 2073600 bytes, changed=2073256 checksum=0xca798bce status=0x3f40002
first 16 bytes: 10 3d 3c 3c 3d 3d 3c 3c 3c 3c 3c 3d 3c 3c 3d 3d
```

| 项目 | 解释 |
|---|---|
| `status=0x03f40002` | 块序号为 `0x3f4=1012`（最后一块）；帧完成位为 1，传输模式位和错误位均为 0 |
| `changed=2073256` | 2,073,256 个字节不同于缓冲区初始值 `0xa5`，实际 DMA 写入已发生 |
| 剩余 344 个字节 | 当前只统计是否等于 `0xa5`，真实灰度值也可能为 `0xa5`；此计数不能判断这些位置是否写入 |
| `checksum=0xca798bce` | 当前接收帧的 FNV-1a 校验值；输入画面变化时允许变化，不是固定通过值 |
| `chunk=27 beats=24/128` | 启动后的运行快照：正在读取第 28 个块（块序号从 0 开始），当前块尚未读满；这不是超时日志 |
| `ready=0 done_seen=0 req=0` | 当前 DDR 命令正在执行，还未到该块完成及提交 PCIe 写请求的时刻；结合后续整帧完成日志，这是正常中间状态 |

从启动日志时间 `96.773566` 到通过日志时间 `97.318346` 约 545 ms。
该间隔包括软件日志、状态轮询、DDR 分块和 PCIe 传输开销，不能作为纯 PCIe 带宽
或连续视频帧率的测量结果。阶段 4B 的单帧 DMA 和图像验收均已通过；PGM 下载后
转换得到的像素校验值与驱动一致，用户确认 1920×1080 灰度图像正常。首次采集的
不可变图像副本仍通过 sysfs 导出，具体操作见“阶段 4B 图像验收”。当前推进阶段
4C-1 的重复采集；长期稳定性、持续缓冲、源帧计数及帧边界统计仍需后续验证。

### 故障定位与修复记录

修复前实机测试打印 `HDMI frame DMA start submitted: status=0x000001`，随后
20 秒超时，状态仍为 `0x000001`。BAR1 启动已接收，块序号仍为零。
旧状态字不包含状态机和握手细节，不能据此确定是 DDR 等待还是 PCIe 请求等待。

已定位并修复 `06_hdmi_loop_1/src/pcie/pcie_frame_dma_bridge.v` 的地址译码。
用户日志中的 BAR1 为 `0x380904000`，大小为 `0x1000`（4 KiB）。官方写控制器
输出完整 TLP 地址的 `[15:4]`，所以 BAR1 偏移 `0x130`、`0x140`、`0x150` 实际
输出 `0x413`、`0x414`、`0x415`。原帧桥匹配完整 12 位地址，没有移除 BAR 基址，
导致目标 DMA 地址和启动命令都被忽略。修复后只匹配 `bar1_wr_addr[7:0]` 的
`0x13`、`0x14`、`0x15`，即 4 KiB BAR 内按 16 字节单位表示的寄存器偏移。
主机的三个字节偏移保持为 `0x130`、`0x140`、`0x150`。

`06_hdmi_loop_1/ipcore/pcie_test/example_design/bench/pcie_hdmi_start_tb.v` 使用
真实官方写控制器，将完整主机地址送入帧桥。修改前复现了 `status=00000000`；
修改后在 BAR 基址 `0x380904000`、`0`、`0x38090f000` 下，启动、首个 DDR 读命令
及首个 DMA 请求的长度和 64 位目标地址全部通过，且其它偏移和写零不会启动。
成功日志为 `HDMI BAR1 start regression passed`。该测试采用行为 RAM 和模拟 DDR
命令完成信号，只验证启动路径，不代表实机整帧内容已验收；测试文件不加入综合。

本轮继续检查了 DDR 上传路径，修复内容如下：

| 文件 | 修改 | 解决的问题 |
|---|---|---|
| `src/ddr/fram_buf.v` | 保留当前读通道归属，等待已有命令的数据和地址发送均结束后再交给上传 | 上传不能中途改变显示命令，也不能把旧显示数据及完成信号交给帧桥 |
| `src/ddr/wr_cmd_trans.v` | 接收命令时锁存地址及长度；后续分段只用锁存值 | 外部长度从显示的 120 拍切换到上传的 128 拍时，原命令的计数和 `ready` 判定不能改变 |
| `src/ddr/wr_cmd_trans.v` | 最后一个有效数据到达时才输出 `rd_cmd_done` | 最后一拍数据延迟时不能提前通知 PCIe 读取暂存 RAM |
| `src/pcie/pcie_frame_dma_bridge.v` | 首块等待帧缓存初始化后才暂停采集；仅在上传读状态写暂存 RAM | 避免在首个 HDMI 帧尚未写入时冻结采集，并过滤无关读数据 |
| `src/pcie/pcie_frame_dma_bridge.v`、`src/hdmi_loop.v` | BAR0 `0x0c` 增加带版本标记的诊断字 | 可以判断 DDR 校准、首帧就绪、读命令、数据返回及 PCIe 请求分别走到了哪里 |
| `pango_pcie_hdmi_frame_test.c` | 启动和超时打印 `HDMI debug:` | 下一轮实机测试直接输出状态名称和数据拍数 |

修改前新增集成仿真复现 `Display data was routed into the upload before its command`。
修复后以下回归通过：

| 测试 | 覆盖内容 | 通过日志 |
|---|---|---|
| `ddr_read_command_tb.v` | 命令接收后立刻改变外部地址和长度，原 120 拍读取仍正确完成；最后一拍延迟不能提前完成 | `DDR read command regression passed` |
| `pcie_hdmi_start_tb.v` | 官方 BAR1 写控制器、三个 BAR 基址、64 位 DMA 地址及首块长度 | `HDMI BAR1 start regression passed` |
| `pcie_hdmi_ddr_tb.v` | 显示读命令执行中启动上传、等待首帧、DDR 地址及数据延迟、灰度打包、1013 块、最后 64 拍、连续运行两次完整帧传输 | `HDMI DDR upload regression passed` |

集成测试使用实际 `fram_buf`、读命令转换器、AXI 读控制器和帧桥；显示命令由测试注入，
DDR 响应、HDMI 行缓存 RAM 和 PCIe 暂存 RAM 使用行为模型。PCIe 请求的应答和
busy 信号也由测试模拟，因此仿真不替代 DDR 校准、HDMI 采集、PCIe IP 和板上时序验收。
所有测试文件仅用于仿真，不加入 PDS 综合文件。

Windows PowerShell 已安装 Icarus Verilog 时，可以运行：

```powershell
& 'F:\B_file\jichuangsai\Pango_FPGA\06_hdmi_loop_1\ipcore\pcie_test\example_design\bench\run_hdmi_regression.ps1' -IverilogDirectory '你的 Icarus bin 目录'
```

本次修复需要在 Windows 打开 `06_hdmi_loop_1/hdmi_loop.pds`，重新综合、布局布线
并生成 `06_hdmi_loop_1/generate_bitstream/hdmi_loop.sbit`，核对新文件时间后烧录。
保持 FPGA 已配置且供电，再重启 RK3568 重新枚举 PCIe。仅重新加载 `.ko` 无法
更新 FPGA 逻辑。本轮主机驱动增加了诊断打印，需要同步源码、重新编译并签名。

阶段 4A 已确认 HDMI 工程的帧桥状态寄存器可以正确读取。阶段 4B 使用
`pango_pcie_hdmi_frame_test.ko` 向 BAR1 写入 RK3568 的 32 位一致性 DMA 地址，
再写入启动寄存器 `0x150`。FPGA 从 HDMI/DDR 帧缓存读取 1920×1080 灰度帧，
分成 1012 个 2048 字节块和最后一个 1024 字节块，通过 PCIe Memory Write
写回 RK3568 的缓冲区。

该测试会验证帧签名、帧大小、DMA 完成状态和缓冲区是否发生变化，并打印整帧的
FNV-1a 校验值及前 16 个字节。阶段 4C-1 已增加可复用 DMA 缓冲区和字符设备读取；
用户空间映射、环形缓冲和持续帧管理放在阶段 4C-2。

新终端先设置 HDC。共享目录已经挂载时无需重复挂载；未挂载时执行：

```sh
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
sudo mkdir -p /mnt/hgfs
if ! mountpoint -q /mnt/hgfs; then
    sudo vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other
fi
ls -l /mnt/hgfs/pcie_host/Makefile /mnt/hgfs/pcie_host/pango_pcie_hdmi_frame_test.c
```

必须看到这两个共享文件后，再编译并签名新模块：

```sh
cp -v /mnt/hgfs/pcie_host/Makefile /mnt/hgfs/pcie_host/pango_pcie_hdmi_frame_test.c "$HOME/pcie_host/"
cd "$HOME/pcie_host"
make KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10" LLVM_DIR="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin"
KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10"
"$KDIR/scripts/sign-file" sha1 "$KDIR/certs/signing_key.pem" "$KDIR/certs/signing_key.x509" pango_pcie_hdmi_frame_test.ko
modinfo -F signer pango_pcie_hdmi_frame_test.ko
```

确保 HDMI 输入已经连接并有稳定图像。卸载阶段 4A 的状态测试模块，发送并加载
阶段 4B 模块：

```sh
sudo "$HDC" -t "$TARGET" shell lsmod | grep pango_pcie
```

仅卸载列表中实际存在的测试模块，例如 `pango_pcie_hdmi_status_test` 或
`pango_pcie_hdmi_frame_test`，然后发送新模块并加载：

```sh
sudo "$HDC" -t "$TARGET" file send "$HOME/pcie_host/pango_pcie_hdmi_frame_test.ko" /data/local/tmp/pango_pcie_hdmi_frame_test.ko
sudo "$HDC" -t "$TARGET" shell insmod /data/local/tmp/pango_pcie_hdmi_frame_test.ko
sudo "$HDC" -t "$TARGET" shell dmesg | grep -E 'pango_pcie_hdmi_frame_test (0002:21:00\.0:|.*probe)|pango_pcie_hdmi_frame_test: probe' | tail -n 20
```

成功日志包含 `HDMI frame DMA passed`，并显示 `2073600 bytes`、非填充值字节数、
校验值和 `status`。如果出现 `timed out waiting for HDMI frame DMA`，请保留完整
日志。启动后 `status` 的 bit 0 应置位；若超时仍为 `0x000001`，则帧桥已启动但
首块尚未完成，应进一步检查 DDR 读取和 PCIe DMA 握手。高位 `[25:16]` 为块序号，
bit 1 为帧完成，bit 2 为帧错误，不能把任何非零状态都视为整帧成功。

新驱动启动及超时都会打印 `HDMI debug:`。BAR0 `0x0c` 的最高字节是 `0xd4`；
如果日志显示 `HDMI debug unavailable`，需核对是否确实烧录了本轮新位流。
其余字段如下（DDR 信号经过两拍同步；瞬态日志中的字段不一定对应同一周期）：

| 字段 | 含义 |
|---|---|
| `pcie` | `IDLE(0)`、`WAIT_DDR(1)`、`SEND_DMA(2)`、`WAIT_DMA(3)` |
| `ddr` | `IDLE(0)`、`WAIT_READY(1)`、`WAIT_DATA(2)`、`WAIT_FRAME(3)` |
| `calib`、`frame_ready` | DDR 校准完成、HDMI 帧缓存至少已有一帧 |
| `active`、`ready` | 上传已经申请读通道、读通道已经交给上传且可接收命令 |
| `cmd_seen`、`done_seen` | 当前块已经发出读命令、已经收到读完成（事件锁存到下一块） |
| `beats` | 当前块收到的 256 位 DDR 数据拍数；正常 128 拍，最后一块 64 拍 |
| `arvalid`、`arready` | DDR AXI 读地址通道的当前握手电平 |
| `req`、`busy`、`chunk` | 当前 PCIe 写请求、发送 busy、块序号 |

| 超时诊断 | 下一步检查 |
|---|---|
| `calib=0` | DDR 时钟、复位、引脚及校准 |
| `ddr=WAIT_FRAME frame_ready=0` | HDMI 输入是否稳定、帧写入是否完成 |
| `ddr=WAIT_READY ready=0` | 显示命令是否排空、DDR 读通道是否恢复空闲 |
| `ddr=WAIT_DATA cmd_seen=1 beats=0` | DDR 读地址接收和数据返回 |
| `ddr=WAIT_DATA beats` 小于目标 | DDR 数据中断或命令长度/响应计数 |
| `pcie=SEND_DMA req=1` | PCIe 写请求没有应答 |
| `pcie=WAIT_DMA` | PCIe busy 的进入和退出；不能仅依据当前 `busy` 电平判断完成 |

发生超时后 FPGA 状态机可能仍在运行，卸载 `.ko` 只卸载主机模块，不能复位帧桥。
再次测试前重新配置 FPGA 并重启 RK3568 重新枚举，避免沿用超时状态。

如果没有重启 RK3568、需要重新运行同一测试，先检查当前模块：

```sh
sudo "$HDC" -t "$TARGET" shell lsmod | grep pango_pcie
```

确认哪个测试模块已加载后，先用 `rmmod` 卸载对应模块，再执行上面的 `insmod`
和日志命令。阶段 4A 状态模块和阶段 4B 帧模块都需要检查，避免已有驱动占用设备。
即使 PCI 驱动的 `probe` 校验失败，模块也可能仍然已加载。

## 阶段 4B 图像验收：导出并查看单帧

### 目标与接口

当前 FPGA 位流已经通过单帧 DMA 测试，本阶段沿用该位流。只更新 RK3568 驱动，
将成功接收到的灰度帧导出成文件，确认数据确实组成 HDMI 输入画面。

| 项目 | 说明 |
|---|---|
| 采集时机 | 每次成功加载并绑定 `pango_pcie_hdmi_frame_test.ko` 时采集一次 |
| 导出位置 | `/sys/bus/pci/devices/0002:21:00.0/hdmi_frame.pgm` |
| 文件格式 | 二进制 PGM（P5），1920×1080，8 位灰度 |
| 文件大小 | 2,073,617 字节：17 字节文件头和 2,073,600 字节像素 |
| 生命周期 | DMA 完成后复制并保留首次图像；当前驱动复用 DMA 缓冲区直到卸载或解绑，届时移除导出接口并释放副本 |
| 重复读取 | sysfs 始终返回首次采集的快照；新画面使用阶段 4C-1 字符设备获取，也可重新加载模块 |
| 转换工具 | `frame_to_png.py` 使用 Python 3 标准库，将本驱动导出的 PGM 转为灰度 PNG 并计算 FNV-1a 校验值 |

首次验收建议输入稳定的 1920×1080 静态画面，包含明显的文字、黑白区域及边缘。
导出结果是灰度图，验收时检查文字、物体位置和图像结构。

### 1. Ubuntu 同步、编译、签名

在 Ubuntu 终端执行：

```sh
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
sudo mkdir -p /mnt/hgfs
if ! mountpoint -q /mnt/hgfs; then
    sudo vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other
fi
ls -l /mnt/hgfs/pcie_host/pango_pcie_hdmi_frame_test.c /mnt/hgfs/pcie_host/frame_to_png.py
```

必须能看到这两个共享文件，再继续复制。源码检查应显示 `HDMI frame image ready` 行：

```sh
cp -v /mnt/hgfs/pcie_host/{Makefile,pango_pcie_hdmi_frame_test.c,frame_to_png.py,frame_format.py} "$HOME/pcie_host/"
cd "$HOME/pcie_host"
grep -n 'HDMI frame image ready' pango_pcie_hdmi_frame_test.c
make KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10" LLVM_DIR="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin"
KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10"
"$KDIR/scripts/sign-file" sha1 "$KDIR/certs/signing_key.pem" "$KDIR/certs/signing_key.x509" pango_pcie_hdmi_frame_test.ko
modinfo -F signer pango_pcie_hdmi_frame_test.ko
```

### 2. 更新 RK3568 模块并采集

HDMI 输入保持稳定。先检查模块列表，并仅卸载实际加载的测试模块。
此前 4B 已成功时，列表通常包含 `pango_pcie_hdmi_frame_test`：

```sh
sudo "$HDC" -t "$TARGET" shell lsmod | grep pango_pcie
sudo "$HDC" -t "$TARGET" shell rmmod pango_pcie_hdmi_frame_test
sudo "$HDC" -t "$TARGET" file send "$HOME/pcie_host/pango_pcie_hdmi_frame_test.ko" /data/local/tmp/pango_pcie_hdmi_frame_test.ko
sudo "$HDC" -t "$TARGET" shell insmod /data/local/tmp/pango_pcie_hdmi_frame_test.ko
sudo "$HDC" -t "$TARGET" shell dmesg | grep -E 'pango_pcie_hdmi_frame_test (0002:21:00\.0:|.*probe)|pango_pcie_hdmi_frame_test: probe' | tail -n 20
```

本阶段需要同时看到 `HDMI frame DMA passed` 和下面的新日志：

```text
HDMI frame image ready: /sys/bus/pci/devices/0002:21:00.0/hdmi_frame.pgm (2073617 bytes)
```

DMA 成功但导出失败时会打印 `failed to export HDMI frame image` 并返回错误。
文件不出现时先检查本次 `probe` 日志；`insmod` 没有输出并不能保证设备已绑定成功。

### 3. 导出到 Ubuntu 并查看

重定向必须在 RK3568 端执行，保留下面命令中的双引号。
先将 sysfs 快照保存到板上的普通文件，再通过 HDC 下载：

```sh
sudo "$HDC" -t "$TARGET" shell "cat /sys/bus/pci/devices/0002:21:00.0/hdmi_frame.pgm > /data/local/tmp/hdmi_frame.pgm"
sudo "$HDC" -t "$TARGET" shell ls -l /data/local/tmp/hdmi_frame.pgm
sudo "$HDC" -t "$TARGET" file recv /data/local/tmp/hdmi_frame.pgm "$HOME/pcie_host/hdmi_frame.pgm"
wc -c "$HOME/pcie_host/hdmi_frame.pgm"
python3 "$HOME/pcie_host/frame_to_png.py" "$HOME/pcie_host/hdmi_frame.pgm" "$HOME/pcie_host/hdmi_frame.png"
xdg-open "$HOME/pcie_host/hdmi_frame.png"
```

`wc -c` 应显示 `2073617`。Python 输出的 `checksum` 应等于同一次采集的
`HDMI frame DMA passed` 日志中的校验值。文件头不参与校验。
校验值一致确认导出、下载和转换使用的是同一份像素数据，不证明 HDMI 图像内容一定正确。

Ubuntu 没有图形桌面时，可以把 `hdmi_frame.png` 复制到 Windows 查看。
需要通过 VMware 共享目录复制时：

```sh
cp -v "$HOME/pcie_host/hdmi_frame.png" /mnt/hgfs/pcie_host/hdmi_frame.png
```

Windows 对应位置是 `09_pcie_test/pcie_host/hdmi_frame.png`，前提是 VMware 的
`pcie_host` 共享仍指向这个目录。

### 4. 验收与再次采集

| 检查项 | 通过标准 |
|---|---|
| 传输和导出 | `HDMI frame DMA passed` 与 `HDMI frame image ready` 均出现 |
| 文件完整性 | PGM 为 2,073,617 字节，转换成功，像素校验值与同一次驱动日志相同 |
| 图像内容 | PNG 是 1920×1080 灰度图，能对应 HDMI 输入，文字及物体位置正确 |
| 图像结构 | 没有明显的错行、拼接、重复块、大面积未更新或花屏 |
| 画面更新 | 改变 HDMI 输入画面后，重新采集的文件能反映新画面 |

第一次导出后改变输入画面，等待画面稳定，再按第 2 节卸载并加载模块，按第 3 节
重新导出。建议先另存第一次 PNG，方便对比。成功完成后的重新加载无需烧录 FPGA；
发生 DMA 超时时按故障处理步骤重新配置 FPGA 并重启 RK3568。

本阶段已实机验收通过：`wc -c` 显示 `2073617`，PNG 转换输出像素校验值
`0x989973b4`，与同一次 DMA 日志相同，用户确认画面正常。下一步是阶段 4C-1。

## 阶段 4C-1：同一驱动内重复采集多帧

### 目标与接口

沿用已经通过图像验收的 FPGA 位流。此次仅更新主机驱动及采集工具，先验证
30 次顺序采集能完成，且 HDMI 输入变化时导出画面能够更新。

| 项目 | 说明 |
|---|---|
| 首次图像 | 加载并绑定驱动时先采一帧，通过原 sysfs 文件保留该快照 |
| 新采集接口 | `/dev/pango_pcie_frame_0002:21:00.0`，名字包含实际 PCI BDF |
| 触发方式 | 打开字符设备后第一次非零长度读取触发一次 DMA；同一个文件描述符的后续读取返回同一张 PGM，读完返回 EOF |
| 获取新帧 | 关闭并重新打开设备；每次 `cat` 都打开一次设备，触发一次采集 |
| 缓冲区 | 驱动保留并复用一个 coherent DMA 缓冲区，用互斥锁串行执行采集；每个读者得到独立快照，关闭文件后释放 |
| 序号 | `frame_seq` 是本次驱动绑定期间通过校验的采集次数，首次采集为 1；不是 HDMI 源帧序号 |
| 时间 | `capture_us` 是启动到轮询观察到完成的耗时，不包括图像校验、复制、保存、下载及采集间隔 |
| 采集脚本 | `capture_many.sh [数量] [间隔秒数] [板端目录] [字符设备]`；数量为 1–1000，间隔为非负整数 |
| 检查工具 | `verify_frames.py` 检查文件编号、数量、PGM 头和像素大小，打印校验值，可转换 PNG |
| 出错恢复 | DMA 等待失败后驱动阻止再次启动；保留日志，重新配置 FPGA 并重启 RK3568 重新枚举后再试 |

本阶段按需采集，尚未实现持续采集、环形缓冲或源帧丢失统计。即使批次内文件编号
连续，也不能据此认定收到所有 HDMI 帧。采集会暂停 HDMI 帧缓存写入，帧率和无撕裂
保证仍需后续完善。当前格式是 8 位灰度，后续识别的输入格式需要单独确定。

### 1. Ubuntu 挂载、同步、编译和签名

所有命令均在 Ubuntu 终端执行。当前 Windows 目录是
`F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host`，VMware 的
`pcie_host` 共享需指向该目录。任何一步报错，先修复该步，再继续。

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"

sudo mkdir -p /mnt/hgfs
if ! mountpoint -q /mnt/hgfs; then
    sudo vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other
fi

ls -l /mnt/hgfs/pcie_host/pango_pcie_hdmi_frame_test.c /mnt/hgfs/pcie_host/capture_many.sh /mnt/hgfs/pcie_host/verify_frames.py

mkdir -p "$HOME/pcie_host"
cp -v /mnt/hgfs/pcie_host/Makefile \
  /mnt/hgfs/pcie_host/pango_pcie_hdmi_frame_test.c \
  /mnt/hgfs/pcie_host/capture_many.sh \
  /mnt/hgfs/pcie_host/verify_frames.py \
  /mnt/hgfs/pcie_host/frame_to_png.py \
  /mnt/hgfs/pcie_host/frame_format.py \
  "$HOME/pcie_host/"

cd "$HOME/pcie_host"
grep -n 'HDMI repeat capture ready' pango_pcie_hdmi_frame_test.c
KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10"
LLVM_DIR="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin"
make KDIR="$KDIR" LLVM_DIR="$LLVM_DIR"
"$KDIR/scripts/sign-file" sha1 "$KDIR/certs/signing_key.pem" "$KDIR/certs/signing_key.x509" pango_pcie_hdmi_frame_test.ko
modinfo -F signer pango_pcie_hdmi_frame_test.ko
```

`grep` 必须显示重复采集接口的日志行。新 `.ko` 编译成功后再签名，`signer`
应非空。`frame_to_png.py` 是 `verify_frames.py` 的依赖，需要放在同一目录。
脚本只需要 Python 3 标准库，无需安装 Pillow。

### 2. 更新 RK3568 驱动

保持 HDMI 输入稳定，检查现有模块。上一阶段的帧模块仍在时先卸载；列表中其它
匹配同一设备的测试模块也要先卸载。模块未加载时跳过对应的 `rmmod`。

```bash
sudo "$HDC" list targets
sudo "$HDC" -t "$TARGET" shell lsmod | grep pango_pcie
sudo "$HDC" -t "$TARGET" shell rmmod pango_pcie_hdmi_frame_test
sudo "$HDC" -t "$TARGET" file send "$HOME/pcie_host/pango_pcie_hdmi_frame_test.ko" /data/local/tmp/pango_pcie_hdmi_frame_test.ko
sudo "$HDC" -t "$TARGET" file send "$HOME/pcie_host/capture_many.sh" /data/local/tmp/capture_many.sh
sudo "$HDC" -t "$TARGET" shell insmod /data/local/tmp/pango_pcie_hdmi_frame_test.ko
sudo "$HDC" -t "$TARGET" shell dmesg | grep -E 'pango_pcie_hdmi_frame_test (0002:21:00\.0:|.*probe)|pango_pcie_hdmi_frame_test: probe' | tail -n 20
sudo "$HDC" -t "$TARGET" shell ls -l /dev/pango_pcie_frame_0002:21:00.0
```

需要同时看到 `HDMI frame DMA passed`、`HDMI frame image ready` 和：

```text
HDMI repeat capture ready: /dev/pango_pcie_frame_0002:21:00.0
```

设备节点应为字符设备（`ls -l` 的首字符为 `c`）。若日志显示注册成功但节点不存在，
先检查板上 `/sys/class/misc/pango_pcie_frame_0002:21:00.0/dev`；需进一步核对板上的
设备节点管理。未确认节点前不进入批量采集。

### 3. 先验证一次读取，再采集 30 帧

使用同一个 Ubuntu 终端，保留这些变量。每次新测试重新生成 `RUN_TAG`，避免覆盖
上次文件。脚本拒绝覆盖已有帧文件，失败时保留已保存的文件供诊断。

```bash
RUN_TAG="run_$(date +%Y%m%d_%H%M%S)"
REMOTE_DIR="/data/local/tmp/pcie_frames_$RUN_TAG"
LOCAL_DIR="$HOME/pcie_host/pcie_frames_$RUN_TAG"

sudo "$HDC" -t "$TARGET" shell sh /data/local/tmp/capture_many.sh 1 0 "${REMOTE_DIR}_check"
```

看到 `Capture batch completed: 1 frames` 后，将 HDMI 输入切换为明显变化的画面，
例如带秒数的时钟、移动窗口或物体运动，再执行：

```bash
sudo "$HDC" -t "$TARGET" shell sh /data/local/tmp/capture_many.sh 30 1 "$REMOTE_DIR"
sudo "$HDC" -t "$TARGET" shell dmesg | grep 'HDMI frame DMA passed' | tail -n 32
```

预期最后出现 `Capture batch completed: 30 frames`。每帧为 2,073,617 字节，30 帧
约需 62.2 MB 板端存储。间隔 1 秒表示前一帧保存完成后等待 1 秒再采下一帧，不表示
每秒采一帧。加载时先采一帧，再执行一次检查，然后运行批次，在期间没有其它采集时，
批次文件 `frame_000001.pgm` 对应驱动 `frame_seq=3`，最后一个对应 `frame_seq=32`。

### 4. 下载、检查和查看

逐个下载文件到明确目录，避免目录嵌套复制：

```bash
mkdir -p "$LOCAL_DIR"
for frame_index in $(seq 1 30); do
    frame_name=$(printf 'frame_%06d.pgm' "$frame_index")
    sudo "$HDC" -t "$TARGET" file recv "$REMOTE_DIR/$frame_name" "$LOCAL_DIR/$frame_name" || break
done

sudo "$HDC" -t "$TARGET" shell dmesg | grep 'HDMI frame DMA passed' | tail -n 32 > "$LOCAL_DIR/dma_pass.log"
python3 "$HOME/pcie_host/verify_frames.py" "$LOCAL_DIR" --expected 30 --png-dir "$LOCAL_DIR/png"
xdg-open "$LOCAL_DIR/png/frame_000001.png"
xdg-open "$LOCAL_DIR/png/frame_000030.png"
```

检查工具必须打印 `文件检查通过：frames=30`，并为每一帧打印像素校验值。
`distinct_checksums` 是不同校验值的数量，静态输入允许为 1；动态输入应进一步核对
多张 PNG 是否随输入更新。将文件校验值与对应 `frame_seq` 日志比较；当前工具不自动
关联日志，文件名是本批次序号。校验值变化不能单独证明画面正确或 HDMI 帧完整。

Ubuntu 无图形桌面时，可以复制首尾 PNG 到共享目录，在 Windows 打开：

```bash
cp -v "$LOCAL_DIR/png/frame_000001.png" "/mnt/hgfs/pcie_host/${RUN_TAG}_first.png"
cp -v "$LOCAL_DIR/png/frame_000030.png" "/mnt/hgfs/pcie_host/${RUN_TAG}_last.png"
```

### 5. 验收与下一阶段

| 检查项 | 通过标准 |
|---|---|
| 重复启动 | 30 次采集都在同一次驱动绑定内成功完成，无超时或帧桥错误 |
| 文件完整性 | 连续编号 1–30，每个 PGM 均为 2,073,617 字节，格式和像素长度检查通过 |
| 数据对应 | 每帧校验值与对应采集日志一致；`frame_seq` 连续增加 |
| 内容更新 | 动态 HDMI 输入下，导出的多张图像能对应输入变化，没有明显错行、重复块或撕裂 |
| 重复执行 | 重新生成 `RUN_TAG` 后可再次完成整个批次，无需卸载成功运行的驱动 |

请保存批次完成输出、`dma_pass.log`、文件检查输出和代表性 PNG。若某次读取失败，
获取完整驱动日志，保留失败文件；DMA 超时后按复位步骤恢复，不继续同一批次。

```bash
sudo "$HDC" -t "$TARGET" shell dmesg | grep -E 'pango_pcie_hdmi_frame_test (0002:21:00\.0:|.*probe)|pango_pcie_hdmi_frame_test: probe' | tail -n 120
```

本轮本地验证了 POSIX shell 语法、合成帧的批量检查和 PNG 像素、缺失/额外/损坏文件
的拒绝、模拟采集及禁止覆盖旧文件。Windows 环境没有运行 RK3568 内核模块，新驱动的
编译、设备节点创建及多帧 DMA 仍须按上述步骤在 Ubuntu/RK3568 验收。

4C-1 已通过后，当前进入 4C-2-阶段一：增加主机侧 4 槽环形缓冲和连续读取接口。
下一轮再增加 FPGA 源帧计数、帧边界同步、持续 DMA、用户空间缓冲区所有权和丢帧统计，
再测量持续采集的实际帧率及延迟。阶段 5 接入识别程序时，
需要明确模型的尺寸、色彩格式和处理硬件；RK3568 常见推理路径是 RKNN/NPU，GPU 路径
需根据实际模型及可用运行时确定。

## 阶段 4C-2-阶段一：连续采集环

本阶段仍沿用当前 FPGA 位流，只修改 RK3568 驱动。新增设备：

```text
/dev/pango_pcie_stream_0002:21:00.0
```

打开该设备后，内核线程连续启动 DMA；驱动内部保留 4 个 1920×1080 coherent DMA
槽位。用户空间每读取一次，就得到一个完整的带头记录：

| 字段 | 长度 | 含义 |
|---|---:|---|
| `magic` | 4 | `0x50475246`，ASCII 为 `PGRF` |
| `version` | 2 | 当前为 1 |
| `header_bytes` | 2 | 当前为 40 |
| `width/height` | 8 | 1920×1080 |
| `pixel_bytes` | 4 | 2,073,600 |
| `checksum` | 4 | 像素 FNV-1a 校验 |
| `sequence` | 8 | 驱动内成功采集序号 |
| `timestamp_ns` | 8 | 内核单调时钟时间戳 |
| 像素 | 2,073,600 | 8 位灰度数据 |

每个记录共 `2073640` 字节。驱动支持任意大小的用户空间 `read`，即使 `cat` 将一条
记录拆成多次小读取，也会保持记录头和像素属于同一槽位；`capture_stream.py` 会重新
拼接这些读取。4 个槽位满后，生产线程会等待用户空间读走槽位，
因此本阶段会施加反压，不会静默覆盖未读取的帧。`sequence` 连续只能说明驱动没有
丢弃已完成的环槽，不能证明 FPGA 没有跳过 HDMI 源帧；真正的源帧计数放在下一阶段。

### Ubuntu 编译和加载

在 Windows 共享目录同步本轮文件时，增加 `capture_stream.py`：

```bash
cp -v /mnt/hgfs/pcie_host/Makefile \
  /mnt/hgfs/pcie_host/pango_pcie_hdmi_frame_test.c \
  /mnt/hgfs/pcie_host/capture_stream.py \
  /mnt/hgfs/pcie_host/frame_to_png.py \
  /mnt/hgfs/pcie_host/frame_format.py \
  "$HOME/pcie_host/"
cd "$HOME/pcie_host"
KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10"
LLVM_DIR="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin"
make KDIR="$KDIR" LLVM_DIR="$LLVM_DIR"
"$KDIR/scripts/sign-file" sha1 "$KDIR/certs/signing_key.pem" \
  "$KDIR/certs/signing_key.x509" pango_pcie_hdmi_frame_test.ko
modinfo -F signer pango_pcie_hdmi_frame_test.ko
```

发送并加载后检查：

```bash
sudo "$HDC" -t "$TARGET" shell rmmod pango_pcie_hdmi_frame_test
sudo "$HDC" -t "$TARGET" file send "$HOME/pcie_host/pango_pcie_hdmi_frame_test.ko" /data/local/tmp/pango_pcie_hdmi_frame_test.ko
sudo "$HDC" -t "$TARGET" shell insmod /data/local/tmp/pango_pcie_hdmi_frame_test.ko
sudo "$HDC" -t "$TARGET" shell dmesg | grep -E 'HDMI (repeat|stream) capture ready|HDMI frame DMA passed' | tail -n 5
sudo "$HDC" -t "$TARGET" shell ls -l /dev/pango_pcie_stream_0002:21:00.0
```

需要看到 `HDMI stream capture ready`，并确认新节点首字符为 `c`。

### 连续采集验证

先在 Ubuntu 本地准备脚本：

```bash
cp -v /mnt/hgfs/pcie_host/{capture_stream.py,frame_to_png.py,frame_format.py} "$HOME/pcie_host/"
```

再发送到板端（脚本只在 Ubuntu 上运行，Python 读取 HDC 不适用）：

```bash
sudo "$HDC" -t "$TARGET" file send \
  "$HOME/pcie_host/capture_stream.py" /data/local/tmp/capture_stream.py
sudo "$HDC" -t "$TARGET" file send \
  "$HOME/pcie_host/frame_format.py" /data/local/tmp/frame_format.py
```

`HDC shell` 是文本通道，不能可靠转发包含 `0x00` 的二进制流；不要把流设备直接
接到 `HDC shell cat` 的标准输出。应先让 RK3568 将完整记录保存到普通文件，再用
`HDC file recv` 下载。`capture_stream.py` 可以从 Ubuntu 上的普通二进制文件解析
一条或多条连续记录。

先在板端保存一条完整记录：

```bash
REMOTE_STREAM_DIR="/data/local/tmp/stream_probe_$(date +%Y%m%d_%H%M%S)"
sudo "$HDC" -t "$TARGET" shell \
  "mkdir -p '$REMOTE_STREAM_DIR' && dd if=/dev/pango_pcie_stream_0002:21:00.0 of='$REMOTE_STREAM_DIR/record_000001.bin' bs=2073640 count=1 2>/dev/null"
```

下载并确认二进制文件大小：

```bash
LOCAL_STREAM_RAW="$HOME/pcie_host/stream_probe_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOCAL_STREAM_RAW"
sudo "$HDC" -t "$TARGET" file recv \
  "$REMOTE_STREAM_DIR/record_000001.bin" \
  "$LOCAL_STREAM_RAW/record_000001.bin"
wc -c "$LOCAL_STREAM_RAW/record_000001.bin"
```

`wc -c` 必须显示 `2073640`。用新的本地输出目录解析这一条记录：

```bash
STREAM_LOCAL_DIR="$HOME/pcie_host/stream_check_$(date +%Y%m%d_%H%M%S)"
python3 "$HOME/pcie_host/capture_stream.py" \
  "$LOCAL_STREAM_RAW/record_000001.bin" \
  "$STREAM_LOCAL_DIR" --count 1
python3 "$HOME/pcie_host/verify_frames.py" \
  "$STREAM_LOCAL_DIR" --expected 1 --png-dir "$STREAM_LOCAL_DIR/png"
```

需要看到 `Stream capture completed: frames=1 dropped_sequences=0`。确认单条记录成功后，
再批量保存 5 条记录：

```bash
REMOTE_STREAM_DIR="/data/local/tmp/stream_run_$(date +%Y%m%d_%H%M%S)"
sudo "$HDC" -t "$TARGET" shell \
  "mkdir -p '$REMOTE_STREAM_DIR'; i=1; while [ \$i -le 5 ]; do name=\$(printf 'record_%06d.bin' \"\$i\"); dd if=/dev/pango_pcie_stream_0002:21:00.0 of='$REMOTE_STREAM_DIR'/\$name bs=2073640 count=1 2>/dev/null || exit 1; i=\$((i+1)); done"

LOCAL_STREAM_RAW="$HOME/pcie_host/stream_run_raw_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOCAL_STREAM_RAW"
for frame_index in $(seq 1 5); do
    record_name=$(printf 'record_%06d.bin' "$frame_index")
    sudo "$HDC" -t "$TARGET" file recv \
      "$REMOTE_STREAM_DIR/$record_name" \
      "$LOCAL_STREAM_RAW/$record_name" || exit 1
done
cat "$LOCAL_STREAM_RAW"/record_*.bin > "$LOCAL_STREAM_RAW/records.bin"

STREAM_LOCAL_DIR="$HOME/pcie_host/stream_run_$(date +%Y%m%d_%H%M%S)"
python3 "$HOME/pcie_host/capture_stream.py" \
  "$LOCAL_STREAM_RAW/records.bin" "$STREAM_LOCAL_DIR" --count 5
python3 "$HOME/pcie_host/verify_frames.py" \
  "$STREAM_LOCAL_DIR" --expected 5 --png-dir "$STREAM_LOCAL_DIR/png"
```

预期最后显示 `Stream capture completed: frames=5 dropped_sequences=0`。如果板端
确认存在 Python 3，也可以在板端直接运行下面的备用方式；仍然不要使用 `HDC shell`
把二进制流输出到 Ubuntu：

```bash
sudo "$HDC" -t "$TARGET" shell python3 /data/local/tmp/capture_stream.py \
  /dev/pango_pcie_stream_0002:21:00.0 /data/local/tmp/stream_run1 --count 30
```

板端 Python 方式会在板端直接生成 PGM 和 `frames.csv`，完成后再使用 `file recv`
下载这些普通文件。采集完成后使用已有 `verify_frames.py` 检查像素，再比较
`frames.csv` 中的序号和 checksum。

本阶段验收标准：连续读取 30 帧均得到完整记录；每张 PGM 为 2,073,617 字节；CSV
中的 `sequence` 严格递增；驱动没有 DMA 超时；动态 HDMI 输入下 PNG 内容随输入变化。
这仍是主机侧环形缓冲验证，不代表 HDMI 源帧无丢失，也不代表已达到实时识别输入。

## 阶段 4D：通过以太网直连预览

### 连接方式与前提

RK3568 的以太网口可以用网线直接连接电脑有线网口，Ubuntu 虚拟机通过桥接网卡
访问板端 TCP 服务。现有 `pcie_stream_server` 支持 `--bind`，`preview_stream.py`
支持 `--host`；无需修改 FPGA 位流、PCIe 驱动或预览程序代码。

```text
HDMI → FPGA DDR → PCIe DMA → RK3568 pcie_stream_server
                                          ↓
                                  以太网 TCP :9000
                                          ↓
                            电脑有线网口 → VMware 桥接
                                          ↓
                              Ubuntu preview_stream.py
```

| 项目 | 要求 |
| --- | --- |
| 物理连接 | RK3568 以太网口通过网线直连电脑有线网口 |
| 网络速度 | 建议两端协商到 `1000 Mb/s`；1920×1080 GRAY8、约 18 fps 需要约 37.3 MB/s，即 299 Mb/s |
| VMware | 将虚拟网卡配置为桥接，明确选择连接板子的电脑有线网卡；勾选已连接及启动时连接 |
| IP 地址 | 板端、Windows 有线网卡及 Ubuntu 桥接网卡位于同一子网，地址不能重复 |
| HDC | 暂时只用于发送文件和启动板端程序；图像传输不使用 USB，也不执行 `fport` |
| 板端状态 | 已加载通过验证的 `.ko`，stream 节点存在，HDMI 输入画面有效 |
| 实机状态 | 此连接方式尚待验证；千兆标称带宽不能代替实际帧率和稳定性测量 |

### 1. 配置直连 IP 和 VMware 桥接

直连时通常没有 DHCP 服务器，需要配置静态地址。以下地址只是示例；如果与现有
网络冲突，选用其他独立网段，并对应调整后续命令。

| 设备 | 示例 IPv4 | 子网掩码 |
| --- | --- | --- |
| Windows 电脑有线网卡 | `192.168.50.10` | `255.255.255.0` |
| RK3568 有线网卡 | `192.168.50.20` | `255.255.255.0` |
| Ubuntu 虚拟机桥接网卡 | `192.168.50.30` | `255.255.255.0` |

在 Windows 有线网卡 IPv4 属性中填写示例地址；在 VMware 虚拟网络设置中明确将
桥接网卡绑定到这张有线网卡。Ubuntu 的有线网络 IPv4 设置选择手动，填写
`192.168.50.30/24`。这条本地直连网络不需要默认网关或 DNS；保留其他网卡已有的
互联网连接配置。

在 Ubuntu 的控制终端设置 HDC，查看板端网卡名称：

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
sudo "$HDC" list targets
sudo "$HDC" -t "$TARGET" shell ip link show
sudo "$HDC" -t "$TARGET" shell ip -4 addr show
```

从输出中确认插入网线的有线网卡名称，再配置板端。不要把 `lo` 或 Wi-Fi 网卡当作
有线接口；也不要直接假定名称一定是 `eth0`。

```bash
read -r -p "请输入 RK3568 的有线网卡名称：" BOARD_ETH
BOARD_IP="192.168.50.20"
sudo "$HDC" -t "$TARGET" shell ip link set dev "$BOARD_ETH" up
sudo "$HDC" -t "$TARGET" shell ip addr add "$BOARD_IP/24" dev "$BOARD_ETH"
sudo "$HDC" -t "$TARGET" shell ip -4 addr show dev "$BOARD_ETH"
sudo "$HDC" -t "$TARGET" shell cat "/sys/class/net/$BOARD_ETH/speed"
```

如果地址已经存在，跳过 `ip addr add`，不要重复添加。这个板端地址配置是临时的，
重启后需重新检查；系统网络服务也可能管理该接口。`speed` 输出 `1000` 表示当前
协商为千兆；`100` 表示百兆，无法满足当前约 18 fps 原始图像传输的数据量。

在 Ubuntu 验证路由和可达性：

```bash
ip -4 addr show
ip route get "$BOARD_IP"
ping -c 4 "$BOARD_IP"
```

路由应使用桥接到电脑有线网卡的接口。如果不通，先检查 IP、网线和 VMware
桥接选择。连接路由器或交换机时，也可以沿用这个 TCP 方式，使用实际分配的板端 IP。

### 2. 终端 A：启动以太网发送程序

先关闭原来的 `preview_stream.py`，确认没有正在运行的 `pcie_rknn_probe` 或其他
stream 读取者。停止板端旧服务器后再启动新的网络监听：

```bash
sudo "$HDC" -t "$TARGET" shell \
    'if pidof pcie_stream_server >/dev/null; then killall pcie_stream_server; fi'
sudo "$HDC" -t "$TARGET" shell ls -l /dev/pango_pcie_stream_0002:21:00.0
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_stream_server --daemon --bind 0.0.0.0 --port 9000
sudo "$HDC" -t "$TARGET" shell pidof pcie_stream_server
sudo "$HDC" -t "$TARGET" shell tail -n 10 /data/local/tmp/pcie_preview_server.log
```

前提是 `/data/local/tmp/pcie_stream_server` 已发送到板端并有执行权限。日志中的
新启动信息应为 `PCIe preview v2 listening on 0.0.0.0:9000`。`0.0.0.0` 是板端监听
地址，Ubuntu 客户端使用板端实际 IP。

### 3. 终端 B：直接连接板端 IP 打开窗口

```bash
cd "$HOME/pcie_host"
BOARD_IP="192.168.50.20"
python3 -u preview_stream.py --host "$BOARD_IP" --port 9000
```

这里不执行 HDC `fport`，也不使用 `127.0.0.1:19000`。图像通过以太网直接传输，
板子和网络正常时不依赖 USB 调试连接持续在线。按 `S` 保存截图，按 `Q`、`Esc`
或 `Ctrl+C` 退出。先持续预览 5～10 分钟，核对 HDMI 内容、帧率和断线情况。

### 4. 切换到 RKNN 单帧识别

当前预览和 RKNN 探测程序不能同时读取同一个 stream 设备。确认目标图片在预览中
显示正确后，退出窗口，通过板端命令停止发送程序：

```bash
sudo "$HDC" -t "$TARGET" shell \
    'if pidof pcie_stream_server >/dev/null; then killall pcie_stream_server; fi'
sudo "$HDC" -t "$TARGET" shell pidof pcie_stream_server
```

确认没有服务器 PID 后，按 [RKNN.md](RKNN.md) 进行单帧识别或启动连续识别服务。
连续识别使用 `pcie_rknn_probe --serve --bind 0.0.0.0`，Ubuntu 窗口使用板端 IP
及 `--require-detections`，会自动叠加同帧的框、类别和置信度。连续识别窗口已通过
USB/HDC 实机功能验证；通过以太网传输这一识别数据的性能和稳定性仍待验收。
切回原始预览时，先停止识别服务，再执行本节的服务器启动和 Python 预览命令。
# 2026-10-06 预览 FPS 优化说明

本节记录此前的异步预览方案；目前默认模式和升级指令以“2026-10-07：同帧识别优先预览”为准。使用 `--preview-mode async` 才会发送较快的原始图像和独立检测记录。

本轮 `pcie_rknn_probe --serve` 已将原始 HDMI 帧、RKNN 推理和 TCP 发送解耦。窗口现在分别显示 `Raw`、`Receive`、`Display` 和 `Detection` FPS：原始画面可以在不等待每次推理的情况下更新，检测框保留最近一次结果并显示对应的 `detection seq`。`cv2.waitKey(1)` 只减少窗口等待，不会消除板端采集、整帧传输或 RKNN 推理耗时。

当前 YOLOv5s 单帧推理约 136～188 ms，检测框稳定 30 FPS 需要把端到端周期降到 33.3 ms 以内，暂不能直接承诺。新版程序和协议必须在 RK3568 实机上重新编译、部署后测量；USB/HDC 传输仍可能限制原始画面 FPS。

### 推理耗时与运动框优化

板端新版程序在 `rknn_outputs_get` 中保留模型的量化输出，不再要求每个输出张量先转换为 FP32；YOLO 解码使用输出张量的量化参数还原候选值，并在目标置信度不足时跳过该网格的 80 类扫描。旧版 `--profile` 的检测结果和坐标仍需在 RK3568 上对照确认，不能只根据交叉编译通过就判断加速有效。

预览端加入轻量运动跟踪：检测结果按类别和 IoU 匹配，使用相邻检测框的速度在两次 NPU 推理之间预测位置。默认保留 350 ms，窗口会继续显示 `detection seq` 和 `delta`，因此框的连续移动不等于模型每一帧都重新推理。需要更保守时可缩短保留时间：

```bash
python3 -u preview_stream.py --host 127.0.0.1 --port 9001 \
    --require-detections --track-max-age-ms 200 --profile
```

当前服务重新部署后，先观察板端日志中的 `outputs_ms`、`postprocess_ms` 和 `total_ms`，再观察窗口的 `Detection FPS`。如果量化输出在实机上出现坐标或置信度异常，应回退到旧版 `want_float=1`，优先保留检测正确性。

兼容性对照可以在板端服务命令末尾增加 `--float-output`。默认不加该选项以使用量化输出；加上后只用于对比性能和检测结果，不代表优化版已经失效。

### 检测框闪烁修复

版本 3 的原始帧头部也带有检测扩展字段，但其中的空框数组不代表模型执行了一次空检测。接收端现在按记录类型处理：原始帧只刷新图像；检测记录才刷新框和 `Detection` FPS；版本 2 同帧记录即使目标数为零也属于真实检测结果。后续原始帧保留最近一次检测结果，真正的零目标检测仍清除框；重连时清除旧连接的检测结果。

如果已部署并行版板端服务，只需在 Ubuntu 关闭预览窗口、复制修复后的 `preview_stream.py` 并重新启动，无需重新编译板端程序或修改模型：

```bash
cp -v /mnt/hgfs/pcie_host/preview_stream.py "$HOME/pcie_host/preview_stream.py"
cd "$HOME/pcie_host"
python3 -u preview_stream.py --host 127.0.0.1 --port 9001 \
    --require-detections --profile 2>&1 | tee rknn_preview_fixed.log
```

`--profile` 对每次真实检测输出 `Detection result: seq=... targets=... max_score=... infer_ms=...`，可用 `grep 'Detection result:' rknn_preview_fixed.log` 查看。原始帧不产生这类日志。若修复后静止画面仍出现连续的 `targets=0`，再检查模型输入、置信度和灰度场景适配；在此之前，窗口闪框不能用于判断模型准确率。原始画面快于检测时，运动目标的框仍可能滞后，`image seq`、`detection seq` 和 `delta` 用于观察两者的时间差。
