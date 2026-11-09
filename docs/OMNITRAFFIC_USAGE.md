# 本地 OmniTraffic 场景的用途与使用方式

## 1. 已核对的数据

数据根目录：

```text
F:\Chengdu_Chenghannanlu_easy_fluctuating_commuter_pedestrian\Chengdu_Chenghannanlu\Chengdu_Chenghannanlu_easy_fluctuating_commuter_pedestrian
```

本说明针对这一个本地场景包。样例图像是固定路口摄像机的仿真斜俯视图和鸟瞰图，与车载行车记录仪的视角不同。

| 项目 | 实际内容 |
| --- | --- |
| 场景 | `Chengdu_Chenghannanlu`，信号系统 ID 为 `INT1` |
| 时间步目录 | `0`～`602`，共 603 个；样例 `time_step` 从 1 递增 |
| 高质量图像 | `0.png`、`1.png`、`2.png` 三个方向，以及 `bev.png` 鸟瞰图 |
| 高质量图像尺寸 | 480×480 |
| 高质量图像数量 | 每个视角 601 张，合计 2,404 张 |
| 缺失高质量图像 | 时间步 601、602 四个视角均缺失，仍有 JSON 元数据 |
| 低质量图像 | `low_quality_rgb` 下另一组路口／鸟瞰图，可按用途另行检查 |
| `annotations/0.json` 等 | 入／出道路、车道 ID、车道长度值、车辆车道及位置、当前相位和相位对应道路方向 |
| 全部 annotations | 1,809 份；没有像素掩膜、二维车道曲线、虚实线属性或目标检测框 |
| `3d_vehs.json` | 样例记录车辆仿真坐标、朝向和模型文件名；未找到配套相机投影参数 |
| `state_vector.npy` | 样例形状 `[7,6,7]`、`float32`；包内未附特征维度定义 |
| `step_info.json` | `time_step`、`action`、`can_perform_action` |
| `VQA` | 车道数量、车辆、特殊事件、交通相位等图像问答 |

例如时间步 300 的方向 0 标注包含 `current_phase=1`，车辆的 `lane_id` 和 `lane_position`。相应问答描述了当前绿灯相位。这是结构化交通状态；问答中的 `Lane Detection` 能力标签不等于已经提供车道分割真值。

## 2. 与当前工程的对应关系

| 当前需求 | 这批数据的用法 | 需要补充的内容 |
| --- | --- | --- |
| 训练实线／虚线分割 | 先补图像坐标下的边界与线型标注，作为仿真补充数据 | 当前 `lane_solid` / `lane_dashed` / `other_marking` 监督目标 |
| 确定车载视频的本车左右边界 | 车载真实视频仍是主要验证素材 | 本车视角；本包没有明确的本车摄像机与本车轨迹定义 |
| 检查交通相位变化逻辑 | 按时间步读取 `current_phase` 和 `traffic_phase`，建立结构化场景回放 | 道路方向与当前车辆请求的关联、相位／灯色细节定义 |
| 检查车辆所属车道及运动 | 同一车辆 ID 跨时间步关联 `road_id`、`lane_id`、`lane_position` | 位置单位和速度定义；跨道路切换时的位置不能直接相减 |
| 训练车辆／行人检测 | 补二维目标框或获取可投影的官方标注 | 当前包未提供标准检测框；目录名含 pedestrian 不能替代行人位置标注 |
| 训练图像问答模型 | 使用图片及配对 VQA | 这是另一项模型任务，与当前 LR-ASPP 和 if-else 状态机的输入输出不同 |

当前 `prepare_bdd_lanes.py` 解析 BDD 的 `poly2d` 和线型属性，不能直接读取本包。`in_lanes` 是车道 ID 到数值的映射，不能当成图像像素坐标；`lane_id` 后缀也不能直接当成用户要求的从左到右车道编号。

`action=0/1/2` 与交通相位有关，不能当作本车的左变道／右变道／直行动作标签。`can_perform_action` 也不能映射为本车安全变道许可。`state_vector.npy` 需要官方特征定义，不能按形状自行猜测每一维。

## 3. 现在可以直接使用的工具

工程已增加 `training/inspect_omnitraffic.py`，读取源数据并输出检查报告、按数字时间步排序的预览视频及帧对应清单。输出放在工程目录，源数据不修改。

```powershell
cd 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host'
$omniRoot = 'F:\Chengdu_Chenghannanlu_easy_fluctuating_commuter_pedestrian\Chengdu_Chenghannanlu\Chengdu_Chenghannanlu_easy_fluctuating_commuter_pedestrian'
py -3.11 training\inspect_omnitraffic.py --root $omniRoot
```

已生成的产物：

| 文件 | 用途 |
| --- | --- |
| `datasets/omnitraffic_chengdu_inspection.json` | 数量、字段、缺失帧、相位分布和时间步元数据 |
| `video_decision_runs/omnitraffic_preview/camera_0.mp4` | 方向 0 的 601 帧预览，5 fps，约 120.2 秒 |
| `video_decision_runs/omnitraffic_preview/camera_0.frames.json` | 每个视频帧对应的源图、时间步和当前相位 |
| `video_decision_runs/omnitraffic_preview/camera_bev.mp4` | 鸟瞰图预览 |

播放方向 0 预览：

```powershell
Start-Process -FilePath '.\video_decision_runs\omnitraffic_preview\camera_0.mp4'
```

生成另一个方向的预览：

```powershell
py -3.11 training\inspect_omnitraffic.py --root $omniRoot --preview-video video_decision_runs\omnitraffic_preview\camera_1.mp4 --view 1 --preview-fps 5
```

`--view` 支持 `0`、`1`、`2`、`bev`。工具拒绝覆盖已有预览或报告；重新导出时指定新的输出文件名。预览 fps 只控制观看速度，不是原始仿真采样率；需要时间信息时读取 `.frames.json` 和源 `step_info.json`。

不要把这个俯视预览的 `video_driving.py` 本车边界结果作为识别率评价。现有道路裁剪、摄像头中心和近端参考行按车载视角配置，需要单独为路口视角适配。

## 4. 后续实际路线

若优先解决当前车载视频的车道误检和漏检，继续使用 BDD 权重并补充同摄像头真实标注。本包适合先建立路口交通状态回放，把结构化标注作为测试真值，检查相位关联和状态转换。用真值输入测试状态机与用图片检测后测试整套程序，是两个评价阶段，结果分别记录。

若确实要将本包用于线型分割：选择代表性的图像，人工标注实线、虚线和其他标线的路径；核对静态背景下的标注传播和动态车辆遮挡；编写转换器保持四类顺序、路径监督语义和源身份；再与 BDD／真实车载数据混合微调并在真实视频上验证。只有单一路口和三个固定视角，随机拆分相邻帧会让相同背景同时进入训练与验证。优先按场景留出，单场景试验则按连续时间段划分并保留间隔，同时明确其不能证明跨路口泛化。
