# Windows 车道线型首轮微调

## 1. 实际采用的方案

本轮训练采用 Torchvision 的 `LR-ASPP + MobileNetV3 Large`，加载官方 `COCO_WITH_VOC_LABELS_V1` 分割预训练权重，保留特征提取及兼容的分割参数，将两个类别输出层替换为四类。模型约 322 万参数。预训练数据不是 BDD100K；本轮才使用本机 BDD 标注进行车道适配。

| ID | 标签 | 含义 |
| --- | --- | --- |
| 0 | `background` | 非标注边界区域 |
| 1 | `lane_solid` | 沿道路方向、标注为实线的边界路径 |
| 2 | `lane_dashed` | 沿道路方向、标注为虚线的边界路径 |
| 3 | `other_marking` | 路缘、斑马线以及横向标线，不能直接用作可跨越边界 |
| 255 | 忽略 | 不确定线型、类别交叉，以及动态目标框中被遮挡的标注路径 |

BDD 的 `poly2d` 是边界路径，而非精确可见油漆轮廓。本轮把路径转换成输入坐标中宽 5 像素的监督目标；虚线类别路径可能贯穿虚线间隙。因此输出语义是“带线型的边界路径”，不能再用预测掩膜是否连续来判断虚实线。

## 2. 数据核对

本机数据位置为 `E:\BaiduNetdiskDownload\车牌数据集\BDD100K`。其中图片为旧版 10k 子集，JSON 标注为旧版 100k，不能按目录名字直接一一配对。

| 项目 | 数量／处理 |
| --- | --- |
| 原始图片 | 10,000 张 |
| 图片与 JSON 文件名匹配 | 4,260 张 |
| 没有匹配标注 | 5,740 张，排除，不作为背景样本 |
| 匹配的官方 test | 830 张，保留，不参加训练或选模型 |
| 匹配但没有车道路径标注 | 145 张，排除 |
| 最终训练／验证 | 2,846／439 张 |
| 划分依据 | JSON 原始 train/val 划分，不随机混合图片目录 |

生成的数据在 `datasets/bdd_lane_style_v1/`，包含裁剪图像、四类 mask、标签预览和 `dataset.json`。清单记录来源、源图/JSON/生成图/mask 的 SHA256、类别顺序及划分。原始 BDD 文件不修改。

输入裁剪原图下方 65%（从 `round(height*0.35)` 到底部），双线性缩放至 640×384。训练采用 RGB、除以 255 后使用 ImageNet mean/std。首轮使用颜色增强和同步水平翻转；边界身份、左右归属仍需后处理。

## 3. 独立训练环境

训练环境 `.venv-lane` 使用 Python 3.11、Torch 2.5.1+cu124、Torchvision 0.20.1+cu124。现有预览的默认 Python 环境继续使用原有依赖。

如果需要重新建立环境，在工程根目录执行：

```powershell
py -3.11 -m venv .venv-lane
.\.venv-lane\Scripts\python.exe -m pip install --upgrade pip --index-url https://pypi.org/simple
.\.venv-lane\Scripts\python.exe -m pip install -r training\requirements-lane.txt --index-url https://pypi.org/simple --extra-index-url https://download.pytorch.org/whl/cu124
.\.venv-lane\Scripts\python.exe -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

最后必须显示 CUDA 可用。无需激活脚本，也无需另装完整 CUDA Toolkit。

## 4. 数据生成、训练与续训

所有命令的工作目录为 `F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host`。

```powershell
.\.venv-lane\Scripts\python.exe training\prepare_bdd_lanes.py --root 'E:\BaiduNetdiskDownload\车牌数据集\BDD100K' --output datasets\bdd_lane_style_v1
.\.venv-lane\Scripts\python.exe training\verify_lane_training.py
.\.venv-lane\Scripts\python.exe training\train_lane_style.py --dataset datasets\bdd_lane_style_v1 --output runs\lane_style\bdd_lraspp_v1_20261013 --epochs 5 --batch 4 --workers 2
```

数据生成器拒绝覆盖已有 `dataset.json`。首轮是完整配对训练集的 5 个 epoch，用于取得首份真实微调权重和验证结果；不代表已收敛。训练器使用混合精度、AdamW、主干学习率 0.0001、头部学习率 0.001、加权交叉熵及车道 Dice，保留预训练 BatchNorm 运行统计。每轮保存 `last.pt`，按验证集实/虚两类平均 IoU 保存 `best.pt`。

首轮结束后，在同一数据上继续到总计 25 个 epoch：

```powershell
.\.venv-lane\Scripts\python.exe training\train_lane_style.py --dataset datasets\bdd_lane_style_v1 --output runs\lane_style\bdd_lraspp_v1_20261013 --epochs 25 --batch 4 --workers 2 --resume runs\lane_style\bdd_lraspp_v1_20261013\last.pt
```

`--epochs 25` 是总轮数，不是额外增加 25 轮。不要同时启动两份训练写入同一个目录。恢复模型、优化器和混合精度状态；会重新设置随机种子，不承诺与未中断运行逐位一致。

## 5. 查看进度与产物

```powershell
Get-Content runs\lane_style\bdd_lraspp_v1_20261013\metrics.jsonl -Tail 1
Get-Content runs\lane_style\bdd_lraspp_v1_20261013\run.json
```

| 产物 | 用途 |
| --- | --- |
| `run.json` / `environment.txt` | 模型版本、来源、数据身份、超参数、环境及完成状态 |
| `initial_validation.json` | 换成新随机分类头后的初始值，不是当前 YOLOP 基线 |
| `metrics.jsonl` | 每轮损失、验证集类别 IoU/precision/recall、显存及耗时 |
| `best.pt` / `last.pt` | 最优验证权重／最后一轮续训检查点 |
| `best_validation.json` | 最优验证轮次及像素指标 |
| `validation_previews/` | 原图、监督目标、预测三列对比 |

指标仅衡量 BDD 路径栅格标签的像素匹配。它们不等于本车边界识别成功率、变道许可正确率或实际视频准确率。仍需目标摄像头数据和人工真值验证。

## 6. ONNX 导出与解释契约

```powershell
.\.venv-lane\Scripts\python.exe training\export_lane_style.py --weights runs\lane_style\bdd_lraspp_v1_20261013\best.pt --output runs\lane_style\bdd_lraspp_v1_20261013\best.onnx
```

导出固定 FP32 `[1,3,384,640]` 输入，输出 `[1,4,384,640]` logits。脚本检查 ONNX，并对 6 张验证图执行 PyTorch/ONNX Runtime 数值和类别图比较。相邻 `best.manifest.json` 记录版本、权重和 ONNX 哈希、标签顺序、裁剪/缩放/归一化、输出布局、softmax 解码、量化状态和待完成项。

续训后如果 `best.pt` 更新，必须再次执行导出命令，同步 `best.onnx` 和 `best.manifest.json`。不能将旧 ONNX 与新权重或新验证指标混用；以契约中的权重轮次和 SHA256 确认对应关系。

当前没有 INT8 量化，也未进行 RKNN 转换。使用本轮预处理的模型不能直接套用旧 YOLOv5/YOLOP 的预处理或解码器。

## 7. 接入边界

`video_driving.py` 已默认接入第 24 轮最佳 ONNX，使用 `windows_lane_style.py` 复现裁剪及归一化、解析类别图、按局部曲线组织边界、确认本车左右归属，并通过置信度拒绝和连续确认接入现有状态机。启动方式、颜色图例、阈值和检查命令见 [`LANE_PREVIEW_WINDOWS.md`](LANE_PREVIEW_WINDOWS.md)。这是模型接入与左右边界初版，尚未进行目标视频效果验收；新模型输出虚线也不等于变道安全。

用户提供的三个视频继续保留为目标场景素材。本轮没有对它们再次进行旧检测测试，也没有用未经人工确认的旧推理结果生成训练标签。补充同摄像头的人工标注后可以开展第二阶段微调。

## 8. 已完成训练与续训结果

2026-10-13 首轮完成上述完整配对数据的 5 个 epoch，随后从 `last.pt` 续训至总计 25 轮。当前最优验证模型来自第 24 轮，产物为 `runs/lane_style/bdd_lraspp_v1_20261013/best.pt`、`best.onnx` 和 `best.manifest.json`。ONNX 与契约已按第 24 轮权重重新导出，并通过 6 张验证图的 PyTorch/ONNX 数值及类别图一致性检查。

| 验证指标 | 首轮最佳：第 4 轮 | 续训最佳：第 24 轮 |
| --- | ---: | ---: |
| 实线 IoU | 0.494888 | 0.524663 |
| 虚线 IoU | 0.357746 | 0.429529 |
| 实线／虚线平均 IoU | 0.426317 | 0.477096 |
| 实线像素 precision／recall | 0.615583／0.716240 | 0.614035／0.782832 |
| 虚线像素 precision／recall | 0.450318／0.635072 | 0.597643／0.604269 |

实线／虚线平均 IoU 增加 0.050779，虚线 precision 提高但 recall 略降，不能据此认定所有场景的漏检都改善。第 25 轮平均 IoU 为 0.472807，低于第 24 轮，因此推理使用 `best.pt` 或其对应 ONNX，`last.pt` 保留用于续训。

这些数值来自 439 张 BDD 验证图及路径栅格标签，不等于本车左右边界识别成功率或变道许可正确率。后半段验证平均 IoU 在约 0.46～0.48 间波动；当前已完成预览及左右边界初版接入，下一步核对目标视频上的左右归属和虚实线判断，再针对目标摄像头补充人工标注。首轮的 8 项数据/模型/损失回归测试通过；实际训练耗时、每轮曲线及来源清单保存在该 run 目录。
