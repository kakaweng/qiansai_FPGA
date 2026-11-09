# Windows 训练与 RK3568 模型部署手册

2026-10-13 用户授权开始车道模型微调。实际执行的轻量语义分割方案、数据匹配审计、独立 CUDA 环境和续训/导出命令见 [LANE_FINETUNING.md](LANE_FINETUNING.md)。本手册中的 YOLOv8 等命令保留为其他模型路线参考。

## 1. 当前工程和平台分工

训练不需要在 RK3568 上进行。本机已检测到 RTX 4060 Laptop GPU、约 8 GB 显存，以及 Python 3.11；现有默认环境的 PyTorch 是 CPU 版，应另建训练环境。

| 工作 | 环境 | 产物 |
| --- | --- | --- |
| 标注、数据划分、GPU 训练、ONNX 导出 | Windows 原生 CUDA | 数据集、`best.pt`、`best.onnx`、导出契约 |
| INT8 校准、ONNX → RKNN | 现有 Ubuntu 虚拟机或 WSL2，x86_64 | `.rknn`、转换日志、模型清单 |
| ARM64 编译、HDC 部署 | 现有 Ubuntu 工具链 | 板端程序与模型包 |
| NPU 精度、时延、温度、连续流和 FPGA 输出 | RK3568 实机 | 实机验收记录 |

普通 VMware 显卡加速不能直接用于 CUDA 训练。训练优先 Windows；可以继续使用现有 Ubuntu 虚拟机做转换，无需另换系统。

**当前车道微调采用 LR-ASPP + MobileNetV3 Large，输出带虚实线属性的边界路径。** 本文后续的 `YOLOv8n-seg` 示例属于可选实例分割路线，与当前训练的语义、标签及输出布局不同。当前 C 服务只支持 YOLOv5 目标检测，新车道输出需要独立解码器和本车边界关联，不能直接替换旧模型文件。

## 2. 第一步：建立 Windows CUDA 环境

在 PowerShell 执行：

```powershell
$projectRoot = 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host'
Set-Location $projectRoot
py -3.11 -m venv .venv-train
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv-train\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu124
python -m pip install -r .\training\requirements-windows.txt
python -c "import torch; print('torch=',torch.__version__); print('cuda=',torch.cuda.is_available()); print('gpu=',torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none')"
```

必须看到 `cuda=True` 和 RTX 4060。这些 CUDA wheel 自带所需 CUDA 运行库，通常只需已有 NVIDIA 驱动，无需另装完整 CUDA Toolkit。固定 PyTorch 2.5.1 也便于使用 YOLOv5 v7.0，避免较新 PyTorch 的默认权重加载方式改变导致旧训练脚本报错。

后续每次打开新窗口：

```powershell
Set-Location 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host'
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv-train\Scripts\Activate.ps1
```

## 3. 第二步：准备和标注数据

### 3.1 按视频/道路分组

使用原始行车视频或实际 HDMI/PCIe 帧，避免使用已叠加识别框、文字的屏幕录制作为主要训练图像。屏幕录制适合分析失败现象。建议从不同道路的视频抽取约 1～3 帧/秒，再对变道、路口、遮挡和灯态切换增加采样；无需把每一帧都放入训练集。

按整段视频或道路划分 `train/val/test`，可先采用约 70%/20%/10%。同一视频的相邻帧必须留在同一分组；验证集、测试集和校准集用途不同，最终测试集不参加训练或量化调参。

```text
pcie_host/
  datasets/
    lane_v1/
      images/train/   images/val/   images/test/
      labels/train/   labels/val/   labels/test/
    traffic_light_v1/
      images/train/   images/val/   images/test/
      labels/train/   labels/val/   labels/test/
```

图像和标签必须同名，例如 `images/train/road_001.jpg` 对应 `labels/train/road_001.txt`。数据集 YAML 已写入 `training/`；如果使用其他路径，修改 YAML 的 `path`。

### 3.2 车道标注

用 CVAT 等工具标注多边形并导出 YOLO segmentation 格式。类别顺序固定为：

| ID | 标签 | 标注内容 |
| --- | --- | --- |
| 0 | `lane_solid` | 实际可见的实线标记区域 |
| 1 | `lane_dashed` | 实际可见的虚线标记区域 |

每一处可见的标线用窄多边形沿标线轮廓标注；虚线按可见的每一段标记标注，后处理再关联成边界。遮挡处不凭空补画。车道编号、本车左右边界和变道事件另作评估记录，不将道路总车道数作为第一版训练类别。

分割标签每行格式：

```text
class_id x1 y1 x2 y2 x3 y3 ...
```

所有坐标归一化到 `[0,1]`。这不是目标检测的 `center_x center_y width height` 格式。

当前 640 输入可能损失远处细线。第一轮先测 640；若困难样本明显失真，再比较裁剪前方道路 ROI、较大输入和专用车道网络，并重新评估 NPU 性能。白天、阴影、反光、夜间、弯道、路口缺线和车辆遮挡都应有样本。

### 3.3 交通灯标注

`training/traffic_light_dataset.example.yaml` 已固定 13 类顺序：红黄绿圆灯、红黄绿左箭头、红黄绿直行箭头、红黄绿右箭头、`unknown_light`。框住实际发光的独立灯面；无法分辨的灯面标未知，遮挡严重且无法可靠定位的样本应复核。

远距离小灯应使用原始较清晰灯 ROI 样本。全图模型若无法分辨箭头，可再采用“灯框定位 + ROI 分类”；车道对应关系和转向许可仍由几何/规则层判断。

## 4. 第三步：在 Windows 训练和验证

### 4.1 车道模型优先

先用 3 个 epoch 检查数据格式和显存，再开始正式训练。在项目根目录执行：

```powershell
yolo segment train model=yolov8n-seg.pt data=training/lane_dataset.example.yaml imgsz=640 epochs=3 batch=4 device=0 workers=0 seed=3568 project=runs/lane name=smoke-v1
yolo segment train model=yolov8n-seg.pt data=training/lane_dataset.example.yaml imgsz=640 epochs=100 batch=4 device=0 workers=0 seed=3568 project=runs/lane name=lane-v1-20261008
yolo segment val model=runs/lane/lane-v1-20261008/weights/best.pt data=training/lane_dataset.example.yaml split=test imgsz=640 conf=0.25 iou=0.45 device=0
```

首次使用 `yolov8n-seg.pt` 会下载预训练权重。显存不足降到 `batch=2`；`workers=0` 便于 Windows 起步排错，环境稳定后再试 2～4。正式训练的结果、`args.yaml`、曲线和 `best.pt` 位于 `runs/lane/lane-v1-20261008/`。

仅用分割 mAP 不足以验收车道：还需记录左右边界误差、当前区域判断、变道事件精确率/召回率、每分钟误报和确认延迟。既有视频的“区域可视覆盖率”不能当作区域准确率。

### 4.2 交通灯模型

```powershell
yolo detect train model=yolov8n.pt data=training/traffic_light_dataset.example.yaml imgsz=640 epochs=100 batch=8 device=0 workers=0 seed=3568 project=runs/traffic name=traffic-v1-20261008
yolo detect val model=runs/traffic/traffic-v1-20261008/weights/best.pt data=training/traffic_light_dataset.example.yaml split=test imgsz=640 conf=0.25 iou=0.45 device=0
```

这个专用模型的 class 9 是 `red_right`，与现有 COCO class 9=`traffic light` 完全不同。它需要单独适配语义绑定和解码器，不能套用现有 `traffic_light.py` 的 class 9 规则。

训练完成后保存环境：

```powershell
python -m pip freeze | Set-Content -Encoding utf8 .\runs\lane\lane-v1-20261008\environment.txt
python -c "import torch,sys; print(sys.version); print(torch.__version__); print(torch.version.cuda); print(torch.cuda.get_device_name(0))" | Set-Content -Encoding utf8 .\runs\lane\lane-v1-20261008\platform.txt
```

## 5. 第四步：ONNX 导出与自动记录

### 5.1 车道分割模型

```powershell
yolo export model=runs/lane/lane-v1-20261008/weights/best.pt format=onnx imgsz=640 batch=1 dynamic=False simplify=False opset=12 device=cpu
python training/record_onnx_model.py --onnx runs/lane/lane-v1-20261008/weights/best.onnx --weights runs/lane/lane-v1-20261008/weights/best.pt --task lane_segmentation --model-version lane-v1-20261008
```

脚本检查 ONNX 静态输入及内嵌标签与 `.pt` 一致，并生成 `best.export.json`，记录版本、权重/ONNX 哈希、标签顺序、输入、各输出张量、预处理和待实现的分割解码器。灯模型同样导出后，将 `--task` 改为 `traffic_light_detection`，路径改为灯模型路径。

导出成功后，还需用同一批固定图片比较 PyTorch 与 ONNX Runtime 的预测，尤其看细线、小灯和箭头；结构检查不代表精度验收。

### 5.2 如果后续微调 YOLOv5 通用目标检测

此路线用于已有车辆/行人检测，不能代替车道分割。为减少环境漂移，固定训练仓库 v7.0 并记录实际 commit：

```powershell
git clone --branch v7.0 --depth 1 https://github.com/ultralytics/yolov5.git .\training\yolov5
python -m pip install -r .\training\yolov5\requirements.txt -c .\training\requirements-windows.txt
git -C .\training\yolov5 rev-parse HEAD
```

标准 YOLOv5 `export.py` 通常输出单个已解码张量，**与当前 C 的三路 NCHW 输出不兼容**。项目已提供 `training/export_yolov5_rknn.py`：

```powershell
python training/export_yolov5_rknn.py --repo training/yolov5 --weights runs/objects/objects-v1/weights/best.pt --output runs/objects/objects-v1/weights/best.onnx --model-version objects-v1
```

此处 `best.pt` 需要先用 YOLOv5 `train.py` 和自己的数据集训练得到。脚本只接受普通 YOLOv5 Detect，导出 3 个已 sigmoid 的 NCHW 检测头，并执行 PyTorch/ONNX Runtime 数值比对。元数据保存**训练后的真实 anchors**，包括 AutoAnchor 的修改。

即使都是 YOLOv5，自定义类别数、标签或 anchors 改变后仍需修改 C/Python 解码配置。不要只替换 `.rknn`。现有 COCO 模型保留用于通用物体检测，可以与车道/灯模型并存。

## 6. 第五步：Ubuntu INT8 转换

使用现有 Ubuntu 虚拟机即可。若改用 WSL2，可按微软 WSL 安装流程安装 Ubuntu 22.04；普通 Ubuntu 22.04 默认 Python 3.10，需匹配 `cp310`、`linux_x86_64` 的 RKNN wheel。

以下在 Ubuntu 终端执行。先将工程、`best.onnx`、相邻的 `best.export.json` 和约 200～500 张有代表性的原始 1920×1080 图片复制到 Ubuntu；数量是起步建议，代表性更重要。例子使用 `$HOME/pcie_host` 和 `$HOME/calibration-source`。

```bash
sudo apt-get update
sudo apt-get install -y python3-venv
cd "$HOME/pcie_host"
python3 -m venv .venv-rknn
source .venv-rknn/bin/activate
python -m pip install --upgrade pip
python -m pip install /实际SDK目录/rknn_toolkit2-2.3.2-cp310-cp310-linux_x86_64.whl
python -c "from rknn.api import RKNN; print('Toolkit import OK')"
```

wheel 从瑞芯微官方发布包取得；路径需替换为实际位置。转换工具采用 2.3.2，与工程记录的板端 runtime 2.3.2 配套。旧模型究竟由哪个 Toolkit 版本生成尚未核实，已在 baseline 清单中保留未知，避免用 runtime 版本冒充转换版本。

准备校准图：

```bash
python training/prepare_calibration.py \
  --images "$HOME/calibration-source" \
  --output "$HOME/calibration-lane-v1" \
  --interpolation bilinear
```

脚本复现 RGB565 的颜色精度，缩放到 640×360，上下各补 140 行、填充值 114，输出 640×640 PNG、校准列表和每张图片的哈希。YOLOv8 示例使用双线性；YOLOv5 与现有 C 对齐时使用 `--interpolation nearest_center`。校准集也应包含阴影、夜间、细线、遮挡和远处灯。

**在 Ubuntu 重新生成校准列表**，不要直接使用 Windows 盘符路径。转换命令：

```bash
python training/convert_rknn.py \
  --onnx runs/lane/lane-v1-20261008/weights/best.onnx \
  --dataset "$HOME/calibration-lane-v1/calibration.txt" \
  --output models/lane-v1-20261008-rk3568-int8.rknn \
  2>&1 | tee models-lane-v1-conversion.log
```

脚本校验 ONNX 和校准图片哈希，固定目标 `rk3568`、`mean=0`、`std=255`、INT8 build，检查 API 返回码，并生成同名 `.manifest.json`。标准 YOLO 训练输入为 RGB `[0,1]`，新模型的归一化在 RKNN 转换中完成；板端仍提交 UINT8。不能从“C 不除以 255”推断 Toolkit 的 std 应为 1。

输出清单记录：模型/ONNX/权重版本和哈希、标签顺序、预处理、输出布局、解码类型和阈值、Toolkit 配置、校准集哈希。实际 RKNN 张量的 dtype、量化类型、zero point、scale 需上板查询，脚本将这些字段保留为待验收。

若转换报不支持算子或量化精度不达标，保留日志后调整导出/模型；生成 `.rknn` 不等于实机可用。清单状态会保持 `converted_pending_decoder_and_board_acceptance`。

## 7. 现有基线清单与防错检查

| 文件/功能 | 已实现内容 |
| --- | --- |
| `model_manifest.json` | 当前 COCO YOLOv5 的版本、标签、输入、输出、量化、解码器和来源限制 |
| `labels/coco80.txt` | 从 0 开始的 80 类标签顺序及 SHA256 |
| `validate_model_manifest.py` | 对比 C/Python 实际标签、类别数、anchors、阈值和 6 个实现文件指纹 |
| 新模型 `*.export.json` | Windows 导出时记录权重、ONNX、标签和输出解释 |
| 新模型 `*.manifest.json` | Ubuntu 转换时记录 RKNN 哈希、量化配置和待完成验收项 |

在项目根目录执行：

```powershell
python validate_model_manifest.py
```

该校验器支持当前 80 类 baseline，并会拒绝未审核的类别、输出和源码变化。源码指纹按 UTF-8/LF 归一化计算；修改感知/解码代码后，应审核变化并升级清单，不能仅刷新哈希以跳过审核。它不是分割模型的验收器。

旧 `.rknn` 文件目前位于原 Ubuntu/板端包，未在本工程取得，所以 `artifact.sha256=null`、`status=reference_only`。旧模型转换 mean/std、校准集也无法追溯，已明确标为未知。取得文件后计算并填入清单：

```powershell
Get-FileHash '实际模型路径\yolov5s-640-640.rknn' -Algorithm SHA256
python validate_model_manifest.py --model '实际模型路径\yolov5s-640-640.rknn' --require-artifact
```

第二条命令要求先把第一条得到的实际哈希填入 `artifact.sha256`；缺少模型或哈希会失败。默认校验通过仅代表源码与契约一致，不代表已连接 RK3568 验证。

## 8. RK3568 部署与验收

沿用本工程的 OpenHarmony/HDC 和 ARM64 glibc loader 链路。重启和服务启动命令仍见 `COLOR.md` 第 3～5 节。新模型先使用新文件名，保留旧模型和旧可执行文件。

| 顺序 | 验收 | 记录 |
| --- | --- | --- |
| 1 | 适配新解码器、语义 ID、分割/灯结果协议、预览显示 | 对应清单版本和代码版本 |
| 2 | HDC 发送模型/程序，核对源端与板端 SHA256 | 模型身份和文件完整性 |
| 3 | 通过板端 `--inspect` 查询实际张量 | shape/layout/dtype/qnt_type/zp/scale、runtime 版本 |
| 4 | 固定样本比较 PyTorch、ONNX、RKNN、板端 | 类别、边界/mask、置信度、量化误差 |
| 5 | 连续真实 RGB565/PCIe 视频 | 变道/灯态误报漏报、延迟、有效 FPS、温度、稳定性 |
| 6 | FPGA/LED/按键行为闭环 | 同帧、同版本、状态超时和告警原因 |

已设置 `HDC/TARGET` 后，Ubuntu 中发送模型的形式是：

```bash
sudo "$HDC" -t "$TARGET" file send \
  models/lane-v1-20261008-rk3568-int8.rknn \
  /data/local/tmp/rknn-yolov5-test/model/lane-v1-20261008-rk3568-int8.rknn
```

当前协议 v4 没有传输模型指纹。当前实现通过清单、源码校验和部署哈希减少解释偏差；后续应补模型身份握手，才能让预览自动拒绝板端加载错误版本。现有 `pcie_rknn_probe_rgb565` 不包含新车道 mask 解码器，完成第 1 项后才能用新模型运行连续服务。

训练后的车道总数继续作为辅助。停车、转向、变道许可还需结合当前区域、左右边界、虚实线、邻道占用、灯箭头与车道绑定和导航目标；训练模型本身不自动完成这些行为规则。

## 9. 本轮交付边界

旧训练指南的脚本与契约保留。2026-10-13 开始的车道微调进度以 `runs/lane_style/bdd_lraspp_v1_20261013/run.json` 为准；数据和当前训练路线见 `LANE_FINETUNING.md`。RKNN 转换、目标摄像头精度、预览接入和板端验收需分别完成，不能用训练指标代替。
