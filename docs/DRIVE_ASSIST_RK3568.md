# RK3568 三路辅助识别与中文驾驶面板（v6）

车道跳变修正：v6新增模型间车道优先仲裁，获得执行机会后才选择最新图像，保留目标任务执行机会和原时序阈值。应用此次C更新需重新执行下方首次升级命令，不能只重开预览或执行start。录屏证据、本地验证与实际验收步骤见[LANE_SCHEDULING_FIX.md](LANE_SCHEDULING_FIX.md)。

显示入口更新：辅助驾驶状态和三路识别画面现可在同一个预览窗口操作，无需另看网页。关闭旧预览后执行`bash /mnt/hgfs/pcie_host/preview_drive_assist.sh`，中继保留运行；更换Windows视频源的完整命令见[UNIFIED_DRIVE_PREVIEW.md](UNIFIED_DRIVE_PREVIEW.md)。本次只更新显示端，不要求重建或重启已经运行的第六版板端服务。下文保留网页面板作为备用入口。

2026-10-28。按本轮要求完成第2、3、4步的代码，先跳过第1步左右区域标定。复用已验收的 LR-ASPP 车道模型和 YOLOv5 COCO80 RKNN；不训练、不转换模型、不修改 FPGA 位流。

## 当前实现

| 功能 | 行为 |
|---|---|
| 前方 HDMI | 保留车道边界、车辆、行人和灯色识别 |
| 左侧 OV5640 | 增加车辆检测框；显示左侧数据时效，继续标记手动对齐、同步未确认 |
| 右侧以太网 | 增加车辆检测框；关联受控视频源的 session、wire 和源时间 |
| 多路调度 | 单个 YOLO 模型/context；常规前、左、前、右轮询。前方结果落后250ms时提高前方优先级，连续最多3个前方任务后服务等待的侧方。每路只保留最新待处理帧 |
| 模型间调度 | 等待中的车道优先，连续最多两个车道任务后服务等待的YOLO；获得计算机会后才取最新图像，取输出后释放，CPU解码独立 |
| 变道请求 | 面板点击向左／向右即有原因反馈，当前请求持续更新；10秒自动结束，也可取消 |
| 实线报警 | 所选一侧有有效实线结果时显示红色禁止提示，不因侧方未检测到车而解除 |
| 虚线反馈 | 区分侧方缺失／过期、检测到车、左侧同步未确认、右侧结果时间不匹配，以及需人工观察 |
| 模拟车辆状态 | 模拟P/N/D/R挡、速度±5、制动；运动中不换挡，D挡上限120、R挡上限10 km/h。与视频速度和真实车辆无关 |
| 重播与失效 | 新session清理请求、车况和各路感知；旧推理即使随后完成也不发布；重复源wire不更新证据；结果超过750ms失效 |

侧方车辆包括 COCO 的自行车、汽车、摩托车、公交车、卡车；侧方不判断灯色、不做车道检测。前方灯色仍仅表示检测到的颜色，未判断本车道对应灯和箭头方向，不由绿灯推导可左转／右转。行人仅展示，未添加“检测到人就礼让”的报警。

**跳过标定的实际边界：**本版可以提示“所选侧实线，禁止跨越”和“虚线，侧方有车辆／需要观察”，不会输出“安全可变道”。侧方任意车辆不直接视为目标车道冲突，零检测不等于没有风险；不输出没有相机标定依据的米制距离和TTC。

## 首次升级：Ubuntu一条命令

保持现有Windows受控播放器打开，关闭当前OpenCV预览窗口。在Ubuntu执行：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/integrate_drive_assist_rk3568.sh
```

流程为本地源码测试/构建 → 上传独立命名的v6服务和控制器 → 检查现有模型能否加载 → 启动新服务。prepare和install阶段不停止旧服务；start阶段在控制器启动成功后切换视频服务。成功末尾应有：

```text
SYNC_PREPARE_PASS
SYNC_INSTALL_PASS
SYNC_BOARD_START_PASS
SYNC_START_PASS
DRIVE_ASSIST_PASS
```

在Ubuntu另外两个终端运行：

```bash
bash /mnt/hgfs/pcie_host/preview_hdmi_lane_fast.sh
```

```bash
python3 /mnt/hgfs/pcie_host/sync_control_relay.py
```

Ubuntu浏览器打开／刷新 **http://127.0.0.1:9011**。标题应为“中文驾驶辅助面板”，带“RK3568 · 第六版”。按钮在浏览器页面中，OpenCV窗口仍负责视频显示。先点“开始检测 / 从头重播”，配对正常后试向左／向右请求。预览需保持运行，当前服务依靠预览连接持续采集。

Windows视频源命令沿用原有入口，显示输出编号应先核对：

```powershell
& 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host\run_sync_hdmi_ethernet.cmd' --list-monitors
& 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host\run_controlled_source.cmd' --monitor 1 --bind 192.168.1.102
```

这里的1只是示例，使用实际连接FPGA的显示输出；不需要重启当前已经正确工作的播放器。

## 已升级后重启与回退

共享目录挂载、HDC连接和驱动恢复后，使用新入口启动第六版：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_drive_assist_rk3568.sh start
```

日志：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_drive_assist_rk3568.sh log
```

退回原先已安装的同步第五版（保留前方识别、三路输出和开始控制）：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_drive_assist_rk3568.sh rollback
```

第六版独立二进制为`pcie_multivideo_server.drive_v6`和`rk_drive_control`，不会覆盖`scene_sync_v5`和`rk_sync_control`。生命周期脚本同时识别两种控制器，包括通过glibc加载器启动且argv改变的情况；未知端口占用者仍只报告，不自动杀进程。

## 感知、决策和面板所在位置

| 环节 | 位置 |
|---|---|
| 视频文件输出 | Windows受控播放器；HDMI和EVF1分别进入FPGA |
| DMA读取、拼接、LR-ASPP和三路YOLO | RK3568视频服务 |
| 开始/停止、变道请求、证据校验和模拟车况状态机 | RK3568 C控制器 |
| HTTP/控制中转 | Ubuntu现有HDC端口转发和Python relay |
| 中文面板显示 | Ubuntu浏览器加载RK3568提供的页面；JavaScript仅展示和提交请求 |

侧方640×480输入的YOLO适配是640×480有效图像、上下各80行补114；前方仍是640×360、上下各140行补114，与原先逐字节一致。模型本体、RGB通道顺序、NHWC-U8输入、INT8 NCHW三个检测头、anchors、标签顺序、0.25阈值、0.45同类NMS均沿用现有实现；坐标按各路补边和缩放分别还原。左侧框仅画在拼接画面`(800,0,320,240)`，右侧仅画在`(800,240,320,240)`。

感知报告经板端本地UDP9013传给C控制器，携带session、单调时间、各路证据时间和源时间。控制器拒绝其他session、倒序、未来、无效范围和过期报告。右侧联合反馈额外要求识别对应源时间与当前前方源时间相差不超过250ms；这与接收配对的150ms阈值用途不同，不把异步旧检测解释为同帧结果。

## 验证与实机验收

已通过本地真实C线程/核心逻辑测试、真实C HTTP/UDP控制器联调、页面脚本DOM契约测试和ARM64严格交叉编译。使用模拟感知报告验证规则，不代表真实侧方检测准确率或RK3568多路帧率已验收。Windows界面工具因无法确认当前浏览器URL而中止，未完成视觉验收。

| 实机观察项 | 预期 |
|---|---|
| 开始与重播 | 保留首帧屏障；重播后上一轮检测和请求消失 |
| 左右车辆 | 对应侧方画面出现车辆框，前方不串用侧方框 |
| 实线请求 | 所选侧为实线时，面板立即有红色禁止提示 |
| 虚线请求 | 显示具体侧方证据／不足原因，不给出安全保证 |
| 结束或断流 | 状态失效，旧框过期，不持续使用旧结果 |
| 性能 | 观察预览FPS和三路结果年龄；新增侧方任务会分摊NPU时间，实际更新频率以实机为准 |

本轮无需再次拿原视频做训练或全面重测前方车道。若部署后前方更新明显变慢，先依据`objects source_size=...`、`objects_multi source=...`和lane日志调整调度，不盲目改模型阈值。
