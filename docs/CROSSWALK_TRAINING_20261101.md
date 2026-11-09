# 独立斑马线模型：首轮微调与视频复核

后续连续验证已完成：10 个片段、865 次推理及可播放对比视频；车内区域过滤、短时保持改进与行人关系的实际限制见 [CROSSWALK_VALIDATION_20261101.md](CROSSWALK_VALIDATION_20261101.md)。模型权重未变化，自动礼让规则尚未验收。

日期：2026-11-01。工程目录：`F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host`。

本轮目标是为 HDMI 前视的实时 2.5D 和行人礼让判断补充独立的斑马线感知结果。使用 Mapillary Vistas 的像素标注微调轻量模型；PIE 用于连续过街场景核对，不把它的行人框或斑马线框当作像素分割真值。

**训练结果**

已在 Windows GPU 上完成 12 轮微调，保留第 11 轮最佳权重。训练循环及逐轮验证共约 498 秒（不含下载、解压、数据准备、启动核验和导出）。原车道权重的 SHA256 在训练前后相同。

| 指标／检查 | 结果 | 口径 |
| --- | ---: | --- |
| 最佳轮次 | 11 / 12 | 按验证集 IoU 选择，未直接取最后一轮 |
| 斑马线像素 IoU | **58.57%** | 完整 2,000 张官方验证图，忽略 255；AMP FP16 验证 |
| 前景精确率 Precision | **80.08%** | 被预测为斑马线的有效像素中，真值也为斑马线的比例 |
| 前景召回率 Recall | **68.56%** | 真值斑马线有效像素中，被找回的比例 |
| 背景像素误检率 | 0.141% | FP/(TN+FP)，不是图片误报率或礼让误报警率 |
| 无斑马线标签图诊断 | 95 / 1,389 | 这些图片中，预测斑马线超过有效图像面积 0.1% 的张数；阈值仅用于诊断 |
| ONNX 检查 | 6 / 6 通过 | 4 张正例、2 张负例，FP32 PyTorch 与 CPU ONNX Runtime 对比 |
| ONNX 最大 logit 绝对误差 | 0.00005341 | 这 6 个样例中的最大值 |
| ONNX 分类图不一致比例 | 0 | 仅指这 6 个验证样例，不外推至全部输入 |

第 1 轮 IoU 为 49.81%，第 6 轮为 57.99%，第 11 轮为 58.57%，第 12 轮为 58.33%。当前是可以继续做视频接入试验的初版，并非高召回或实机验收版本。上述均为像素级指标，不能读作“58.57% 的路口识别成功”或“80.08% 的礼让准确率”。

权重：[best.pt](runs/crosswalk/mapillary_lraspp_v1_20261101/best.pt)（37.14 MiB，含续训状态）；导出：[crosswalk.onnx](runs/crosswalk/mapillary_lraspp_v1_20261101/crosswalk.onnx)（12.28 MiB）；接口约定：[crosswalk.manifest.json](runs/crosswalk/mapillary_lraspp_v1_20261101/crosswalk.manifest.json)；逐轮记录：[metrics.jsonl](runs/crosswalk/mapillary_lraspp_v1_20261101/metrics.jsonl)。最终权重／ONNX／数据 manifest／原车道权重哈希、最佳轮次、导出记录和 59 帧产物完整性检查通过：`CROSSWALK_FINAL_ARTIFACT_CHECK_PASS`。

**跨视频实际效果与剩余问题**

已经对 8 段视频的 59 帧做离线预测，并查看全部 8 张联系图；另查看验证集正／负样例及两个误检帧的原分辨率叠加图。视频预测使用 FP32 PyTorch、前景概率 >0.5，尚未使用道路 ROI、连通域过滤或时序保持。图册：[domain_review/index.html](runs/crosswalk/mapillary_lraspp_v1_20261101/domain_review/index.html)。黄色是模型预测，不是人工真值。

| 样本 | 目视观察 | 对后续的影响 |
| --- | --- | --- |
| 原视频 `112507`，10 帧 | 0、23、25、38、40 秒的斑马线有对应预测；抽查直行普通标线画面基本没有成片预测 | 可用于第一版前视区域显示回归，尚不能证明整段连续稳定 |
| 原视频 `113421`，7 帧 | 前车遮挡时只得到可见部分；2、4、8 秒斑马线有预测 | 需要区域跟踪与短时保持，不能把被遮挡部分当作即时可见证据 |
| 原视频 `113539`，10 帧 | 多个路口有对应预测；12 秒仪表台下沿出现小块误检，部分边界向车辆外缘扩张 | 优先使用已选视频区域／道路有效区域排除车内部分，并保留困难负例 |
| Waymo 两个白天场景，共 12 帧 | 近处横道带有可见预测，目标遮挡和远处会造成断裂／缺失 | 不直接对所有碎片求大凸包，避免把不同方向横道或遮挡目标错误连接 |
| Waymo 夜间场景，6 帧 | 预测面积接近 0，所查画面未确认清楚的条纹式斑马线正例 | 不能据此宣称夜间召回率合格，也不能直接按漏检计数 |
| PIE 两段视频，共 14 帧 | 部分路口有预测，但多个画面下方白色车头出现明显误检；video_0002 的 230 秒最直观 | 先排除车头／仪表台，再补这类负例；未经处理不能直接由 mask 触发停车 |
| Mapillary 验证预览 | `val_04`、`val_05` 的远处／褪色小区域漏检明显；`val_07` 相对标签存在误检 | 下一轮补困难场景、验证类别定义，并扩充训练子集 |

具体误检图：[PIE 230 秒](runs/crosswalk/mapillary_lraspp_v1_20261101/domain_review/frames/pie_set01_0002_230000.jpg)、[本地 113539 的 12 秒](runs/crosswalk/mapillary_lraspp_v1_20261101/domain_review/frames/local_113539_012000.jpg)。Mapillary 的 `void`／ego 区域在本轮被忽略，不能假定模型在这些区域学会了背景；当前报告将其保留为原始失败样例，没有用裁剪后截图掩盖问题。

这些视频没有本任务的逐像素真值。本轮只报告定性发现，不计算视频斑马线准确率、完整过街区域精度或礼让成功率；抽帧检查也不等同连续流畅度、时延和短时跳变验收。

**数据及标签**

| 项目 | 本轮实际使用 |
| --- | --- |
| Mapillary 版本 | Vistas v2.0，读取本地 `config_v2.0.json`，并实际扫描 18,000 张训练标签和 2,000 张验证标签 |
| 首轮训练集 | 2,400 张：原标签含斑马线 1,400 张，普通道路标线等困难负例 800 张，其他负例 200 张 |
| 验证集 | 保留全部 2,000 张官方验证图；不使用 testing 图片训练或调参 |
| 缩小后的正例图 | train 1,397 张、val 611 张；有 4 张极小目标经最近邻缩小后消失，样本仍保留 |
| 标签顺序 | `0=background`、`1=crosswalk_zebra`；训练标签 `255=ignore`，不是输出类别 |
| 原始正例类别 | 45 `marking--discrete--crosswalk-zebra`、56 `marking-only--discrete--crosswalk-zebra` |
| 忽略区域 | 官方 `evaluate=false` 类别、`void--*`，以及语义不同的 14 `construction--flat--crosswalk-plain` |
| 普通标线 | 实线、虚线、箭头等属于背景负例，训练模型区分其与斑马线 |
| 划分检查 | 样本 ID 及源图精确 SHA256 在 train/val 之间不重复；没有声称完成近似图或同场景去重 |
| 可追溯记录 | `datasets/mapillary_crosswalk_v1/dataset.json` 记录源图、源标签、处理后图像与标签的路径及哈希；`summary.json`、`validation.json` 记录统计和检查结果 |

模型学习的是 **Mapillary 官方标注的斑马线区域像素**。目视抽查显示标注可包含条纹间隙，不能称为“仅白色条纹像素”，也不能直接等同真实地面上完整、带米制坐标的过街区域。普通 plain crosswalk 暂时忽略；无斑马线横穿行人仍需独立的道路关系判断。

先用固定子集建立可复核的训练、导出与视频检查流程；这次没有声称已对 18,000 张训练图完成全量微调。完整验证集用于选最佳轮次，属于验证集，不作为独立测试集宣传结果。

**训练设置与部署约定**

| 项目 | 配置 |
| --- | --- |
| 模型 | Torchvision LR-ASPP + MobileNetV3-Large，3,218,308 个参数 |
| 初始化 | 从 `runs/lane_style/bdd_lraspp_v1_20261013/best.pt` 迁移 backbone 与兼容的 context 层；重新初始化两个类别输出层 |
| 训练设备 | Windows，NVIDIA GeForce RTX 4060 Laptop GPU，CUDA 环境 `.venv-lane` |
| 版本 | PyTorch 2.5.1+cu124、Torchvision 0.20.1+cu124；完整依赖记录在运行目录 `environment.txt` |
| 输入 | 全帧等比例缩放并补边至 640×384；RGB，PIL bilinear，补边 RGB=114；mask 最近邻，补边=255 |
| 归一化 | RGB/255 后使用 ImageNet mean/std；参数写入 manifest |
| 训练 | 最多 12 轮，batch=8、workers=2、AMP FP16；冻结 BatchNorm 运行统计，梯度裁剪 5 |
| 优化器 | AdamW；backbone 学习率 1e-4、head 1e-3；cosine 衰减至初始值的 0.1 |
| 损失 | 权重 `[0.3, 1.0]` 的交叉熵 + 0.5×前景 Dice，忽略 mask=255 |
| 增强 | 轻量亮度／对比度／色彩增强、水平翻转 |
| 选权重 | 按完整验证集的斑马线像素 IoU 选择 `best.pt`；`last.pt` 用于断点续训 |
| 输出约定 | `crosswalk_logits`，NCHW `[1,2,384,640]`，raw logits；softmax 后取前景，去补边并映射原图 |
| 默认检查阈值 | 前景概率 >0.5；尚未做板端工作阈值标定 |
| 量化 | 导出 FP32 ONNX；当前没有 INT8 量化，也没有生成 RKNN |

`crosswalk.onnx` 及同名 `.manifest.json` 用于后续独立接入。manifest 保存模型版本、权重轮次和哈希、标签顺序、预处理、输出布局、解码器、量化状态及一致性结果，防止 Windows 与板端解释不同。

**PIE 的用途**

| 素材 | 已核对的内容 | 用途 |
| --- | --- | --- |
| `set01/video_0001.mp4` | 10 分钟、1080p/30FPS；21 条行人轨迹，轨迹级 crossing=1 为 0 | 路边目标、误报警及遮挡开发；原记录见 [PIE_AUDIT_20261031.md](PIE_AUDIT_20261031.md) |
| `video_0002.mp4`，本轮按 set01 核对 | 10 分钟、1080p/30FPS；36 条行人轨迹，其中 crossing=1 为 18，crossing=0 为 4，irrelevant 为 14；5 条斑马线框轨迹 | 过街正例、遮挡和连续轨迹开发；标签与画面抽查对应，见 [新视频筛查图册](dataset_audit/pie_set01_video0002_20261101/index.html) |

video_0002 的逐帧行人记录为 not-crossing 22,857、crossing 4,067、crossing-irrelevant 245。这些是轨迹／框记录数，不是模型检出数或独立交通事件数。没有标注的帧不能自动当负例。当前未找到相机标定 JSON 和本车传感器 metadata，不阻塞本轮像素分割训练；本轮没有训练行人意图预测模型。

**产物和复现入口**

| 文件 | 用途 |
| --- | --- |
| [运行目录](runs/crosswalk/mapillary_lraspp_v1_20261101) | `best.pt`、`last.pt`、`run.json`、`metrics.jsonl`、`best_validation.json` |
| [训练脚本](training/train_crosswalk.py) | GPU 微调、完整验证、保存最佳权重及断点 |
| [数据准备脚本](training/prepare_mapillary_crosswalk.py) | Vistas 类别核对、样本选择、图像／标签处理与溯源 |
| [导出脚本](training/export_crosswalk.py) | 静态 ONNX 与 PyTorch/ONNX Runtime 一致性检查、manifest |
| [跨视频检查脚本](training/review_crosswalk_domain.py) | 本地行车、Waymo、PIE 原图预测叠加与联系图 |
| [训练契约测试](tests/test_crosswalk_training.py) | 补边忽略、离散标签、ignore 的损失／梯度、全忽略输入、指标口径；5 项已通过 |

PowerShell 先进入工程目录，后续独立新一轮训练使用新的输出目录名，避免覆盖本轮成果：

```powershell
Set-Location 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host'

# 以下命令复现本轮设置；再次训练时改为未使用过的 output 目录。
& '.\.venv-lane\Scripts\python.exe' -u training/train_crosswalk.py `
  --dataset datasets/mapillary_crosswalk_v1 `
  --output runs/crosswalk/mapillary_lraspp_v1_reproduce `
  --initial-lane runs/lane_style/bdd_lraspp_v1_20261013/best.pt `
  --epochs 12 --batch 8 --workers 2
```

如训练意外中断，使用相同 dataset、initial-lane、output、epochs、batch 等参数，并添加 `--resume 原运行目录/last.pt`。保留优化器和 AMP scaler；当前断点续训不保证与未中断运行逐位完全一致。

```powershell
# 对已经完成的本轮权重重新导出／生成图册，无需重新训练。
& '.\.venv-lane\Scripts\python.exe' -u training/export_crosswalk.py `
  --weights runs/crosswalk/mapillary_lraspp_v1_20261101/best.pt `
  --output runs/crosswalk/mapillary_lraspp_v1_20261101/crosswalk.onnx

& '.\.venv-lane\Scripts\python.exe' -u training/review_crosswalk_domain.py `
  --weights runs/crosswalk/mapillary_lraspp_v1_20261101/best.pt `
  --output runs/crosswalk/mapillary_lraspp_v1_20261101/domain_review
```

**下一阶段**

先在 Windows 离线视频中使用按视频配置的有效道路区域排除车头／仪表台，将斑马线结果与现有行人跟踪、本车道路区域关联，并验证“路边有人”和“进入本车冲突区”的区别；加入区域平滑和限时保持后再送入 2.5D。不能对所有视频统一裁掉固定高度而不检查近处斑马线。对本地视频补少量独立区域／事件真值，以片段或路口划分，避免用相邻帧随机拆分后高估效果；下一轮训练扩充远处、褪色、遮挡样本及车头等困难负例。之后再验证 RKNN 转换及与现有车道、YOLO 的 NPU 调度耗时，决定运行频率或是否需要蒸馏／合并模型。

本轮训练产物尚未接入已有预览或 RK3568。新增斑马线 mask 不能单独决定停车，也不代表自动获得行人意图、真实距离或驾驶许可。
