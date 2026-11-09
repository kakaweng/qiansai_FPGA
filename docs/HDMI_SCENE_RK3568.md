# RK3568 HDMI：车道、车辆、行人和信号灯

用户已确认当前车道判定效果良好。v4保留现有车道模型、边界选择与虚实线规则，将以前板端使用的YOLOv5s COCO80模型接入同一三路拼接服务。识别仍只处理原始HDMI，摄像头和以太网保持采集、拼接、显示。

| 功能 | 实现与显示 |
|---|---|
| 车道 | 当前LR-ASPP第24轮RKNN模型，原有三行状态及覆盖层 |
| 车辆 | COCO bicycle/car/motorcycle/bus/truck，绿色框、类别和分数 |
| 行人 | COCO person，紫色框、类别和分数；不触发自动礼让报警 |
| 信号灯目标 | COCO traffic light，黄色框 |
| 灯色 | 在产生YOLO框的同一原始RGB565帧内分析HSV、紧凑灯点/稀疏绿色符号和暗色灯壳，连续三次确认；显示RED/GREEN/YELLOW/UNKNOWN |
| 两个模型 | 分别使用独立推理线程，每个线程一帧处理中、一帧等待，新帧替换旧等待帧 |
| NPU提交 | 两种模型的input/run/output-get串行执行，CPU预处理和解码独立；不让视频线程等待推理 |
| 陈旧结果 | 两个结果各自显示AGE/FRAME，源帧年龄超过750ms隐藏；灯色证据另外执行500ms失效 |
| 失败或空检测 | 推理失败清旧结果；成功的零目标结果清旧框，视频仍继续 |

车辆和行人输出的是检测目标，不是碰撞、礼让或变道结论。灯色输出是搜索区域内YOLO灯框的颜色证据，不识别箭头方向或本车道对应关系，不输出绿灯必行、允许左转/右转等决定。红绿冲突输出UNKNOWN。不同模型的结果来自各自标明的源帧，不能当作严格同帧的联合决策输入。

## 模型与契约

既有板端模型来自`/data/local/tmp/rknn-yolov5-test/model/yolov5s-640-640.rknn`。转换环境、标定数据和是否与Windows ONNX具有完全相同权重并不明确，保留该来源限制；本次不重新训练、下载或转换模型。

更新脚本读取板端SHA256，接收原模型后核对，记录SHA256、FNV64、COCO80标签顺序、预处理、输出、量化属性读取规则、解码器和实现源码SHA256。板端逐字节核对FNV64及大小，查询并校验两种模型张量，避免拿其他输出布局直接解释。

OpenHarmony的toybox可能没有`sha256sum`。脚本会编译并上传项目自带的`scene_sha256`，通过现有glibc加载器读取原模型校验值，再与Ubuntu接收文件的Python SHA256比对；不依赖板端校验命令，也不安装软件包。该工具在上传前用空文件、标准向量、二进制数据、补齐/读取边界及带空格路径与`hashlib`对照。

| 契约项 | 固定行为 |
|---|---|
| 输入 | 原始HDMI 1920×1080 RGB565LE，展开为RGB888 |
| 缩放 | 640×360，最近中心采样`src=3*dst+1`；上下各补140，值114 |
| 提交 | UINT8 NHWC 1×640×640×3，不另做手工归一化 |
| 输出 | 三个post-sigmoid NCHW头，1×255×80×80、1×255×40×40、1×255×20×20 |
| 输出顺序 | 按grid/stride选择anchor，允许三个头的返回顺序变化 |
| 量化 | 原生INT8/UINT8只接受有效AFFINE属性，scale和zp由RKNN查询；浮点输出请求FP32 |
| 解码 | `yolov5-coco80-road-v1`，不再次sigmoid；confidence 0.25，按类别NMS IoU 0.45 |
| 灯色 | 搜索区域[0.15,0.04,0.85,0.60]，仅YOLO灯框；不启用整片区域备用检测 |

灯色算法沿用`traffic_light.py`默认YOLO路径的证据规则，新增C版黄灯状态；C浮点HSV和OpenCV整数HSV不保证在阈值边缘逐像素相同。合成RGB565红/绿/切换/冲突/缺失帧的状态已完成对照。远处灯点在YOLO 640输入中可能漏检，此时UNKNOWN是诚实结果；此前针对特定视频的成对灯点ROI备用路径没有自动启用。

绿色箭头修正版本为`yolo-box-hsv-green-symbols-c-v2`。原紧凑灯点要求填充率≥0.45、暗灯壳比例≥0.25；绿色符号额外允许填充率≥0.25，但暗灯壳比例要求提高到≥0.35。亮度、饱和度、面积6～1400、宽高比0.5～1.8及三次确认/500ms失效规则保持原值；红黄灯不使用较低填充率。Python参考实现同步增加相同规则。该分支只提供GREEN色证据，不输出箭头形状类别、方向或通行许可。

## Ubuntu更新与打开预览

保持Windows端纯视频全屏播放，关闭Ubuntu预览窗口。现有共享目录已挂载、PCIe驱动已恢复时执行：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/integrate_hdmi_scene_rk3568.sh
```

脚本运行车道回归、目标/灯色核心测试、异步队列测试和Python/C对照，编译独立`pcie_multivideo_server.scene_v4`，核对原YOLO模型身份，上传后同时初始化两个模型。检查通过后才停止旧服务并切换；切换失败时尝试恢复保留的lane_v3。成功标志：

```text
OBJECT_CONTRACT_PASS ...
OBJECT_INSPECT_PASS ...
DEPLOY_inspect-scene_PASS
DEPLOY_start-scene_PASS
SCENE_INTEGRATE_PASS
```

绿色箭头修正版的`OBJECT_INSPECT_PASS`行还应包含`signal=yolo-box-hsv-green-symbols-c-v2`。再次更新v4时先上传临时文件，再原子替换程序路径，避免在原服务运行时直接覆盖其程序文件。

这版复用已有Ubuntu编译器、Python和RKNN环境，不安装依赖。测试、接收模型或检查失败时脚本停止，不应跳过错误继续手动切换。

```bash
bash /mnt/hgfs/pcie_host/preview_hdmi_lane_fast.sh
```

HDMI区域保留三行车道状态，新增两行：

```text
OBJ:ASYNC AGE:...MS FRAME:...
SIGNAL:GREEN CAR:... PERSON:... LIGHT:...
```

`CAR`统计上述车辆类别，含自行车和摩托车；框标签仍区分具体类别。灯色UNKNOWN不代表YOLO灯框一定没检出，也可能是灯点不足、颜色冲突、尚未确认或证据超时。`OBJ ... WAIT/STALE/ERROR`分别表示未有结果、结果过期和目标推理失败。

需要排错时保持预览运行，在另一个终端读取现有日志入口：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_hdmi_lane_rk3568.sh log
```

`lane`和`objects`分别给出模型耗时，`inference_wait_io_ms`包含NPU排队等待；`profile total_us`是视频周期，两模型耗时不应直接与之相加。新服务版本为`multivideo-hdmi-scene-v4`。接入第二模型增加NPU负载，实际视频FPS、两种识别更新频率、500ms车道时序重置是否增加均需板端验收，不能沿用单模型测量宣称已通过。

## 重启与回退

重启后按原流程恢复共享挂载和`pango_pcie_multivideo.ko`驱动，确认`/dev/pango_pcie_multivideo_0002:21:00.0`存在，然后启动已部署v4：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_hdmi_lane_rk3568.sh start-scene
bash /mnt/hgfs/pcie_host/preview_hdmi_lane_fast.sh
```

首次部署通过后不需要每次重新运行集成脚本。需要恢复用户已认可的车道单模型版时，关闭预览并执行：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_hdmi_lane_rk3568.sh start-diag
bash /mnt/hgfs/pcie_host/preview_hdmi_lane_fast.sh
```

## 记录与验证状态

| 文件 | 内容 |
|---|---|
| `rk3568_scene_deploy/integrate.log` | Ubuntu构建、两模型检查和启动输出 |
| `road_objects.manifest.json` | 实际原YOLO模型身份、全部标签、预处理、输出、量化读取规则、解码器、灯色规则和源码身份 |
| `road_objects.contract` | 板端校验的大小、FNV64和解码器身份 |
| `scene_runtime.json` | 两个实际模型SHA256、服务SHA256、异步方式和年龄限制 |
| `rk3568_lane_deploy/preview_runtime.json` | 成功切换后的当前预览运行身份 |

本地已通过目标FP32/INT8解码、头顺序、NMS、损坏张量、RGB565补边、红绿黄确认/冲突/过期、行人颜色排除和HDMI以外区域不变测试；C/Python预处理逐字节一致、合成灯色状态一致；新服务与无识别基线ARM64严格编译通过。Windows没有pthread运行环境，两个异步模块的实际线程回归由Ubuntu脚本执行。板端模型检查、真实道路检出质量与双模型性能待用户执行，不能以合成测试代替。

2026-10-26用户已在Ubuntu通过两种异步线程回归、场景服务编译和C/Python对照，首次执行停在`toybox: Unknown command sha256sum`，尚未切换服务。已改用项目自带校验工具；修复后重新运行同一集成命令，板端模型检查和实际画面仍待验证。

随后双模型已在实机启动，录屏`屏幕录制 2026-10-26 221420.mp4`可见车辆、行人和两个LIGHT框，但绿色箭头时SIGNAL为UNKNOWN。原视频112507的9.8～10.4秒右侧灯人工ROI回归中，紧凑形状规则接受5/7帧，绿色符号规则接受7/7帧，三帧预热后5/5帧确认GREEN。此处人工ROI仅验证颜色模块，不作为YOLO检出率、全视频准确率或板端效果证明。四种朝向的稀疏绿色符号、浅色背景/细线拒绝与红绿冲突已通过C测试及C/Python对照。更新后实机效果待验证，仍使用上面的集成和预览命令。
