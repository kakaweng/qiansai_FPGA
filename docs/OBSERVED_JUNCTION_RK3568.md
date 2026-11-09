# HDMI 感知增强路口预演

2026-11-02更新：下文“未知时动画继续”的历史策略已由路线冲突检查替换。当前普通预演在相关横道遇确认/疑似行人冲突或证据过期时停车，连续新观测确认解除后恢复；完整说明及安装命令见 [JUNCTION_ROUTE_CONFLICT.md](JUNCTION_ROUTE_CONFLICT.md)。道路补全、目标平滑和历史展示继续保留。

2026-11-01。按用户允许推测和美化的要求，M路口视图采用“完整道路示意＋真实目标驱动＋短时预测”。保留Claude深色界面、路口建筑及本车路线，去掉密集P/Z编号和碎片横道。复用BDD车道、YOLO、Mapillary横道和PIE模型，无需重训。界面始终标注“含推测”；道路形状、目标布局和动画速度属于示意，不能作为完整路口重建或真实距离。

## 实际行为

| 需求 | 本版实现 |
| --- | --- |
| 右转预演 | L选右转，G播放；检查驶入和右转出口横道。确认冲突停车礼让，路线内疑似冲突及结果过期停车确认，连续新观测解除后恢复；其他方向或路线外的人不因人数触发。 |
| 检测中断 | 单个模型短时无结果时继续平滑显示，超过期限淡出；HDMI断流、状态连接中断、停止或换源会清空目标并暂停动画。恢复后按G继续。 |
| 车道显示 | 常规视图保留真实曲线；M视图先使用双车道示意，可靠可见车道数经两次独立观测确认后更新驶入一侧，最多保持5秒。其余方向属于模板补全。 |
| 斑马线位置 | M视图补全四向整齐横道，标为“完整横道 · 示意补全”；不再把分割碎块直接铺进路口。常规实时视图仍能查看真实mask。 |
| 人车位置 | YOLO框决定目标类别、相对左右和远近，映射到易读的示意道路位置，最多显示6个行人和5个其他交通目标；行人靠近示意横道不代表已检测到真实接触。 |
| 横道归属修正 | 中间偏右的行人归入右转出口横道，同侧、相近深度人群参考已有归属；同一跟踪目标保持横道身份，减少右转镜头移动导致换错横道。预测位置限制在对应横道范围内，同一横道内图标适当错开以避免堆叠，J/I/L不改变目标归属。仍属于显示推测，实际接触由原板端规则判断。 |
| 漏检与延迟 | 板端显示通道提供2500ms内的历史框；目标平滑移动，最多1秒速度外推，750ms后标预测、逐渐淡化，源时间超过2.6秒清除；重复刷新不续期。 |
| 少量补全 | 新鲜同源的总人数存在但没有框时，最多补3个明确标为预测的示意行人；这些图标不形成确认接触，不触发驾驶判断。 |
| 操作 | J/I/L选择路线，G开始/继续，B暂停，R重置，F违规预演；这些运动操作在本地进行，M返回常规视图。A/D仍发送原板端变道请求。 |

原板端 `crosswalk-contact-v1` 关系状态继续保留，不用推测图标覆盖关系结果。`CLEAR / OBSERVE / CONFIRMING / UNALIGNED / YIELD / YIELD_HOLD / STALE`区分观察、候选、时间不齐、礼让及过期。路口动画增加独立路线冲突检查；左转/直行仍遇新鲜有效红黄灯暂停。动画不输出板端行驶许可。

候选要求分数至少0.45、框高至少画面3%、足点位于可分析下方区域；脚点须命中实际横道mask或有双侧真实像素支持的微小空洞，并有邻域支持。至少两次目标观测、150ms跨度及两次独立横道观测才确认，重复HTTP刷新不计数。目标源最多750ms，横道关联最多850ms、仅供显示的横道最多1000ms，两源时间差不超过350ms；板端无冲突需两次新目标观测且跨度至少200ms，本地预演还需满足路线冲突解除与500ms连续观测。画面边缘、低分远小目标只保留观察。该规则是当前图像证据下的演示规则，尚不代表真实距离下的精确碰撞预测，也不能证明整条真实路线无人。

这一版优先保证路口展示的连续性和可读性，模板和历史预测不会写回板端。道路可以补全，目标仍主要由实际检测驱动；短时预测有期限，不会无期限保留已消失的人。常规实时道路视图继续遵守原检测证据要求。

## 两个数据集的作用

| 数据 | 已有训练产物与本轮用途 |
| --- | --- |
| Mapillary Vistas | `mapillary_lraspp_v1_20261101`，第11轮，二类斑马线像素分割；沿用非INT8 RKNN。原完整验证集IoU为58.57%，仍有漏检和车头误检；实际mask用于常规视图和板端关系，M视图以完整横道示意补足展示。 |
| PIE | `pie_anticipation_v2_20261101`，行人历史几何分类头；板端分数继续保留。它不提供车道数或斑马线分割，不替代空间关联。 |
| BDD100K | 继续使用原车道线型模型及所有可见边界；没有重新训练车道模型。 |

## Ubuntu更新

若出现普通界面没有YOLO框、`OBJ --`、车道计数在1/2之间反复重置，请优先运行`repair_front_perception_rk3568.sh`，见[识别恢复修正](FRONT_PERCEPTION_REPAIR.md)。它同时更新板端取帧、车道分析间隔和普通视图目标保持；不能仅靠重开预览应用。

已成功运行感知增强版、仅更新本次横道归属修正时：关闭旧预览后执行 `bash /mnt/hgfs/pcie_host/preview_pie_anticipation.sh`。启动器会复制最新显示代码到Ubuntu本地目录，无需再次编译或切换RK3568服务。首次安装感知增强版再按以下完整流程。

先关闭原预览，恢复既有共享目录及USB连接。只需使用现有RKNN转换环境，不安装依赖、不重新训练、不修改FPGA位流。

本次同时修改板端目标快照、控制器显示协议和预览；仅重开预览无法取得延迟目标。新入口复用原整合流程，并从运行中的控制器检查display_world接口。已有合格RKNN产物会复用。

```bash
sudo -v
sudo mkdir -p /mnt/hgfs
if ! mountpoint -q /mnt/hgfs; then
    sudo vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other
fi
sudo -v && bash /mnt/hgfs/pcie_host/update_junction_presentation_rk3568.sh
```

第一次执行会读取已训练的ONNX，用既有`$HOME/.venvs/rknn-lane-2.3.2`做RKNN转换与参考输入数值对照。转换成功且哈希/合同匹配时，后续复用产物。脚本会执行回归测试（包括Linux异步worker测试）、编译、检查原驱动和FPGA设备、上传模型与控制器、检查模型并切换服务。出现任一失败标记时不要将其当作部署成功。

新版PIE配置的检查与启动必须同时加载斑马线模型，缺文件或缺`CROSSWALK_INSPECT_PASS`会报错，不会降级成旧版仍报告整合成功。原`front_p1`配置不要求新增模型。

| 标记 | 含义 |
| --- | --- |
| `CROSSWALK_CONVERSION_PASS` | ONNX→RKNN转换和参考数值对照通过；不表示视频准确率通过 |
| `CROSSWALK_ARTIFACTS_PASS` | RKNN、合同、来源版本及哈希一致 |
| `FRONT_P1_TEST_PASS` | 本地C/预览/协议与Linux异步worker回归完成 |
| `CROSSWALK_INSPECT_PASS` | 板端实际加载新模型，张量布局和精度检查通过 |
| `FRONT_BOARD_START_PASS` | HDMI板端服务启动 |
| `PEDESTRIAN_POLICY_PASS` | 已从实际启动的控制器读回新空间关联策略，未误连旧版 |
| `OBSERVED_JUNCTION_PASS` | 本次整合脚本全部完成 |
| `JUNCTION_PRESENTATION_PASS` | 新显示通道已从运行控制器读回，本轮更新完成 |

另一终端打开：

```bash
bash /mnt/hgfs/pcie_host/preview_pie_anticipation.sh
```

空格开始检测，M进入“路口预演 · 感知增强”，J／I／L选左转／直行／右转，G开始，B暂停，R重置。请全屏播放前视视频并隐藏播放器栏。本地预演不暂停或控制输入录像。M模式不需要先完成地面标定；常规真实道路视图仍可用H标定和U选择视频区域。

验收重点：完整道路不随分割碎片跳变；行人/车辆短时漏检不断闪，淡色虚环清楚表示预测；G后遇路线内行人停车礼让或等待确认，连续新观测确认解除后恢复；停止、断流和换源后旧人车清空。实际变道请求仍通过右下角反馈，不使用路口动画作许可。

重启RK3568后，按原流程恢复共享目录、USB及驱动，然后执行：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_pie_anticipation_rk3568.sh start
bash /mnt/hgfs/pcie_host/preview_pie_anticipation.sh
```

查看日志：`sudo -v && bash /mnt/hgfs/pcie_host/deploy_pie_anticipation_rk3568.sh log`。`crosswalk_async`提供实际源序号、耗时、年龄。完整示意画面本身不证明所有模型已接通，更新应以模型检查及`JUNCTION_PRESENTATION_PASS`为准。

## 接口与验证边界

模型与礼让关系判断执行在RK3568，Ubuntu跟踪、投影和绘制板端确认关系。横道worker只保留最新待处理帧，至少700ms间隔，共享NPU串行调度；车道最多连续优先两次，排队的YOLO与横道交替获得辅助名额，避免横道长期饥饿。视频刷新不等分割，旧mask最多1000ms；实际帧率和三模型时延仍需板端验收。

独立`FCW1`报文提供全HDMI源平面的320×180二值mask，按行LSB位序打包。HTTP的`state.crosswalk`同时包含producer、source_seq/source_ms、age_ms、valid、model_version、plane_width/plane_height及mask_lsb_hex。它不会延长FRONT或行人的新鲜期限。停止、换源、时间回退与重复报文都不能使旧证据继续有效。

新`FWD1`报文提供显示专用目标历史，HTTP增加`display_only: true`与`display_world`。接收端校验同producer、会话和源时间；它不进入`drive_update`、`pg_update`或PIE分类器。原`FWM1 / world`及驾驶规则时限不变。目标显示快照强制清除灯色；不能用历史图标恢复旧交通灯许可。

开发机已验证C空间礼让规则、真实本地C服务UDP→HTTP、mask解码、区域/行人对应关系、可见车道带和界面失效保护；服务及控制器ARM64严格编译通过。本轮Windows没有可执行Linux pthread测试的环境，调度及横道worker已交叉编译检查，并纳入Ubuntu整合入口运行；RK3568实际显示/帧率需执行上述入口，不能用Windows离线结果代替。

## 现有视频离线检查

新版连续回放在`rk3568_crosswalk_deploy/presentation_review/`。其中`pie_original/preview_after.mp4`使用PIE set01/video_0004的160秒起12秒原片，实际YOLO ONNX 2Hz采样，模拟900ms结果延迟，15FPS输出180帧。中间画面补全四向道路、人车跟踪和平滑，本车完成约12秒路线预演；截图保留OFFLINE标记。它验证展示链路，不代表RK3568帧率或模型准确率提升。运行：

横道归属修正后的6秒回放单独保存在`presentation_review/crossing_fix/`，使用同一原片、同一900ms延迟；`after_045.png`用于检查右侧人群的横道位置。相关32项展示、保持、复位和接口隔离测试通过。

```powershell
.\.venv-lane\Scripts\python.exe tools\review_junction_presentation.py --video F:\waymo_data\pie\set01\video_0004.mp4 --start 160 --seconds 12 --delay-ms 900 --output rk3568_crosswalk_deploy\presentation_review\pie_original
```

以下是之前严格映射版本的检查记录，仍可用于模型问题分析，不作为新版M视图的显示约束。

`rk3568_crosswalk_deploy/offline_review/`保存六张原视频帧及实际ONNX结果，来源为原行车视频与PIE set01的两个视频。`*_comparison.jpg`左侧原图、中间感知叠加、右侧相对地面图；`*_source.jpg`是未叠加的原始帧，也用于转换数值对照。可执行`.venv-lane\Scripts\python.exe tools/review_observed_junction.py`重新生成。

这些单帧检查已确认车辆和行人能投影至2.5D、斑马线mask能保留真实形状及空洞；每张使用独立跟踪器，不重复同一帧凑确认。单帧区域显示“候选”，等待后续独立观测确认。PIE画面中车头白色区域仍有明显误检，需要继续收集误检样本优化模型；不能以这六帧替代准确率评测。

旧混合路口检查图仍保存在 `offline_review/`，属于上一版显示基线。本次 `pedestrian_review/` 使用原行车视频23秒附近、PIE set01/video_0001的49秒及334秒附近三段，共48个不同真实视频帧，通过RGB565精度模拟、现有ONNX、板端C解码和行人策略回放；目标4Hz、横道按源时间750ms采样。这里使用虚拟源时间，不模拟板端推理耗时，不能据此声称RK3568帧率或准确率提升。

这些短片仍出现横道漏检和无法可靠匹配足点的情况，没有形成连续确认的横道人；因此只能验证降级为观察/待确认，不能作为横道人召回率通过的证据。确认礼让、短时丢失、时间错位和释放另有构造输入的真实C控制器UDP→HTTP回归。界面截图保留醒目的Windows离线标记。可运行 `.venv-lane\Scripts\python.exe tools/review_pedestrian_junction.py` 重新检查；需要先编译 `tests/pedestrian_gate_bridge.c` 为 `rk3568_pie_deploy/pedestrian_gate_bridge.dll`。

常规真实感知视图中，Z编号表示可见mask区域，Z数量不等于物理横道总数。可见车道计数要求车道带在同一段纵深范围横向连续。新版M视图中的完整路口、画面外车道和转向后道路属于模板推测，两种视图的含义不同。
