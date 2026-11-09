# RK3568 三路拼接中的 HDMI 车道识别首版

这版沿用 Windows 已接入的 `bdd_lraspp_v1_20261013/best.onnx`，最佳权重为第 24 轮。迁移范围为车道语义分割、本车左/右边界及虚实线分类。摄像头和以太网继续原有采集与拼接。

## 处理与显示

| 环节 | 行为 |
|---|---|
| 输入 | 原始 HDMI 1920×1080 RGB565LE；不对缩小后的三路拼接图推理 |
| 预处理 | RGB565 展开为 RGB，裁去顶部 `round(height*0.35)`，PIL 等价双线性缩放为 640×384 |
| 模型 | LR-ASPP + MobileNetV3 Large，标签依次为 background、lane_solid、lane_dashed、other_marking |
| RKNN 输入 | UINT8 NHWC；ImageNet 归一化嵌入模型，板端不再手动归一化 |
| 精度 | 首版 `do_quantization=False`，RKNN 非 INT8 / FP16 路径 |
| 输出 | 原始四类 logits；启动时查询并验证 NCHW/NHWC，softmax 后处理 |
| 边界 | 置信度≥0.45、margin≥0.10、类别占比≥0.70；连续三次确认，丢失最多保留350ms |
| 本车区域 | 选择图像中心左右最近边界，检查曲线几何，匹配同一对边界后显示绿色区域 |
| 绘制位置 | 三路画面 x=0..639、y=60..419；摄像头/以太网区域保持原有字节 |
| 颜色 | 橙色=实线模型区域，青色=虚线模型区域，紫色=其他标线；粗线为当前本车两侧边界 |
| 状态文字 | L/R=本车左/右，SOLID=实线，DASHED=虚线，UNKNOWN=未确认，HOLD=短暂保留历史状态 |

虚线输出表示学习到的边界路径，可以跨越油漆间隙；不能再通过路径是否连续判断虚实。`DASHED` 仅表示边界类型，不能据此认定侧后方无车或直接允许变道。

本次没有迁移 YOLO 目标检测、目标遮挡掩膜、红绿灯及完整驾驶决策状态机。C 解码器版本为 `lane-style-curves-c-v1`，核心分组/拟合与 Windows 版本对照；时序实现为保守的左右边界几何匹配，不能宣称所有 Windows 行为完全一致。

## 1. Ubuntu：准备与转换

共享目录和现有板端三路设备已经探测通过，不需要重跑 probe。执行：

```bash
bash /mnt/hgfs/pcie_host/prepare_hdmi_lane_rk3568.sh
```

脚本在 `$HOME/pcie_lane_rk3568` 编译，使用独立的 `$HOME/.venvs/rknn-lane-2.3.2` 环境安装 RKNN Toolkit2 2.3.2，保留系统 Python；PyTorch使用CPU版本，避免安装转换不需要的CUDA依赖。首次安装需要网络。当前Ubuntu实测缺少Python3.8虚拟环境支持，先执行 `sudo apt-get update && sudo apt-get install -y python3.8-venv`，再重跑准备脚本即可；脚本会重建上次未完成的虚拟环境，无需手动删除。

它会运行 C 核心测试、Python/C 预处理和曲线对照、模型转换，以及三种合成输入的 ONNX/RKNN 模拟器对照。任何步骤失败都停止，不切换板端服务。

| 产物 | 作用 |
|---|---|
| `rk3568_lane_deploy/prepare.log` | 完整准备日志，Windows 共享目录可直接读取 |
| `pcie_multivideo_server.lane_v1` | 新的 GNU/glibc ARM64 服务，不覆盖 ethernet_v6 |
| `lane_style.rknn` | 由已验证 ONNX 转换出的板端模型 |
| `lane_style.contract` | C 端启动时校验的模型身份、标签、归一化、字节数、FNV64、解码器契约 |
| `lane_style.manifest.json` | SHA256、原始模型身份、epoch、布局规则、精度、Toolkit版本及模拟器对照结果 |

只有看到 `PREPARE_PASS` 才继续。模拟器通过代表转换一致性初检，真实 HDMI 识别质量、板端数值和帧率仍需实机验收。

## 2. Ubuntu：板端模型检查

2026-10-25准备流程已实测得到`PREPARE_PASS`；RKNN产物8,278,154字节，SHA256为`01519b4f1815e758491c6b84d4d0f5bc47e211f39cc876cbaeac70ce04196647`，模型与contract的SHA256均已核对。三组合成输入的类别图差异分别为0、0、约0.0053%。板端加载已实测得到`LANE_INSPECT_PASS`和`DEPLOY_inspect_PASS`，实时预览及速度待第3步验收。

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_hdmi_lane_rk3568.sh inspect
```

上传到 `/data/local/tmp/hdmi_lane_v1`，经已有 glibc loader 动态加载已有 `librknnrt.so`，检查模型和张量，不打开 PCIe 设备、不停止正在运行的三路服务。成功标志为 `LANE_INSPECT_PASS` 和 `DEPLOY_inspect_PASS`。

RKNN2.3.2实际返回FP16输入`NHWC [1,384,640,3]`和FP16输出`NCHW [1,4,384,640]`，两者均带`AFFINE scale=1 zp=0`元数据。代码接受FP16/FP32的`NONE`及恒等`AFFINE`，继续拒绝整数张量和非恒等缩放；已通过对应回归测试、ARM64编译及实机加载检查。后续需要重编译并重新检查时使用以下命令，保留已转换模型和当前三路服务：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/recheck_hdmi_lane_rk3568.sh
```

日志写入`rk3568_lane_deploy/recheck.log`和`deploy_inspect.log`。新版标识为`diagnostic=tensor-details-v3`。不能跳过契约检查直接启动。

## 3. Ubuntu：启动与预览

先关闭当前 Ubuntu 预览窗口，然后执行：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_hdmi_lane_rk3568.sh start
```

此步骤重新检查模型，再停止旧三路服务、启动带识别的新服务，复用或建立 `tcp:9004 → tcp:9002` 转发。默认 BDF 为 `0002:21:00.0`。如果设备节点发生变化，执行前设置 `export FPGA_BDF=实际BDF`。

另开 Ubuntu 终端，保持 Windows HDMI 视频播放和原以太网发送端运行：

```bash
python3 /mnt/hgfs/pcie_host/preview_multivideo.py --host 127.0.0.1 --port 9004 --timeout 60
```

也可以先关闭窗口，用现有协议检查十帧：

```bash
python3 /mnt/hgfs/pcie_host/preview_multivideo.py --host 127.0.0.1 --port 9004 --headless --count 10 --timeout 60
```

`start`启动的首版采用同帧同步推理；2026-10-25的19组日志样本中，平均预处理67.09ms、推理及输出读取220.48ms、后处理90.38ms，总发送周期470.79ms，约2.1帧/秒。纯发送平均1.96ms，主要卡顿来自发送前等待推理。流畅预览更新见第4步。超过500ms的推理输入时间断层会重新确认边界。

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_hdmi_lane_rk3568.sh log
```

验收时确认：三个窗口保持原布局；只有 HDMI 窗口有模型叠加；左右边界和状态文字与当前画面对应；不确定状态显示 UNKNOWN；HOLD 有时间限制；日志没有推理错误。不要以合成测试代替实机验收。

## 4. 实时画面与独立车道更新

用户已确认接受实时视频与独立识别更新。`multivideo-hdmi-lane-v2`已完成Ubuntu测试、板端检查和启动。2026-10-26的22.7秒录屏显示9.9～12.8FPS，按接收计数估算平均11.2FPS；识别稳定性和延迟仍需优化。

| 更新 | 行为 |
|---|---|
| 独立推理线程 | NPU推理不再阻塞三路采集、拼接与发送；仍仅识别HDMI源 |
| 有限队列 | 一帧正在推理、一帧等待；新输入替换等待帧，避免积压 |
| 后处理优化 | 保留全部类别argmax和非有限值检查；只为参与边界判断的实线/虚线像素计算softmax分数，背景和其他标线的未使用分数置零 |
| 结果来源 | 叠加显示`AI:ASYNC AGE:...MS FRAME:...`，明确结果来自较早输入帧 |
| 过期策略 | 来源帧年龄超过750ms或推理错误时清除车道叠加和已确认类别，显示STALE/ERROR；启动等待时显示WAIT |
| 预览校验 | 使用已自检的C/FNV-1a加速库；构建自检输入平均校验约2.9ms |
| 显示帧率 | 左下角`Preview ... FPS`表示收到视频帧的速度；不代表模型每秒推理次数 |
| 版本记录 | `rk3568_lane_deploy/preview_runtime.json`记录模型身份、程序及源码SHA256、异步模式、队列容量、750ms阈值和校验后端 |

首次更新时关闭预览窗口，运行以下命令；保留已转换模型、已有Toolkit环境和板端旧v1程序，不重新训练或转换模型：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/optimize_hdmi_lane_rk3568.sh
```

脚本先测试再切换服务。2026-10-25已实测得到`OPTIMIZE_PASS`；弯曲边界测试、FP16恒等AFFINE兼容测试、NCHW/NHWC与FP64 softmax对照、队列替换、输入快照隔离、结果过期、HDMI外区域逐字节不变、失败清除和线程退出测试均通过。异步测试中的`success=0`为主动模拟的失败分支，用于验证清除旧结果。

已完成更新后，直接打开预览：

```bash
bash /mnt/hgfs/pcie_host/preview_hdmi_lane_fast.sh
```

该入口使用`$HOME/pcie_lane_rk3568`中已编译验证的C校验库。记录左下角FPS并保持预览运行，在另一个终端读取日志：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_hdmi_lane_rk3568.sh log
```

`lane`行记录模型工作耗时；`profile total_us`记录视频采集发送周期，两者不再相加。`lane_async replaced_pending`增加表示主动丢弃过时等待帧，是预期行为。需要重新启动已部署的异步版时使用`deploy_hdmi_lane_rk3568.sh start-fast`。实际识别刷新率仍受当前FP16模型速度限制，不能把视频FPS当作识别FPS。

## 5. UNKNOWN原因诊断与预处理缓存（2026-10-26）

用户提供的23条视频周期样本平均89.80ms，7条识别耗时样本平均290.03ms（预处理54.54ms、推理及IO203.25ms、解码32.24ms）。第90～150次结果源帧时间对应平均约3.5次识别/秒；每十次结果的平均间隔约277～300ms。这些抽样不能证明所有单次间隔都小于500ms，也不支持把500ms重置认定为主要原因。本次以太网`available=0`，以上数据不代表三源均有输入时的性能。

先在Windows的HDMI输出屏幕全屏播放道路视频，隐藏控制栏、任务栏，移走鼠标并保持原比例。固定裁剪规则针对整个HDMI输入，播放器界面会进入模型输入；不自动猜测播放器的内容区域。

关闭预览后在Ubuntu执行：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/diagnose_hdmi_lane_rk3568.sh
```

脚本同步源码到`$HOME/pcie_lane_rk3568`，运行核心、诊断、张量、解码、异步测试和Python/C对照，编译独立的`pcie_multivideo_server.lane_v3`并检查模型后切换。复用原RKNN产物和已安装环境，不安装依赖、不训练或转换模型。v2二进制保留。成功标志为`DEPLOY_start-diag_PASS`和`DIAGNOSE_PASS`，服务标识为`multivideo-hdmi-lane-v3-diag`。实机检查和预览效果需以用户执行结果为准。

```bash
bash /mnt/hgfs/pcie_host/preview_hdmi_lane_fast.sh
```

在另一个终端读取日志：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_hdmi_lane_rk3568.sh log
```

预览第三行显示`WHY L:... 1/3 R:... 2/3`。分数表示连续确认次数；3/3达到所需次数，仍需同时阅读最终类型、结果年龄和原因。

| 原因 | 含义 |
|---|---|
| `OK` | 类型已确认 |
| `NO-PIX` | 边界扫描行对应半幅没有实线/虚线类别像素 |
| `LOW-SCORE` | 有该类像素，但扫描行没有通过conf/margin阈值的像素 |
| `NO-RUNS` | 有通过阈值的像素，但未形成宽度合格的横向观测段 |
| `NO-CURVE` | 有观测段，但没有通过长度、跨度、参考点和拟合几何检查的曲线 |
| `NO-SIDE` | 有候选，但没有最终选择到该侧边界 |
| `MIXED` | 边界内实线/虚线类别占比未达到0.70 |
| `CONFLICT` | 相邻重复候选的类别冲突 |
| `CONFIRM` | 边界证据有效，尚在连续三次确认中 |
| `MOVED` | 当前边界未匹配到上次边界，重新确认 |
| `TYPE` | 匹配边界的类别改变，重新确认 |
| `HOLD` / `EXPIRED` | 短暂保留历史类型 / 历史类型超时 |
| `WAIT` / `STALE` / `ERROR` | 等待首个结果 / 整体结果过期 / 推理失败 |

每次成功识别输出一条`lane_diag`：包括`input_gap_ms`、`reset=NONE/SIZE/CLOCK/GAP`、扫描行像素和观测段数量、路径/候选/最终边界数量、拒绝几何数量、两侧观测类型/最终类型/原因/次数/分数/类别占比，以及本车道区域几何和确认次数。`NO-PIX`仅描述模型输出，无法单独区分真实无标线、遮挡和模型漏检；`MOVED`也不能单独证明本车发生变道。诊断不等于道路真值或识别准确率。

缩放权重和中间缓冲区由每个推理实例复用，分辨率变化时重建，保留RGB565展开、固定裁剪、PIL双线性和整数舍入；缓存与原接口在重复调用、不同内容和分辨率变化后逐字节一致。缓存仅减少重复分配和权重计算，板端节省耗时待测。模型权重、标签顺序、归一化、decoder版本、置信度阈值、连续三次、350ms保留、500ms断层和750ms结果年龄规则均保持原值。

运行记录为`rk3568_lane_deploy/diagnose.log`和`diagnostic_runtime.json`，成功启动后同步当前`preview_runtime.json`。重启后先恢复共享挂载和PCIe驱动，再启动诊断版：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_hdmi_lane_rk3568.sh start-diag
```

需要恢复保留的v2异步版时关闭预览并执行：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_hdmi_lane_rk3568.sh start-fast
```

Windows已运行核心/原因诊断/张量/解码测试及三分辨率PIL、曲线对照，ARM64严格编译通过。Windows缺少pthread运行环境，更新脚本在Ubuntu执行异步线程回归测试。用户后续提供的逐次`lane_diag`已确认板端诊断版运行；启动/构建的完整PASS仍以`diagnose.log`为准，不能单凭运行片段补认所有检查通过。

### 2026-10-26 全屏输入与路口运行反馈

用户确认已全屏并隐藏播放器界面，较长UNKNOWN主要发生在路口/左转。附件`febd36ab-db21-4b3e-a5e1-6b03248cc2d1/已粘贴的文本.txt`含32次连续诊断（HDMI序号5857～5961，源时间跨度约9.25秒）。

| 证据 | 解释及范围 |
|---|---|
| 32次均`reset=NONE`，输入间隔150.05～401.16ms | 本片段没有触发500ms断层重置 |
| 5857～5903共15次左侧均`SOLID/OK`，确认计数15～29 | 该段左侧类型输出连续；未结合逐帧真值，不能直接当作准确率 |
| 右侧出现`HOLD → MOVED → CONFIRM → OK`，之后`MIXED → CONFIRM → SOLID/OK` | 有局部丢失、边界匹配变化和类别过渡，均按当前规则重新确认 |
| 5907～5950共14次`boundaries=0` | 主要为NO-CURVE、少量NO-PIX/NO-RUNS；有些帧仍有像素，但没有符合几何要求的曲线 |
| 5954、5958、5961左侧再次出现DASHED候选，次数1、1、2 | 正在恢复，期间一次位置匹配变化；片段结束前尚未确认完成 |
| 11条视频周期平均81.76ms，3条识别总耗时平均310.26ms | 抽样数量和场景与上一轮不同，不能据此判断缓存带来了性能提升 |

结合用户场景描述，长段无可靠边界与路口标线中断相符；不放宽几何门槛，不跨路口长时间沿用旧曲线，不将UNKNOWN自动标为INTERSECTION。下一验收应把清晰直路稳定性、虚实线过渡、路口未知、出路口重新确认分开。当前迁移范围仍是车道模型；路口识别、信号灯和转向许可需要独立证据及后续集成。此反馈不证明所有UNKNOWN合理，也不说明需要重新训练。

## 回退原三路预览

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_hdmi_lane_rk3568.sh rollback
```

回退脚本停止识别服务并使用原 `/data/local/tmp/pcie_multivideo_server.ethernet_v6` 启动三路预览，预览命令和9004转发保持可用。本次无需修改 FPGA 位流或内核驱动；重启后若设备节点消失，先按 `ETHERNET_VIDEO.md` 恢复现有驱动链路。

## 开发验证记录

2026-10-25：Windows MinGW C11 严格告警编译通过；新增识别服务和关闭识别编译选项的原服务均经 Zig ARM64 Linux/glibc 交叉编译通过（`-Wall -Wextra -Werror`）。人工弯曲实/虚线、连续确认、保留过期、类别冲突、边界对切换、异常输入及 HDMI 区域外逐字节不变测试通过。RGB565展开及缩放在1920×1080、641×481、320×240三种输入上与PIL逐字节一致；合成弯曲边界的C/Python曲线坐标误差≤1像素，类别一致。Python语法和四份Shell脚本的`bash -n`检查通过。模型转换和实机运行状态以 `prepare.log`、`deploy_inspect.log` 和板端日志为准。
