# HDMI 真实彩色输入、RKNN 识别与同帧预览

2026-11-01 PIE v2板端实验接入：geometry模型导出为FP32纯C参数，在新`rk_front_pie_control`中执行行人关联、六次160～240ms真实采样及0.6／1秒分数计算；Ubuntu只绘制既有风格的实验卡片。无线上动作门控，固定unverified/no_control，不触发驾驶规则。64个C／ONNX案例最大误差1.79e-7，核心、真实C进程UDP/HTTP、7项新预览与11项实时道路回归通过，ARM64严格编译完成；Ubuntu脚本和RK3568实机运行待执行，不能以本地回归代替。整合入口`sudo -v && bash /mnt/hgfs/pcie_host/integrate_pie_anticipation_rk3568.sh`，预览`preview_pie_anticipation.sh`，保留旧P1回退；详见 [PIE_ANTICIPATION_RK3568.md](PIE_ANTICIPATION_RK3568.md)。

2026-11-01 PIE 提前横穿第二轮训练：重新构造首次官方横穿前的连续历史队列，完成 geometry／视觉线性／GRU 三候选训练，以验证集自然比例 BCE 选中 geometry 第96轮，导出独立 FP32 ONNX。相同严格开发队列、相同验证误报预算下，1秒提醒精确率23.88%→46.75%，误报窗口102→41、错误启动20→8；有效窗内新启动提醒仍7／34，另1条仅为过早提示延续，仍漏多数起点。set02已参与开发，不称独立新测试。22项回归及3,251行导出模型复算通过，11案例图册含命中、漏报与误报；原预演仍用v1，未替换板端／FPGA／原检测权重。结果、接口合同与后续限制见 [PIE_ANTICIPATION_20261101.md](PIE_ANTICIPATION_20261101.md)。

2026-11-01 PIE 时序训练与行动预演：新增 set01／set02 共 7 视频、215 条行人轨迹已配对，形成 10,497 个因果窗口；冻结预训练视觉骨干，完成轻量 GRU 行人未来1秒分类、独立 set02 评估及 FP32 ONNX 导出。提前横穿子集误报较多，分数仅实验展示，不触发停车。新增 Windows 本机视频播放器，冻结当前画面后同窗选择左／直／右、播放3秒相对2.5D、比较模拟等待与继续；9项回归、六场景18路线浏览器检查及真实冻结→ONNX→预演联调通过。真实路口几何及反事实轨迹尚未完成，未替换RK3568／FPGA／原模型。操作和真实指标见 [PIE_REHEARSAL_20261101.md](PIE_REHEARSAL_20261101.md)。

2026-11-01 过街关系完善：新增局部过街区域组织、足点误差范围、背景运动补偿后的短时横向证据，以及原车道双侧曲线共同可见范围内的本车通道关联。10 项测试、7 视频／10 片段／865 帧回放及 H.264 解码核验完成；Waymo 横穿由无脚点重合候选变为部分时刻可提示关注，仍有延迟与间断。路口路径常未知，实片尚未验收“本车冲突／明确侧方”分支，未接入自动停车。正式图册、复现和逐项限制见 [CROSSWALK_RELATIONS_20261101.md](CROSSWALK_RELATIONS_20261101.md)。复用已有模型，未修改在线预览、RK3568 服务或 FPGA。

2026-11-01 连续验证：独立斑马线模型完成 7 个视频、10 个片段、173 秒／865 次 5Hz 推理的 Windows 离线对比；加入按视频的保守车内排除、运动对齐、两次确认、400ms不确定结果显示保持及明确背景清除，并复用现有 YOLO／跟踪／相对 2.5D。发现并修正过宽底部裁剪和明显保持残影；7 项回归、865 帧 H.264 逐帧解码及模型哈希核验通过。Waymo 明显过街仍会漏掉脚点重合，PIE 侧方重合不代表本车冲突，自动礼让尚未验收。本轮不更换运行预览或部署板端。图册、复现与限制见 [CROSSWALK_VALIDATION_20261101.md](CROSSWALK_VALIDATION_20261101.md)。

2026-11-01 斑马线首轮训练完成：Mapillary Vistas v2.0 的 18,000/2,000 份 train/val 标签已核对；从训练集精选 2,400 张微调独立 LR-ASPP 二分类模型，保留完整 2,000 张验证图。Windows RTX 4060 完成 12 轮，最佳第 11 轮像素 IoU 58.57%、Precision 80.08%、Recall 68.56%。已导出 FP32 ONNX 和完整接口 manifest，6 个样例 PyTorch/ORT 分类图一致；8 段本地/Waymo/PIE 视频共 59 帧定性复核，发现车头/仪表台误检及远处小目标漏检。PIE 新增 video_0002 按 set01 核对并目视配对，含 18 条 crossing=1 轨迹，作为后续行为开发素材，未混作分割像素真值。详见 [CROSSWALK_TRAINING_20261101.md](CROSSWALK_TRAINING_20261101.md)。原车道权重哈希不变；尚未接入预览、转换 RKNN 或部署板端。

2026-10-31 PIE补充筛查：`F:\waymo_data\pie` 的视频经用户确认属于set01；10分钟、1080p、30FPS与XML内部名称及规格配对，21条行人轨迹、6条斑马线框轨迹，抽查51帧。当前已标注轨迹无横穿本车路径正例，适合误报警／遮挡开发；本地其他标注提示优先补set01/video_0002（18条正例轨迹）。已有53对标注不代表已有53段视频；未标注帧不作负样本。结果见[PIE_AUDIT_20261031.md](PIE_AUDIT_20261031.md)，未训练或修改实机程序。

2026-10-31 数据筛查：已核对本地 BDD 10K 图片与 100K 标注，得到 3,430 个 train/val 配对、1,271 张斑马线路径候选及 780 张斑马线／行人共现；原标签不是完整过街区域。已筛查 7 段前视视频、抽查 OmniTraffic，整理重点过街／遮挡／误报片段、300＋80 张首批待补标清单和图册。详见 [DATASET_AUDIT_20261031.md](DATASET_AUDIT_20261031.md)。本轮未训练、未修改运行代码、未部署板端。

2026-10-31 实时2.5D兼容修复：Ubuntu整合测试在背景网格绘制处报告`integer argument expected, got float`，尚未进入安装／启动步骤。已将实时道路、路口投影及标定坐标统一输出Python原生整数，并只对有效投影点做范围比较以消除旧NumPy的NaN比较警告；新增旧版round行为与严格OpenCV参数边界回归。实时映射11项本地测试通过，Ubuntu重跑`integrate_front_p1_rk3568.sh`验证；无需更换Python、OpenCV或模型。

2026-10-31 实时2.5D：新增本车固定的真实车道曲线、车辆／行人映射，FWM1轻量板端感知接口，Ubuntu预览侧跟踪、位置平滑和限时淡化保持；默认相对示意，支持U选视频区域、H四点地面标定、O恢复默认。V大画面、M原路口演示，模型/A1/FPGA及驾驶许可规则沿用原实现。C协议、真实HTTP/UDP、跟踪/时效/界面回归和ARM64编译通过；本地真实视频验证与实机验收范围见[LIVE_ROAD_RK3568.md](LIVE_ROAD_RK3568.md)。更新仍运行`integrate_front_p1_rk3568.sh`，预览`preview_front_p1.sh`。

2026-10-30 路口交互：沿用Claude界面v3视觉，新增同窗实景/2.5D路口模式；M切换，J/I/L选路线，G开始/继续，B暂停，R重置，F红/黄灯违规演示。RK3568 C控制器管理位置、停止线许可、通过路口和违规事件，Ubuntu只绘制与输入。已进入路口后完成路线，断流暂停且需明确恢复。模型/A1/FPGA未改。本地C规则、真实HTTP/UDP、显示坐标/时效/字体、12种渲染、20秒离线动画和ARM64编译通过；实机更新后效果待验收。更新仍运行`integrate_front_p1_rk3568.sh`，操作及Windows离线预览见[JUNCTION_SCENE_RK3568.md](JUNCTION_SCENE_RK3568.md)。

2026-10-30 P1 v2：修正“有LIGHT但选定灯未知”及下方面板跳变。默认自动显示灯色，直接点选指定灯并跟随，不再30秒固定解除；接受750ms内目标结果，颜色短时保持与模拟通行有效期分开。夜间大光晕及过曝白芯规则仅用于P1，5个Waymo主灯ROI颜色样本由3/5变为5/5（不是检测召回率）。板端按每侧最后确认结果保持线型1.5秒，重复帧/未知不续期，新线型覆盖、断流清除；面板与请求反馈统一显示“保持”。更新仍运行`integrate_front_p1_rk3568.sh`，详见[FRONT_P1_RK3568.md](FRONT_P1_RK3568.md)。本地规则、HTTP/UDP和ARM64编译通过，实机更新后效果待验收。

2026-10-30：新增独立HDMI单路 `front_p1`，按用户确认采用手动选择路口方向和对应交通灯、板端实时灯色驱动模拟红停绿行、屏幕方向箭头/虚拟LED、实线变道请求报警。复用现有模型及A1，暂不做P0或多路同步。源图推理、缩小后叠加、C校验与5Hz面板缓存；保留`scene_v4 + A1`回退。规则、真实HTTP/UDP联调、预览回归和ARM64交叉编译通过，板端部署与流畅度仍待验收。操作、限制与两条启动命令见[FRONT_P1_RK3568.md](FRONT_P1_RK3568.md)。

2026-10-28：用户录屏出现车道STALE及828～902ms结果年龄。已修正v6车道与三路YOLO的NPU仲裁及最新帧选择时机，保留模型和原时效；预览后台轮询100ms，已知超时显示“已过期”。竞争、最新帧、重播/停止取消和引擎错误释放本地测试、ARM64编译通过；更新命令和实机验证范围见[LANE_SCHEDULING_FIX.md](LANE_SCHEDULING_FIX.md)。此为计算调度修正，不能把本地通过视为实机跳变已消除。

2026-10-28：新增同窗中文驾驶辅助预览，上方三路识别画面、下方状态/反馈/按钮，入口`preview_drive_assist.sh`。显示端自动同步Ubuntu本地副本，复用C校验加速；控制HTTP独立线程、断流清理显示、8项预览测试、真实C控制器联调及合成占位画面的中文布局渲染通过。模型及板端状态机不变，Ubuntu实机显示与流畅度待验收。换视频命令和流程见[UNIFIED_DRIVE_PREVIEW.md](UNIFIED_DRIVE_PREVIEW.md)。

2026-10-28：用户已确认同步v5三路输出正常。新增辅助驾驶v6：一套YOLO上下文服务前/左/右，侧方640×480使用pad80独立还原坐标；本轮按用户要求不做区域标定。RK3568 C控制器接收分路有时效的感知报告，提供中文请求反馈、实线报警和模拟车况，仍不由零检测或绿灯推导安全通行。代码、核心/线程/HTTP/页面脚本测试、ARM64交叉编译已完成；v6板端效果与性能待验收，视觉工具中止未验收页面显示。部署及回退见[DRIVE_ASSIST_RK3568.md](DRIVE_ASSIST_RK3568.md)。

2026-10-28：再次启动仍报告9010/9013被占且argv扫描未识别进程。新增实际exe/可执行映射身份核对与socket inode→PID诊断，处理加载器启动或argv不匹配时漏识别；未知占用者只报告，不按端口杀进程。start在端口未释放时提前失败并保留视频服务；实机恢复仍待反馈，不能把旧脚本输出“stopped”视为端口已空闲。

2026-10-28：同步控制start的模型检查已在板端通过，启动反馈`Sync control port occupied`。已修复仅凭PID文件停止旧控制器的缺陷：扫描精确可执行参数、丢失/过期PID恢复、僵尸忽略，控制器就绪后才停止旧视频服务；start自动刷新脚本并在失败时输出9010/9013端口诊断。无模型/FPGA变更，直接重试start应用修复，真实重启结果待用户反馈。

2026-10-28：板端开始控制第一阶段见[SYNC_CONTROL_RK3568.md](SYNC_CONTROL_RK3568.md)。新增受控Windows双路录像源、Ubuntu USB/HDC中转、RK3568 C控制面板、RGB565机器时间码及6帧接收配对缓存；开始前等待首帧，重播隔离旧配对和旧模型结果。Windows本地端到端联调/线程重置回归及ARM64交叉编译通过，Ubuntu原生回归和真实视频链路待验收。复用现有模型和FPGA位流，三路预览仍显示最新画面，OV5640手动未确认，侧方融合决策未接入。

2026-10-28：新增Windows通用双路同步播放器，HDMI前视＋EVF1以太网右视共用源时间轴，支持任意本地视频、媒体PTS/独立CSV、不同帧率、暂停/重播/跳转及右侧偏移。OV5640按用户条件拍摄手机并手动对齐，标记manual_unverified。启动、限制和实机核对见[SYNC_HDMI_ETHERNET.md](SYNC_HDMI_ETHERNET.md)。本轮不改模型及板端服务，真实两路接收延迟尚待实测。

2026-10-26 双模型场景v4已实机运行，用户反馈主要剩余绿色箭头灯有LIGHT框但SIGNAL为UNKNOWN，录屏已确认。已修正框内绿色符号形状规则：允许填充率≥0.25，同时要求暗灯壳≥0.35，保留原HSV、面积/宽高比、三次确认和失效规则；红黄灯仍使用紧凑灯点标准。C/Python参考同步，灯色版本`yolo-box-hsv-green-symbols-c-v2`。原视频右侧灯7个RGB565人工ROI样本由5帧颜色证据提高至7帧；这是颜色模块回归，不是YOLO/全视频准确率。更新仍运行`integrate_hdmi_scene_rk3568.sh`，成功后打开`preview_hdmi_lane_fast.sh`，修正版实机效果待验证。

2026-10-26 HDMI场景v4已在Ubuntu通过车道/目标核心与异步线程测试、ARM64编译、C/Python预处理及灯色对照。部署停在板端toybox缺少`sha256sum`，未切换服务；已改为上传项目自带SHA256工具并用既有glibc加载器执行，再与Ubuntu接收文件对照，无需安装依赖。修复后重跑`sudo -v && bash /mnt/hgfs/pcie_host/integrate_hdmi_scene_rk3568.sh`，实机双模型检查与效果待验证。

2026-10-26 用户确认当前车道判定效果良好，下一步接回车辆、行人和红绿灯。已新增HDMI场景v4，复用原板端YOLOv5s与当前LR-ASPP，两模型独立最新帧队列、NPU提交串行，保持实时三路预览；摄像头/以太网不识别。车辆/行人仅检测展示，灯色仅YOLO框内颜色证据，不增加检测到行人即报警或转向许可。目标核心、预处理与灯色C/Python对照及ARM64编译已通过，Ubuntu线程测试、双模型实机检查和效果待执行。更新入口`sudo -v && bash /mnt/hgfs/pcie_host/integrate_hdmi_scene_rk3568.sh`，成功标志`SCENE_INTEGRATE_PASS`；完整说明见[HDMI_SCENE_RK3568.md](HDMI_SCENE_RK3568.md)。v4部署成功后重启使用`deploy_hdmi_lane_rk3568.sh start-scene`，保留车道v3回退入口`start-diag`。

2026-10-26 三路拼接中的 HDMI 车道模型迁移见 [HDMI_LANE_RK3568.md](HDMI_LANE_RK3568.md)。沿用 Windows 第24轮最佳 LR-ASPP 模型，仅处理 HDMI 源。FP16转换、身份校验和实机加载已通过；同步版约2.1FPS，异步v2录屏平均约11.2FPS。v3原因诊断和预处理缓存已在板端输出逐次日志，用户确认HDMI已全屏、UNKNOWN主要发生在路口/左转。最新连续32次诊断间隔150.05～401.16ms，全部reset=NONE；前15次左侧稳定输出SOLID，随后14次无有效曲线，再出现左侧候选并重新确认。与路口标线中断相符，本段没有500ms超时重置，不据此放宽曲线阈值或认定需要重训。最新11条视频周期样本平均81.76ms，3条识别耗时样本平均310.26ms；以太网无有效输入，性能变化不能归功于缓存。当前已部署版重启使用`deploy_hdmi_lane_rk3568.sh start-diag`，预览使用`bash /mnt/hgfs/pcie_host/preview_hdmi_lane_fast.sh`。下一验收重点为清晰直路的边界稳定性及转弯后恢复，路口无可靠标线时继续保持未知，不能据UNKNOWN推断路口或转向许可。

## 实现和验收状态

本次将 FPGA 原始 HDMI RGB 输入保存为 RGB565，经过 DDR/PCIe 送到 RK3568；板端将真实颜色展开为 RGB 三通道送入已经验证的 YOLOv5s INT8 模型。窗口继续使用 `matched` 模式，显示该次推理的彩色原图与检测框。

| 层次 | 数据与变化 |
| --- | --- |
| HDMI/DDR | `PCIE_RGB565=1`，默认开启；存储 `{R[7:3],G[7:2],B[7:3]}`，同步使用原始 HDMI VS/DE |
| PCIe | 1920×1080×2=4,147,200 字节，2025 个 2048 字节 DMA 块；每块读 DDR 64 拍 |
| 硬件元数据 | `signature=0x52473536`（RG56），`bytes=4147200`；旧灰度 FRME 仍兼容 |
| 驱动记录 | V1，40 字节小端头 + 4,147,200 字节 RGB565LE；每条记录 4,147,240 字节 |
| 模型输入 | RGB565LE → RGB888 → 等比缩放 640×360 → 上下各补 140 行，补边值 114 → NHWC UINT8 640×640×3 |
| 模型/输出 | `yolov5s-640-640.rknn`，既有 INT8 模型；保留 native INT8 输出和候选筛选 |
| TCP 识别记录 | V4，88 字节头，格式字段 2=RGB565LE；matched 原图 + 本帧框；零目标正常显示并清空框 |
| 预览 | 保留 USB/HDC :9001；Python 将 RGB565LE 转成 OpenCV BGR 显示，并保存彩色截图 |
| FPGA 本地 HDMI 输出 | raw/lowlight 模式保持 RGB；其他处理模式直接显示对应处理结果，避免把 RGB565 DDR 数据误显示为灰度 |

RGB565 的红/蓝为 5 位、绿色为 6 位；会降低颜色精度，但三通道是真实颜色，并非复制灰度。现有模型无需重新训练或转换。彩色输入能否提高具体场景的检出率，需要对同一场景测量，不能预先保证。

本地验证：灰度与 RGB565 分别完成两帧完整 DDR/DMA 仿真，覆盖地址步进、尾块、双 RAM 高低半拍选择、显示读仲裁及延迟末拍；ARM64 用户态程序严格编译通过；实际 C 彩色预处理与 Python 协议/绘图验证通过，包括 RGB 顺序、补边、空检测、旧协议兼容、损坏与截断拒绝。采集线程交接回归通过。

实机进展：用户已烧录彩色位流、重新构建加载驱动；probe 确认 `RGB565LE`，第一帧 `4147200` 字节 DMA 通过，stream 记录长度为 `4147240`。替换旧灰度 RKNN 可执行文件后，用户确认彩色连续识别与流畅度基本正常。2026-10-07 录屏仍显示原图横向循环错位，修复与验收见下节。

## 彩色画面横向跳动：FPGA 帧边界修复

录屏中的文字保持正常方向，但右侧内容绕回左侧，并出现行接缝；这是图像错位，不是水平镜像。录屏同时显示 `Mode matched`、图像序号等于检测序号、`delta=0.0 ms`、`reconnects 0`。当前 Python 转换没有翻转操作，已用左右不同色块验证解码方向。

旧帧缓存通过 `wr_en & ~wr_pause` 直接屏蔽 DE。DMA 的暂停和恢复发生在任意像素时钟位置，会截断有效行；释放暂停后，剩余半行可能被当成一条新行写入。旧缓存选择又每个 VS 都切换，包括跳过和未完整写入的 HDMI 帧。主机的 FNV 校验发生在采集后，能校验传输一致性，不能检测已经错位的采集图像。

| RTL | 本次修改 |
| --- | --- |
| `src/ddr/wr_buf.v` | 暂停/恢复只在 VS 边界生效；像素打包写使能同时受 DE 和帧使能控制；只在所有行 DDR 写完成后发布完整缓存，跳过的帧不切换已完成缓存 |
| `src/ddr/fram_buf.v` | DMA 就绪要求完整帧有效、写入已在帧边界暂停、DDR 写事务已排空；导出明确的完整帧选择和就绪信号 |
| `src/pcie/pcie_frame_dma_bridge.v` | 第一块读取前先发出暂停请求，再等待缓存锁定；整帧读取期间维持锁定 |
| `src/hdmi_loop.v` | 将新的帧锁定就绪信号连接到 PCIe 桥，替代仅表示曾经初始化完成的信号 |

本地回归包含真实 RGB565 像素打包/读出顺序、首帧尚未到来就请求采集、不同位置暂停、行中间释放暂停、连续跳过 HDMI 帧、1920 像素行宽与环形 RAM 回绕；灰度和 RGB565 的两帧完整 DDR/PCIe 回归也通过。新位流尚未在 RK3568 实机验收，不应将仿真结果视为现场跳动已消除。

### 本次更新步骤

1. 在 Windows 的 PDS 打开 `06_hdmi_loop_1/hdmi_loop.pds`，重新综合、布局布线、检查时序并生成位流；确认此次更新的四个 RTL 文件属于该工程。
2. 停止 Ubuntu 终端 B 的预览及板端识别服务，烧录新位流，重启 RK3568 重新枚举 PCIe。
3. 按第 3 节恢复已经验证的彩色驱动，再按第 4 节启动已验证的 `/data/local/tmp/pcie_rknn_probe_rgb565`，按第 5 节打开窗口。本次记录格式和 RKNN/Python 接口未变，无需为了帧边界修复重新编译它们。
4. 先用带文字、左右不同颜色的静止图案运行数分钟，确认左右位置、行接缝与完整原图稳定，再切换运动视频。必要时按 `S` 对比原始 `.png`。
5. 重采 `HDMI timing:`、`RKNN stream timing:` 和 `RKNN preview timing:`；新增的帧边界等待可能降低采集 FPS，先测量再继续优化。

回归入口为 HDMI 工程的 `ipcore/pcie_test/example_design/bench/run_hdmi_regression.ps1`；可通过 `-IverilogDirectory` 指定已有 Icarus Verilog 的 `bin` 目录。

## 1. Windows：生成并烧录新 FPGA 位流

1. 备份已验收的灰度位流，便于回退。
2. 打开 `06_hdmi_loop_1/hdmi_loop.pds`。更新的是此 HDMI 工程，不是独立 PCIe 测试工程。
3. 顶层 `src/hdmi_loop.v` 的 `PCIE_RGB565` 默认值为 `1`。确认工程没有覆盖成 `0`，使用当前 `src/pcie/pcie_frame_dma_bridge.v`。
4. 重新执行综合、布局布线、时序检查和位流生成，烧录本次新生成的位流。不能仅重新烧录目录里之前生成的旧 `.sbit`。
5. FPGA 正常启动并接上 1920×1080 HDMI 输入后，重启 RK3568，重新枚举 PCIe。

PDS 的时序和资源检查须通过；彩色桥使用两块并行 staging RAM，布局布线需重新完成。KEY1 仍切换本地显示处理模式；彩色 PCIe 采集始终使用原始 RGB。

## 2. Ubuntu：同步、编译和签名

以下命令在 Ubuntu 执行。共享目录需要已挂载：

```bash
sudo mkdir -p /mnt/hgfs
if ! mountpoint -q /mnt/hgfs; then
    sudo vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other
fi

mkdir -p "$HOME/pcie_host"
cp -v /mnt/hgfs/pcie_host/{Makefile,pango_pcie_hdmi_frame_test.c,pango_pcie_hdmi_status_test.c,pcie_rknn_probe.c,build_rknn_probe.sh,stop_rknn_server.sh,pcie_stream_server.c,build_preview.sh,preview_stream.py,rknn_stream.py,draw_detections.py,capture_stream.py,verify_frames.py,frame_to_png.py,frame_format.py,frame_checksum.c,build_checksum.sh,README.md,COLOR.md,RKNN.md} "$HOME/pcie_host/"
cp -v /mnt/hgfs/pcie_host/pango_pcie_*.c "$HOME/pcie_host/"
cd "$HOME/pcie_host"

bash build_checksum.sh
RKNN_RUNTIME="$HOME/rknn-yolov5-test/lib/librknnrt.so" \
    RKNN_PROBE_OUTPUT="$HOME/pcie_host/pcie_rknn_probe_rgb565" \
    bash build_rknn_probe.sh

export KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10"
export LLVM_DIR="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin"
grep -nE 'PANGO_RGB565_BYTES|PANGO_RGB565_SIGNATURE|HDMI pixel format' pango_pcie_hdmi_frame_test.c
make KDIR="$KDIR" LLVM_DIR="$LLVM_DIR" && \
    "$KDIR/scripts/sign-file" sha1 \
    "$KDIR/certs/signing_key.pem" \
    "$KDIR/certs/signing_key.x509" \
    pango_pcie_hdmi_frame_test.ko

modinfo -F vermagic pango_pcie_hdmi_frame_test.ko
modinfo -F signer pango_pcie_hdmi_frame_test.ko
strings pango_pcie_hdmi_frame_test.ko | grep -E 'HDMI pixel format:|RGB565LE'
file pcie_rknn_probe_rgb565
```

确认构建和签名成功再继续。`KDIR` 必须与板端实际运行的内核匹配。默认识别服务无需运行 `pcie_stream_server`；需要彩色纯预览时，必须用 `bash build_preview.sh` 重新编译该程序，不能继续发送旧二进制。

## 3. 终端 A：重启后加载驱动，先确认颜色

以下从 FPGA 新位流已启动、RK3568 已重启、其他采集程序未运行的状态开始：

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
cd "$HOME/pcie_host"
sudo "$HDC" list targets
sudo "$HDC" -t "$TARGET" shell uname -r
sudo "$HDC" -t "$TARGET" shell lspci -nn

sudo "$HDC" -t "$TARGET" file send \
    pango_pcie_hdmi_frame_test.ko /data/local/tmp/pango_pcie_hdmi_frame_test.ko
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pcie_rknn_probe_rgb565" /data/local/tmp/pcie_rknn_probe_rgb565
sudo "$HDC" -t "$TARGET" shell chmod 755 /data/local/tmp/pcie_rknn_probe_rgb565
sudo "$HDC" -t "$TARGET" shell insmod /data/local/tmp/pango_pcie_hdmi_frame_test.ko profile=1
sudo "$HDC" -t "$TARGET" shell dmesg | \
    grep -E 'HDMI frame capture:|HDMI pixel format:|HDMI stream capture ready:|metadata mismatch|probe.*failed' | tail -n 8
```

彩色必须看到：

```text
HDMI frame capture: bytes=4147200 signature=0x52473536
HDMI pixel format: RGB565LE
HDMI stream capture ready: ... slots=4 record_bytes=4147240
```

如果还是 `2073600 / 0x46524d45 / GRAY8`，说明 FPGA 仍在运行灰度位流；仅更新 C/Python 不能恢复原始颜色。发生 `metadata mismatch` 时检查位流和驱动的配对，先不启动识别服务。

### 彩色元数据正确，但仍报告 metadata mismatch

如果同一次 probe 打印 `bytes=4147200 signature=0x52473536` 后立即报告 `metadata mismatch`，说明 BAR0 已返回新彩色位流的正确元数据。当前源码的 RGB565 判断明确接受这组值；此时优先纠正 Ubuntu 源码、构建的 `.ko` 和板端已加载模块的版本，不需要先回退或重新烧录位流。

先退出预览客户端，并停止板端采集/识别服务。成功加载模块但 probe 失败时，模块仍可能出现在 `lsmod`；再次 `insmod` 会报 `File exists`。按以下步骤强制重建，并用新文件名部署，区分旧产物：

```bash
cd "$HOME/pcie_host"
cp -v /mnt/hgfs/pcie_host/Makefile "$HOME/pcie_host/"
cp -v /mnt/hgfs/pcie_host/pango_pcie_*.c "$HOME/pcie_host/"
cmp /mnt/hgfs/pcie_host/pango_pcie_hdmi_frame_test.c pango_pcie_hdmi_frame_test.c
grep -nE 'PANGO_RGB565_BYTES|PANGO_RGB565_SIGNATURE|HDMI pixel format' pango_pcie_hdmi_frame_test.c

export KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10"
export LLVM_DIR="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin"
make clean KDIR="$KDIR" LLVM_DIR="$LLVM_DIR" && \
    make KDIR="$KDIR" LLVM_DIR="$LLVM_DIR" && \
    "$KDIR/scripts/sign-file" sha1 \
    "$KDIR/certs/signing_key.pem" \
    "$KDIR/certs/signing_key.x509" pango_pcie_hdmi_frame_test.ko

strings pango_pcie_hdmi_frame_test.ko | grep -E 'HDMI pixel format:|RGB565LE'
modinfo -F vermagic pango_pcie_hdmi_frame_test.ko
modinfo -F signer pango_pcie_hdmi_frame_test.ko
sudo "$HDC" -t "$TARGET" shell lsmod | grep pango_pcie
```

确认构建成功、`strings` 中有 `HDMI pixel format: %s` 和 `RGB565LE`，且签名者非空。若源码没有 RGB565 判断，先纠正共享目录；若 `.ko` 没有彩色字符串，不要推送。`cmp` 无输出且退出码为 0 表示源码相同。

若模块列表仍有 `pango_pcie_hdmi_frame_test`，卸载后再部署。卸载报错时保留输出，不能通过忽略错误继续加载：

```bash
sudo "$HDC" -t "$TARGET" shell rmmod pango_pcie_hdmi_frame_test
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pango_pcie_hdmi_frame_test.ko" \
    /data/local/tmp/pango_pcie_hdmi_frame_rgb565.ko
sudo "$HDC" -t "$TARGET" file recv \
    /data/local/tmp/pango_pcie_hdmi_frame_rgb565.ko \
    "$HOME/pcie_host/pango_pcie_hdmi_frame_rgb565.received.ko"
cmp "$HOME/pcie_host/pango_pcie_hdmi_frame_test.ko" \
    "$HOME/pcie_host/pango_pcie_hdmi_frame_rgb565.received.ko" && \
    sudo "$HDC" -t "$TARGET" shell \
    insmod /data/local/tmp/pango_pcie_hdmi_frame_rgb565.ko profile=1
sudo "$HDC" -t "$TARGET" shell dmesg | \
    grep -E 'HDMI frame capture:|HDMI pixel format:|HDMI stream capture ready:|metadata mismatch|probe.*failed' | tail -n 20
```

新文件名用于区分部署产物，内核模块名仍为 `pango_pcie_hdmi_frame_test`。下载回读并 `cmp` 用于确认板端文件与本次构建产物相同。只检查最后一次 probe 的时间戳，历史失败记录会继续留在 `dmesg`。

在 HDMI 上显示红、绿、蓝色条，导出首次快照验收颜色：

```bash
sudo "$HDC" -t "$TARGET" shell \
    'cat /sys/bus/pci/devices/0002:21:00.0/hdmi_frame.rgb565 > /data/local/tmp/hdmi_color.rgb565'
sudo "$HDC" -t "$TARGET" file recv \
    /data/local/tmp/hdmi_color.rgb565 "$HOME/pcie_host/hdmi_color.rgb565"
wc -c "$HOME/pcie_host/hdmi_color.rgb565"
python3 frame_to_png.py hdmi_color.rgb565 hdmi_color.png
xdg-open hdmi_color.png
```

原始文件应为 **4,147,200 字节**。这是驱动加载时的快照，之后换场景要使用实时预览或再次采集。颜色应与 HDMI 输入一致，尤其核对红蓝没有颠倒、行边界没有错位。

## 4. 终端 A：启动彩色同帧识别

### 启动前确认 RKNN 程序也是彩色版本

驱动升级不会替换用户态的 `pcie_rknn_probe`。如果服务输出 `result_header=84`，随后报 `Input is not a version-1 1920x1080 GRAY8 PCIe stream record`，说明还在运行 V3 灰度程序；它会在第一帧格式检查时拒绝新的 RGB565 数据，每次连接都发送 0 条记录并关闭客户端。此时重新构建和部署 RKNN 程序，保留已验证的 FPGA 位流和驱动。

彩色流程统一编译、发送和启动 `pcie_rknn_probe_rgb565`。编译成功只更新 Ubuntu 本地文件；必须将这个产物发送到板端同名路径，再从该路径启动。若刚才已成功构建 `_rgb565`，直接执行下面的部署与 `--help` 检查即可。

先退出预览窗口并停止旧 RKNN 服务。下面使用新的本地和板端文件名，明确区分旧程序；文件名变化不改变程序通过 `prctl` 设置的进程名。

```bash
cd "$HOME/pcie_host"
cp -v /mnt/hgfs/pcie_host/{pcie_rknn_probe.c,build_rknn_probe.sh,preview_stream.py,rknn_stream.py,draw_detections.py,capture_stream.py,frame_to_png.py,frame_format.py} "$HOME/pcie_host/"
grep -nE 'RESULT_VERSION|RESULT_HEADER_BYTES|RGB565LE|PCIe input formats' pcie_rknn_probe.c
RKNN_RUNTIME="$HOME/rknn-yolov5-test/lib/librknnrt.so" \
    RKNN_PROBE_OUTPUT="$HOME/pcie_host/pcie_rknn_probe_rgb565" \
    bash build_rknn_probe.sh
```

源码应包含 `RESULT_VERSION 4U`、`RESULT_HEADER_BYTES 88U` 和 `GRAY8 or RGB565LE`。新版构建脚本先生成临时文件，校验彩色标识，再替换最终产物；构建失败时不会发布半成品。必须看到本次构建成功及彩色支持检查通过，再部署：

```bash
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pcie_rknn_probe_rgb565" /data/local/tmp/pcie_rknn_probe_rgb565
sudo "$HDC" -t "$TARGET" shell chmod 755 /data/local/tmp/pcie_rknn_probe_rgb565
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/rknn-yolov5-test/glibc/ld-linux-aarch64.so.1 \
    --library-path /data/local/tmp/rknn-yolov5-test/lib:/data/local/tmp/rknn-yolov5-test/glibc \
    /data/local/tmp/pcie_rknn_probe_rgb565 --help
```

帮助信息必须有：

```text
PCIe input formats: GRAY8, RGB565LE; result_version=4 result_header=88
```

这条只显示程序支持的格式，不读取 PCIe 帧或初始化模型。通过后可先前台运行，确保首帧推理正常：

如果出现 `Listen socket: Address already in use`，说明板端端口 9001 已被占用，新服务没有启动。预览可能仍连到旧服务，然后在发送首个结果头之前断开。隐藏了错误的 `killall` / `pidof` 命令没有输出不能证明端口已释放；应以板端实际监听状态为准。`ps` 显示的进程名和 `netstat` 显示的可执行文件名也可能不同，例如前者为 `pcie_rknn_probe`，后者为 `ld-linux-aarch64.so.1`。

先退出终端 B 的预览，再运行清理脚本。两个版本的程序通过 `prctl` 设置同一个进程名；脚本使用 shell 内置 `read` 检查 `/proc/PID/comm`，停止这些 RKNN 程序（包括单帧实例），最多等待 5 秒，并读取 `/proc/net/tcp` 和 `/proc/net/tcp6` 检查板端 9001 的 LISTEN 状态。新版不依赖板端缺少的 `awk`，也不依赖 `grep`、`tr` 或 `pidof`。脚本通过文件传输后直接运行，避免 HDC 对管道和嵌套引号的解析问题：

```bash
cp -v /mnt/hgfs/pcie_host/stop_rknn_server.sh "$HOME/pcie_host/"
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/stop_rknn_server.sh" /data/local/tmp/stop_rknn_server.sh
sudo "$HDC" -t "$TARGET" shell \
    sh /data/local/tmp/stop_rknn_server.sh
```

必须看到 `RKNN servers stopped; board TCP port 9001 is free.` 才继续。如果仍有服务或其他进程占用端口，脚本会报错并保留 PID 信息；再用以下没有管道的命令查看：

```bash
sudo "$HDC" -t "$TARGET" shell ps -A
sudo "$HDC" -t "$TARGET" shell netstat -ltnp
```

不能用 Ubuntu 上的 HDC 转发条目判断板端端口是否释放；两者属于不同系统的监听端口。不要照搬旧日志中的 PID。

如果旧版清理脚本打印 `awk: inaccessible or not found`，说明端口检查没有执行，后续“仍有监听者”的提示不能作为判断依据。重新复制并部署新版脚本。若 `netstat -ltnp` 已给出明确的 RKNN 监听 PID，可以直接使用 `sudo "$HDC" -t "$TARGET" shell kill -TERM 实际PID` 停止它，随后再次检查端口；PID 必须来自当前输出。

```bash
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/rknn-yolov5-test/glibc/ld-linux-aarch64.so.1 \
    --library-path /data/local/tmp/rknn-yolov5-test/lib:/data/local/tmp/rknn-yolov5-test/glibc \
    /data/local/tmp/pcie_rknn_probe_rgb565 \
    --model /data/local/tmp/rknn-yolov5-test/model/yolov5s-640-640.rknn \
    --runtime /data/local/tmp/rknn-yolov5-test/lib/librknnrt.so \
    --device /dev/pango_pcie_stream_0002:21:00.0 \
    --serve --bind 0.0.0.0 --port 9001 --preview-mode matched --profile
```

保持终端 A 运行，按第 5 节从终端 B 打开预览；复用已存在的 HDC 转发。正常启动头长为 `88`，连接后预处理应为 `RGB565LE/RGB`。如果报告端口占用，核对并停止旧服务；不要同时启动两个实例。

前台首帧推理和预览正常后，先用 `Ctrl+C` 停止前台服务，再用下面的命令启动同一个 `/data/local/tmp/pcie_rknn_probe_rgb565` 的后台实例。

将 HDMI 内容切换为需要识别的人物/车辆图像或视频：

```bash
RUN_TAG="rknn_color_$(date +%Y%m%d_%H%M%S)"
REMOTE_RKNN_LOG="/data/local/tmp/${RUN_TAG}.log"

sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/rknn-yolov5-test/glibc/ld-linux-aarch64.so.1 \
    --library-path /data/local/tmp/rknn-yolov5-test/lib:/data/local/tmp/rknn-yolov5-test/glibc \
    /data/local/tmp/pcie_rknn_probe_rgb565 \
    --model /data/local/tmp/rknn-yolov5-test/model/yolov5s-640-640.rknn \
    --runtime /data/local/tmp/rknn-yolov5-test/lib/librknnrt.so \
    --device /dev/pango_pcie_stream_0002:21:00.0 \
    --serve --daemon --bind 0.0.0.0 --port 9001 \
    --preview-mode matched --profile --log "$REMOTE_RKNN_LOG"

sudo "$HDC" -t "$TARGET" fport ls
if ! sudo "$HDC" -t "$TARGET" fport ls | \
    grep -F "$TARGET" | grep -Eq 'tcp:9001[[:space:]]+tcp:9001'; then
    sudo "$HDC" -t "$TARGET" fport tcp:9001 tcp:9001
fi
```

保留启动输出的真实 PID 和日志路径。通过 glibc 加载器启动时，单靠 `pidof pcie_rknn_probe` 可能查不到；停服务前核对该 PID 的 `/proc/PID/cmdline`，不要照搬旧 PID。不能让原始预览服务或另一个识别服务同时占用 stream。

端口映射已经存在时可以复用；重复执行 `fport tcp:9001 tcp:9001` 可能报 `TCP Port listen failed`。若窗口连接失败，先核对本次服务日志和目标设备在线状态。

## 5. 终端 B：彩色实时窗口

```bash
cd "$HOME/pcie_host"
python3 -u preview_stream.py \
    --host 127.0.0.1 --port 9001 \
    --require-detections --profile \
    2>&1 | tee rknn_color_preview.log
```

窗口显示 `RGB565LE | Mode matched`，图像序号等于检测序号，`delta=0.0 ms`。`S` 保存原始 `.rgb565`、彩色 `.png` 和同帧 `_detections.png`；`Q`/`Esc` 退出。零目标帧也显示原图并清空框。

连接后在终端 A 查看：

```bash
sudo "$HDC" -t "$TARGET" shell cat "$REMOTE_RKNN_LOG" | \
    grep -E 'listening|first inference|Preprocess:|YOLO detections:|RKNN stream timing:|failed|error' | tail -n 25
sudo "$HDC" -t "$TARGET" shell dmesg | grep 'HDMI timing:' | tail -n 10
```

首帧应报告 `Preprocess: RGB565LE/RGB -> NHWC UINT8 640x640 channels=3, letterbox=640x360 pad=0,140`。预览传输的是 1080p 原图，模型仍计算 640×640。

## 单帧与连续记录

关闭实时客户端并停止板端识别服务后，单帧测试沿用原启动命令，将 `--serve ...` 替换为：

```text
--save-frame /data/local/tmp/rknn_color_input.ppm
```

彩色保存 P6/PPM，大小 6,220,817 字节。它是 RGB565 展开的 RGB888；重新读取时可还原原始 RGB565 校验。`frame_to_png.py` 和 `draw_detections.py` 都支持这个文件；绘框仍要求同一次运行的完整日志。

需要验证连续原始记录时，关闭服务后先采集 1 条，再 2 条、5 条：

```bash
TAG="color_stream_$(date +%Y%m%d_%H%M%S)"
REMOTE="/data/local/tmp/${TAG}.bin"
LOCAL="$HOME/pcie_host/${TAG}.bin"
sudo "$HDC" -t "$TARGET" shell \
    "dd if=/dev/pango_pcie_stream_0002:21:00.0 of=$REMOTE bs=4147240 count=5; echo dd_exit_code=\$?"
sudo "$HDC" -t "$TARGET" file recv "$REMOTE" "$LOCAL"
wc -c "$LOCAL"
python3 capture_stream.py "$LOCAL" "$HOME/pcie_host/${TAG}_frames" --count 5
```

五条完整彩色记录应为 **20,736,200 字节**，保存为 `.rgb565`；可用 `verify_frames.py --expected 5 --png-dir ...` 验证并批量转换。`dd count` 数的是读取块，若出现 short read，应以文件字节数及解析结果为准。不要把 HDC shell 的二进制标准输出直接管道给 Python，继续使用板端文件或 TCP。

## 性能与回退

彩色每帧数据量是灰度的两倍。原始 18 fps 的像素负载约 74.65 MB/s（597 Mb/s），还不含协议开销；matched 的实际负载取决于识别帧率。本次保持模型输入分辨率与 USB/HDC 通道，重新收集 DMA、复制、校验、NPU 和传输耗时后再比较。不能直接沿用灰度 FPS 或保证达到 30 fps。

需要回退时，将 `PCIE_RGB565` 改为 `0` 并重新构建烧录，或恢复已验收灰度位流，重启 RK3568。新版驱动会自动选择 GRAY8，识别程序和预览仍支持灰度；灰度导出名继续为 `hdmi_frame.pgm`，记录长度为 2,073,640 字节。只允许严格匹配的签名/帧长度，避免 DMA 大小和分配缓冲不一致。
