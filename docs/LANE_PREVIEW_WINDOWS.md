# Windows 微调车道模型预览

## 1. 启动

已完成新模型接入和本车左右边界定位。`video_driving.py` 默认使用第 24 轮最佳 LR-ASPP 车道线型模型；车辆、行人和交通灯检测仍由现有目标检测与信号模块处理。

在 PowerShell 执行：

```powershell
cd 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host'
py -3.11 video_driving.py 'D:\buduinetdiskdownload\video_20260929_112507.mp4' --lane-source finetuned --backend directml --playback realtime --sample-fps 8 --width 1280
```

本机默认 Python 3.11 已有 `onnxruntime-directml`。训练用 `.venv-lane` 安装的是 CPU ONNX Runtime，预览建议使用上述 `py -3.11`，以调用 DirectML GPU。显式 `--backend directml` 初始化失败会报错；`--backend auto` 可以退回 CPU 并打印提示。

启动时必须看到 `Lane source: finetuned`、`best epoch: 24` 和 `lanes=onnxruntime-directml`，模型路径应为 `runs\lane_style\bdd_lraspp_v1_20261013\best.onnx`。

不指定视频路径会弹出文件选择窗口。可以选择另外两个视频。若需要在完整同帧画面中仔细观察，把命令中的 `--playback realtime` 换为 `--playback offline`；该模式会等待每个采样帧分析完成，播放速度受推理速度影响。

## 2. 画面含义

| 画面元素 | 含义 |
| --- | --- |
| 橙色区域 | 模型预测的实线类别路径 |
| 蓝色区域 | 模型预测的虚线类别路径 |
| 紫色区域 | 模型预测的其他标线，包括路缘、斑马线和横向标线 |
| 带 `LEFT` / `RIGHT` 的曲线 | 当前参考位置最近的本车左／右边界候选 |
| 本车区域填充 | 左右曲线构成的本车行驶区域；绿色为连续确认，黄色为确认中 |
| 下方左右边界文本 | 连续确认后的线型、模型分数、确认中／暂存／未定位的原因 |
| 下方模型文本 | 当前权重轮次、车道推理耗时和颜色图例 |

颜色叠加来自原始类别 argmax，用来观察模型误检和漏检；本车边界定位另有置信度、类别差值、曲线长度、遮挡和几何检查。存在颜色区域不等于已经确认本车边界。虚线监督是边界路径，蓝色预测可以连续，解码器按类别判断虚实。

实时模式中左侧是当前播放帧，右侧是最近完成分析的对应帧；颜色、边界和目标框都绘制在右侧分析帧上，下方显示分析落后时间。结果过期时不提供新的变道判断。

| 按键 | 操作 |
| --- | --- |
| 空格 | 暂停／继续 |
| `A` / `D` | 对当前有效结果发起一次左／右变道判断 |
| `K` | 清除变道请求 |
| `S` | 保存画面及对应 JSON |
| `R` | 重播并清除识别历史 |
| `T` | 重新选择信号灯搜索区域 |
| `Q` | 退出 |

实线禁止跨越。虚线允许进入其他条件检查；邻道范围、车辆运动或侧后方条件未确认时，不能据此断言变道安全。暂存的虚线只供显示，不能批准新的变道请求。

## 3. 当前解码与可调参数

预处理严格按 `best.manifest.json`：从 `round(height*0.35)` 裁剪到图像底部，PIL 双线性缩放至 640×384，BGR 转 RGB，除以 255 后进行 ImageNet 归一化。对 `[1,4,384,640]` logits 执行 softmax；只将类别 1、2 用作车道边界候选，类别 3 不参与本车边界定位。

按多个图像行关联路径、拟合二次曲线、映射回原图，再在参考行按本车中心选左右边界。车辆及行人框内的像素不作为边界证据。禁止将远处短路径大幅外推为近处边界。

| `perception.lane_style` 可选配置 | 初始值 | 作用 |
| --- | ---: | --- |
| `pixel_confidence` | 0.45 | 候选像素最低 softmax 分数 |
| `pixel_margin` | 0.10 | 第一、第二类别分数的最低差值 |
| `style_fraction` | 0.70 | 路径中实线或虚线类别的最低占比 |
| `reference_row` | 0.84 | 原图高度比例，用于左右归属；允许 0.78～0.94 |
| `confirm_frames` | 3 | 同一侧边界线型确认所需的有效观测数 |
| `hold_ms` | 350 | 丢失后仅供显示的短暂保留时长 |

这些是初版默认值，尚未在用户视频上做目标摄像头标定。分数不代表经过校准的识别正确率。几何确认沿用 `perception.lane_detection.confirm_frames`，默认 2。

## 4. 产物与检查

每次运行写入 `video_decision_runs\时间戳_标识\`，包括观测 JSONL、运行摘要、模型哈希、实际解码阈值与代码哈希。截图包含相同分析结果。默认不保存完整标注视频；需要时使用 `--save-video`。

```powershell
py -3.11 verify_lane_style_integration.py
py -3.11 verify_lane_style_integration.py --smoke --backend directml
```

第一条检查类别顺序、预处理、曲线归属、独立单侧确认、低置信度拒绝、遮挡、历史过期、重播和变道反馈。第二条额外用一张 BDD 原始验证图运行真实目标检测和新车道模型，并保存 `runs\lane_style\bdd_lraspp_v1_20261013\integration_preview.jpg` 及其 JSON。这些检查不代表实际视频识别率验收。

若要明确选择旧原型，使用 `--lane-source yolop`。自定义车道 ONNX 使用 `--lane-model`；微调模型必须有相邻的 `.manifest.json`，且输入、输出、标签和 ONNX SHA256 与解码器对应。新权重续训后仍须重新导出。当前未进行 RKNN 转换或板端验收。
