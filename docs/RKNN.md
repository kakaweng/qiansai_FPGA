# 阶段 5A：PCIe 单帧进入 RKNN 与 YOLOv5 检测

## 当前状态

新增 RGB565 彩色输入、RGB 三通道预处理和彩色同帧预览；升级与重启后的命令见 [COLOR.md](COLOR.md)。以下实机记录为灰度版本，彩色尚待上板验收。新程序自动识别灰度/彩色帧，模型保持不变。

`pcie_rknn_probe.c` 用于验证 PCIe 图像能否进入 RK3568 的 NPU 推理。程序使用 RKNN Toolkit2 C API，模型和运行库由命令行指定，不依赖现有示例源码。

已经确认的资产：

| 资产 | 位置或版本 |
| --- | --- |
| 模型 | `rknn-yolov5-test.tar.gz` 内的 `model/yolov5s-640-640.rknn` |
| ARM64 运行库 | `$HOME/rknn-yolov5-test/lib/librknnrt.so` |
| 运行库版本 | `librknnrt version: 2.3.2`，依赖 `GLIBC_2.17` |
| SDK 头文件 | `$HOME/rknn-toolkit2-npu/rknpu2/runtime/Linux/librknn_api/include/rknn_api.h` |
| 板端模型 | `/data/local/tmp/rknn-yolov5-test/model/yolov5s-640-640.rknn` |
| 板端运行库 | `/data/local/tmp/rknn-yolov5-test/lib/librknnrt.so` |

阶段 5A-1 已在 RK3568 上通过：模型初始化、真实 PCIe 帧校验、一次 `rknn_run` 和输出张量读取正常。实机版本为 RKNN API `2.3.2`、NPU 驱动 `0.8.2`；输入为 NHWC `[1,640,640,3]`，三个输出为 NCHW `[1,255,80,80]`、`[1,255,40,40]`、`[1,255,20,20]`。实测一次 NPU 推理约 `55 ms`，FP32 输出获取约 `43 ms`。

阶段 5A-2 已在实际 HDMI 图像上输出检测结果：帧序号 `931` 经 NMS 保留 5 个目标，包含汽车、人物和背包；框位置与类别的准确性还需通过带框图像核对。旧版后处理曾将通道数误判为 `80 × 3 = 240`；正确通道数是 `3 × (80 类 + 4 个框参数 + 1 个目标分数) = 255`。该错误已修复，旧版的 `no candidates or output layout...` 不能证明图像里没有目标。

2026-10-05 用户确认连续识别与实时带框窗口在实机运行，基础端到端框架达到功能里程碑。以太网传输、长期稳定性、持续帧率和场景准确率继续验收。下次重启后的快速恢复命令见 [README.md](README.md#重启-rk3568-后的调试流程)。

## 程序流程

| 阶段 | 实现 |
| --- | --- |
| 模型加载 | 加载指定 RKNN 运行库，初始化指定模型 |
| 信息查询 | 打印 SDK/驱动版本、输入输出尺寸和数据类型 |
| 帧读取 | 从 stream 设备或本地 `.bin` 记录读取一帧，验证帧头和 FNV 校验 |
| 预处理 | 按模型输入尺寸等比例缩放、居中补边；RGB565LE 展开为 RGB 三通道，灰度仍复制三通道 |
| NPU 推理 | 设置 UINT8/NHWC 输入，执行推理；默认直接解码量化输出，必要时请求 FP32 |
| 结果检查 | 检查输出大小和有限数值，解码 YOLOv5 框并执行按类别 NMS |
| 原图保存 | 灰度保存 PGM，彩色保存 PPM；流式截图另存原始 RGB565LE 与彩色 PNG |

当前程序按标准 YOLOv5s 640 模型解码三个输出尺度，使用 COCO 80 类、标准 anchors、置信度阈值 `0.25` 和 NMS 阈值 `0.45`。当前 FPGA 输入是 GRAY8，复制为三个相同通道只能满足输入布局，不能恢复 HDMI 原始颜色。预处理的缩放、补边值 `114` 还需要结合模型转换时的设置核对。

## 模型和运行环境

`D:\dayu200_image` 中的主要内容是分区镜像和升级工具；独立模型来自 Ubuntu 上保留的 RKNN 测试包，不需要从分区镜像提取。

包内清单已经确认：

```text
rknn_yolov5_demo
lib/librga.so
lib/librknnrt.so
glibc/ld-linux-aarch64.so.1
glibc/libc.so.6
model/yolov5s-640-640.rknn
model/bus.jpg
```

板端已找到同一套模型和 `librknnrt.so`。不要把 Ubuntu x86_64 库作为 RK3568 的 ARM64 运行库。

## 构建要求

运行库依赖 `GLIBC_2.17`，因此探测程序需要 ARM64 GNU/Linux 动态 C 工具链。不能沿用发送程序的静态 musl 构建方式。`build_rknn_probe.sh` 只自动选择 `aarch64-linux-gnu-gcc` 或 `aarch64-linux-gnu-clang`，也可以通过 `RKNN_CC` 明确指定编译器。

```bash
cp -v \
    /mnt/hgfs/pcie_host/build_rknn_probe.sh \
    /mnt/hgfs/pcie_host/pcie_rknn_probe.c \
    /mnt/hgfs/pcie_host/draw_detections.py \
    /mnt/hgfs/pcie_host/frame_to_png.py \
    /mnt/hgfs/pcie_host/frame_format.py \
    "$HOME/pcie_host/"
cd "$HOME/pcie_host"
chmod +x build_rknn_probe.sh
grep -n 'YOLO_VALUES_PER_ANCHOR' pcie_rknn_probe.c
RKNN_RUNTIME="$HOME/rknn-yolov5-test/lib/librknnrt.so" \
    ./build_rknn_probe.sh
```

如果头文件或编译器在其他位置：

```bash
RKNN_HEADER_DIR=/实际的/include目录 \
RKNN_CC=/实际路径/aarch64-linux-gnu-gcc \
./build_rknn_probe.sh
```

脚本生成 `$HOME/pcie_host/pcie_rknn_probe`。如果 Ubuntu 没有 ARM64 GNU/Linux 编译器，先不要用 x86_64 `gcc` 生成的程序上板。

## 运行参数

| 参数 | 含义 |
| --- | --- |
| `--model` | 板端可读取的 `.rknn` 文件，必需 |
| `--runtime` | 板端 RKNN 运行库路径，默认 `librknnrt.so` |
| `--device` | stream 设备或本地完整 `.bin` 帧记录 |
| `--inspect` | 只查询模型属性，不读 PCIe 帧、不执行推理 |
| `--save-frame` | 推理成功后保存本次原始输入为 PGM |
| `--serve` | 持续推理并发送同帧图像与检测结果 |
| `--bind` / `--port` | TCP 监听 IPv4 地址和端口，默认 `127.0.0.1:9000` |
| `--daemon` / `--log` | 后台运行服务及追加日志路径，默认 `/data/local/tmp/pcie_rknn_server.log` |
| `--profile` | 连续服务中额外打印抽样检测详情；分段耗时默认打印，不扫描全部张量 |
| `--preview-mode matched\|async` | 连续服务默认 `matched`，每条记录包含同帧图像与真实检测；`async` 使用原始帧与独立检测 |
| `--output-stats` | 额外扫描输出张量 min/max，用于诊断；流式默认关闭以减少耗时 |

stream 设备只允许一个读取者。运行单帧程序前，在 Ubuntu 预览终端按 `Q`、`Esc` 或 `Ctrl+C` 关闭 `preview_stream.py`，并用 HDC 停止板端 `pcie_stream_server`；PCIe 驱动保持加载。保持 HDMI 端显示待识别图片，便于核对本次检测结果。当前模型使用 COCO 80 类，可先用包含人物、汽车或公交车等目标的清晰图片验证。

## 上板步骤

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
sudo "$HDC" list targets
```

```bash
sudo "$HDC" -t "$TARGET" shell \
    'if pidof pcie_stream_server >/dev/null; then killall pcie_stream_server; fi'
sudo "$HDC" -t "$TARGET" shell \
    'pidof pcie_stream_server; ls -l /dev/pango_pcie_stream_0002:21:00.0'
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pcie_rknn_probe" \
    /data/local/tmp/pcie_rknn_probe
sudo "$HDC" -t "$TARGET" shell chmod 755 /data/local/tmp/pcie_rknn_probe
```

先只检查模型，不读取 PCIe：

```bash
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/rknn-yolov5-test/glibc/ld-linux-aarch64.so.1 \
    --library-path /data/local/tmp/rknn-yolov5-test/lib:/data/local/tmp/rknn-yolov5-test/glibc \
    /data/local/tmp/pcie_rknn_probe \
    --model /data/local/tmp/rknn-yolov5-test/model/yolov5s-640-640.rknn \
    --runtime /data/local/tmp/rknn-yolov5-test/lib/librknnrt.so \
    --inspect
```

再执行一次 PCIe 单帧推理：

```bash
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/rknn-yolov5-test/glibc/ld-linux-aarch64.so.1 \
    --library-path /data/local/tmp/rknn-yolov5-test/lib:/data/local/tmp/rknn-yolov5-test/glibc \
    /data/local/tmp/pcie_rknn_probe \
    --model /data/local/tmp/rknn-yolov5-test/model/yolov5s-640-640.rknn \
    --runtime /data/local/tmp/rknn-yolov5-test/lib/librknnrt.so \
    --device /dev/pango_pcie_stream_0002:21:00.0 \
    --save-frame /data/local/tmp/rknn_input.pgm | \
    tee "$HOME/pcie_host/rknn_probe.log"
```

推理成功会出现 `RKNN runtime`、输入输出属性、`PCIe frame verified`、`YOLO decode`、`YOLO detections`、`RKNN timing` 和 `PCIe-to-RKNN single-frame inference passed`。最后一行只代表单帧推理流程成功，实际识别还要核对 `Detection[...]` 的类别、置信度与框位置。检测坐标为原始 1920×1080 图像上的 `x1,y1,x2,y2`。

| 输出 | 含义与处理 |
| --- | --- |
| `YOLO decode: max_objectness=... max_class=... max_score=...` | 输出解码已执行；`max_score` 为目标分数乘类别分数的最大值 |
| `YOLO detections: raw=... kept=...` | 阈值过滤后的候选数与按类别 NMS 后保留数 |
| `Detection[...]: class=... label=... score=... box=...` | 检出的类别、置信度及原图坐标；与 HDMI 待识别图核对 |
| `YOLO detections: raw=0 kept=0` | 输出布局通过检查，但当前阈值下没有保留框；结合本次保存的图像和 `max_score` 排查 |
| `Unsupported YOLO output...` | 输出布局与当前解码器不匹配，程序会报错退出 |

目前板端检测结果打印在终端，通过 `tee` 保存到 Ubuntu 的 `rknn_probe.log`。`--save-frame` 保存的是本次实际推理使用的原始灰度帧，不包含检测框或 640×640 补边；`frame_to_png.py` 只转换图像格式，也不会添加检测框。

## 阶段 5B/5C：连续 RKNN 识别与实时带框预览

新版 `pcie_rknn_probe` 支持 `--serve`。它只打开一次 PCIe stream 和一次 RKNN 上下文，持续读取帧、推理、解码，默认通过 TCP 发送原图与同一帧的检测框。`preview_stream.py` 识别到同帧结果记录后，会自动在窗口叠加框、类别和置信度。每个识别服务实例仍只允许一个预览客户端。

先同步并编译新版文件：

```bash
cp -v \
    /mnt/hgfs/pcie_host/pcie_rknn_probe.c \
    /mnt/hgfs/pcie_host/preview_stream.py \
    /mnt/hgfs/pcie_host/rknn_stream.py \
    /mnt/hgfs/pcie_host/draw_detections.py \
    /mnt/hgfs/pcie_host/capture_stream.py \
    /mnt/hgfs/pcie_host/frame_to_png.py \
    /mnt/hgfs/pcie_host/frame_format.py \
    /mnt/hgfs/pcie_host/build_rknn_probe.sh \
    "$HOME/pcie_host/"
cd "$HOME/pcie_host"
RKNN_RUNTIME="$HOME/rknn-yolov5-test/lib/librknnrt.so" ./build_rknn_probe.sh
```

关闭旧预览后，设置 HDC 变量，停止板端旧服务器再发送程序。只更新用户态程序，保持已经验证的内核模块加载：

`killall` 发出停止信号后，正在执行的推理或采集需要时间退出。确认 `pidof` 不再输出 PID 后再发送文件，避免覆盖仍在运行的程序。

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
sudo "$HDC" list targets
sudo "$HDC" -t "$TARGET" shell \
    'killall pcie_stream_server pcie_rknn_probe 2>/dev/null || true'
sudo "$HDC" -t "$TARGET" shell \
    'pidof pcie_stream_server pcie_rknn_probe; ls -l /dev/pango_pcie_stream_0002:21:00.0'
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pcie_rknn_probe" /data/local/tmp/pcie_rknn_probe
sudo "$HDC" -t "$TARGET" shell chmod 755 /data/local/tmp/pcie_rknn_probe
```

建议后台运行识别服务，让它独立于 HDC shell 会话。首次检查也可去掉 `--daemon`，保留终端查看前台日志：

```bash
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/rknn-yolov5-test/glibc/ld-linux-aarch64.so.1 \
    --library-path /data/local/tmp/rknn-yolov5-test/lib:/data/local/tmp/rknn-yolov5-test/glibc \
    /data/local/tmp/pcie_rknn_probe \
    --model /data/local/tmp/rknn-yolov5-test/model/yolov5s-640-640.rknn \
    --runtime /data/local/tmp/rknn-yolov5-test/lib/librknnrt.so \
    --device /dev/pango_pcie_stream_0002:21:00.0 \
    --serve --daemon --bind 0.0.0.0 --port 9001 --preview-mode matched --profile
sudo "$HDC" -t "$TARGET" shell pidof pcie_rknn_probe
sudo "$HDC" -t "$TARGET" shell tail -n 20 /data/local/tmp/pcie_rknn_server.log
```

这里为识别服务使用 `9001` 端口，避开旧原始预览的 `9000`。USB/HDC 方式先在设置过变量的终端执行转发，查看是否已有 `9001` 转发，已有时复用：

```bash
sudo "$HDC" -t "$TARGET" fport ls
sudo "$HDC" -t "$TARGET" fport tcp:9001 tcp:9001
```

出现 `TCP Port listen failed` 时，先确认该本地端口是否已被旧转发或其他进程占用，不要重复启动服务器。

另开 Ubuntu 图形桌面终端启动窗口，USB/HDC 转发使用 `127.0.0.1`：

```bash
cd "$HOME/pcie_host"
python3 -u preview_stream.py --host 127.0.0.1 --port 9001 --require-detections
```

如果使用 README 中的以太网直连，将 `--host 127.0.0.1` 换成板端 IP，并保持识别服务的 `--bind 0.0.0.0`；以太网方式不使用 `fport`。窗口中每帧会显示 `Targets`、推理耗时和检测框，按 `S` 会同时保存原始 PGM、原始 PNG 和带框 PNG，默认目录为 `preview_snapshots`。窗口状态文字位于单独工具栏，保留完整图像。

为了减少积压，独立采集线程持续更新双缓冲，推理线程优先取最新待处理帧，发送线程替换尚未发送的旧结果。序号有跳跃是这一策略的正常结果，窗口显示的 `Source interval` 是被选中源帧之间的间隔，`Receive` 是窗口接收结果的速率。默认 `matched` 模式中图像和结果在同一条记录里，不会使用上一帧的框标注下一帧。

实机单帧 NPU 耗时约 `50～70 ms`，此外还有预处理、输出获取、解码和传输，不能据此直接承诺连续识别帧率。连续服务通过本地编译和协议等检查后，已获用户确认实机连续识别与带框窗口功能正常；持续运行速度、长时间稳定性及以太网传输仍需测量。

客户端断开后，服务关闭 stream 并等待下一次连接；模型上下文保留。Ubuntu 窗口默认重连，连接恢复后继续显示；帧格式或校验错误也会报告并重新连接，`--no-reconnect` 或无窗口验证模式遇错退出。板子重启会使服务和内核模块消失，需要重新加载模块并启动识别服务；USB 转发也可能需要恢复。

检查和停止命令：

```bash
sudo "$HDC" -t "$TARGET" shell tail -n 30 /data/local/tmp/pcie_rknn_server.log
sudo "$HDC" -t "$TARGET" shell killall pcie_rknn_probe
```

可先验证 30 帧连续识别，运行时关闭其他预览客户端：

```bash
python3 -u preview_stream.py --host 127.0.0.1 --port 9001 \
    --require-detections --headless --count 30 --profile
```

### TCP 识别记录格式

记录按小端序排列，依次发送记录头、可选原始像素、检测框数组。版本 1 原始预览使用 40 字节头，旧版本 2 同帧记录使用 80 字节头，版本 3 使用 84 字节头；当前识别服务使用版本 4 的 88 字节头，Python 兼容四种版本。V1 原始流在固定 1920×1080 下通过像素长度区分 GRAY8/RGB565LE；V2/V3 仅支持灰度；V4 有明确像素格式字段。

| 偏移 | 长度 | 内容 |
| --- | --- | --- |
| 0 | 40 | 与驱动记录对应的 magic、版本、头长度、尺寸、像素长度、FNV 校验和、帧序号、采集时间戳 |
| 40 | 8 | 检测数量和单个框长度，两个 UINT32；单个框为 24 字节 |
| 48 | 24 | 读取、预处理、设置输入、NPU 执行、输出获取、后处理的微秒耗时，六个 UINT32 |
| 72 | 8 | 板端检测完成时的单调时钟纳秒值，UINT64 |
| 80 | 4 | 版本 3/4 的 flags，UINT32 |
| 84 | 4 | 版本 4 的 pixel_format：1=GRAY8，2=RGB565LE，UINT32 |
| 88 | 0、2073600 或 4147200 | 按 flags 和 pixel_bytes 判断是否含原图像素；必须与 pixel_format 一致 |
| 88 + pixel_bytes | 数量 × 24 | 每框六个 UINT32：类别、分数乘 1000000、x1、y1、x2、y2；坐标对应原图 |

| flags | 记录类型 | pixel_bytes | 检测数量 |
| --- | --- | --- | --- |
| `0` | `async` 的原始图像 | 灰度 `2073600`，彩色 `4147200` | 必须为 `0`，不更新检测状态 |
| `1` | `async` 的独立检测结果 | `0` | 可以为 `0` |
| `2` | `matched` 的同帧图像和检测结果 | 灰度 `2073600`，彩色 `4147200` | 可以为 `0`；真实零目标也必须更新画面并清空框 |

未知 flags 或组合值 `3` 会被拒绝；同帧记录在校验像素和框后一次性发布。检测完成时间使用真实推理完成时刻，发送时间不改写该字段。

窗口对帧校验和、框数量、类别、分数及坐标范围进行检查，再发布完整的图像与检测结果。该网络版本只扩展用户态传输，不改变 FPGA 寄存器或内核 stream 记录格式。

## 单帧结果绘框（可选）

`draw_detections.py` 从同次推理日志读取类别、置信度和原图坐标，在 Ubuntu 生成带框 PNG。它会核对日志中的成功状态、帧校验和与检测数量，避免把另一帧的检测结果画到当前图像上。依赖与预览窗口相同的 `python3-opencv` 和 `python3-numpy`。此流程用于单帧核对；连续识别窗口会自动绘框，不需要逐帧执行此脚本。

推理成功后，把同次原图取回 Ubuntu 并绘框：

```bash
sudo "$HDC" -t "$TARGET" file recv \
    /data/local/tmp/rknn_input.pgm \
    "$HOME/pcie_host/rknn_input.pgm"
python3 "$HOME/pcie_host/frame_to_png.py" \
    "$HOME/pcie_host/rknn_input.pgm" \
    "$HOME/pcie_host/rknn_input.png"
python3 "$HOME/pcie_host/draw_detections.py" \
    "$HOME/pcie_host/rknn_input.pgm" \
    "$HOME/pcie_host/rknn_probe.log" \
    "$HOME/pcie_host/rknn_detections.png"
xdg-open "$HOME/pcie_host/rknn_detections.png"
```

查看 `rknn_detections.png` 核对框位置、类别和置信度；`rknn_input.png` 保留原始灰度内容。每次重新推理后，都需要重新取回 PGM，并使用对应日志绘框。日志应只包含一次推理；多次推理的混合日志会被拒绝。

## 验收与后续阶段

| 阶段 | 状态 | 验收目标 |
| --- | --- | --- |
| 5A-1 | 实机通过 | 模型信息查询正常，实际 PCIe 帧校验通过，NPU 推理成功并输出张量摘要 |
| 5A-2 | 实机输出类别和坐标，已接入带框显示；场景准确率待评估 | 核对 YOLOv5 检测框、类别和置信度 |
| 5B | 用户确认实机功能通过，持续性能待测量 | 持续读取并处理最新帧，分别测量预处理、推理和后处理耗时 |
| 5C | 用户确认实机实时带框窗口效果；以太网待验证 | 将帧序号和识别结果回传到预览窗口，显示检测框 |
# 2026-10-06 实时预览并行优化

本节为此前的异步方案记录。当前默认使用 `matched`，如需以下行为，须显式启动 `--preview-mode async`。

新版 `pcie_rknn_probe --serve` 将采集、推理和 TCP 发送解耦：原始 HDMI 帧使用独立双缓冲持续发送，检测结果以小型 detection-only 记录异步发送。`preview_stream.py` 保留最近一帧原图和最近一次检测框，因此窗口可以显示更高的原始画面帧率；检测框可能对应稍早的 `detection seq`，窗口会同时显示图像序号和检测序号。

窗口中的指标含义如下：

| 指标 | 含义 |
| --- | --- |
| `Raw` | 板端原始帧时间戳计算的原始画面频率 |
| `Receive` | Ubuntu 实际收到完整原始帧的频率 |
| `Display` | OpenCV 实际替换窗口图像的频率 |
| `Detection` | RKNN 检测结果记录的更新频率 |

`cv2.waitKey(1)` 已用于减少窗口线程的人为等待，但它不是主要瓶颈。当前模型的单帧推理约 136～188 ms，检测框稳定 30 FPS 仍不现实；30 FPS 需要每帧端到端周期不超过 33.3 ms。实际效果必须在 RK3568 上重新测量，USB/HDC 的传输带宽和稳定性也会限制 `Raw` FPS。

本轮协议版本为 3：原始记录是 84 字节头部加 2,073,600 字节像素，`flags=0`；检测记录是 84 字节头部加 `count×24` 字节检测框，`pixel_bytes=0` 且 `flags=1`。Python 接收端同时兼容版本 1、版本 2 同帧记录和版本 3 分流记录。

### 原始帧与空检测结果的区别

已修复接收端将原始帧中的空检测元数据写入 `latest_inference` 的问题。该问题会让检测框在原始帧到达时消失，并让检测计数错误增加。现在只有版本 2 同帧记录或版本 3 detection-only 记录更新检测状态；原始帧只更新图像。真实的零目标检测仍会更新并清除框，避免将目标消失掩盖成持续识别成功。

开启 `--profile` 后，每次真实检测输出 `Detection result:` 日志，包含序号、目标数量、最高保留置信度和推理耗时。排查顺序为：先确认显示端不清空有效结果，再观察真实检测是否波动，最后用有标注的灰度样本评估模型。灰度复制为三通道不会恢复颜色信息，可能影响在彩色图像上训练的模型，但本次闪框已有明确的软件原因，不能据此判断需要重新训练。

本次修复只需重新复制 `preview_stream.py` 到 Ubuntu 并重启窗口，现有并行版板端程序和模型继续使用。窗口保持较快的原始画面和较低频率的检测框更新；这改善显示连续性，不增加模型每秒推理次数。运动场景的检测延迟仍需通过缩短推理耗时或后续目标跟踪降低。

## 推理耗时与运动跟踪优化

板端 `pcie_rknn_probe.c` 已将 `rknn_output.want_float` 改为 `0`，根据输出张量的 `type`、`qnt_type`、`scale` 和 `zp` 在解码候选时还原数值，避免每次输出都转换成 FP32。YOLO 解码先检查 objectness，低于置信度阈值的网格不会再扫描 80 个类别。启用 `--profile` 时，应重点比较 `outputs_ms`、`postprocess_ms` 和完整检测周期；交叉编译通过不等于 RK3568 实机结果已经确认。

预览端的 `preview_stream.py` 现在对检测框做轻量跟踪。相同类别且 IoU 足够的框会更新速度，下一次检测到达前按速度预测；默认最多保留 350 ms，真实的空检测仍会让框在保留时间后消失。该功能改善显示连续性，不会增加 NPU 推理次数，也不会把跟踪框误计为 `Detection FPS`。

重新部署板端程序后，可以按场景调整：

```bash
python3 -u preview_stream.py --host 127.0.0.1 --port 9001 \
    --require-detections --track-max-age-ms 350 --profile
```

如果新版量化输出的框坐标、类别或置信度与旧版明显不一致，应先恢复 `want_float=1` 验证模型输出，再单独处理量化解码；不能用跟踪器掩盖模型输出错误。

板端服务支持 `--float-output` 作为兼容性对照开关。默认模式使用量化输出；对照测试时在服务启动命令最后加上 `--float-output`，确认检测结果恢复后，再比较两种模式的 `outputs_ms` 和 `total_ms`。

## 2026-10-07 同帧识别优先与性能测量

本轮按用户需求恢复识别完成后更新画面的方式，默认 `--preview-mode matched`。采集、推理、发送继续并行，但 TCP 只发送已推理原图和它自己的检测结果，窗口直接绘制真实框并绕过预测跟踪。真实零目标帧也正常显示，避免隐藏漏检。

流式默认关闭全部输出元素的 min/max 扫描，`--profile` 只开启抽样检测详情；需要张量诊断时显式加 `--output-stats`。量化输出继续直接解码候选。不支持直接解码的类型自动请求 FP32，`--float-output` 也已按实际 FP32 缓冲读取，不再按原模型 INT8 属性误读。阈值仍为 confidence `0.25`、NMS `0.45`。

| 日志或指标 | 本轮测量含义 |
| --- | --- |
| `RKNN stream timing:` | 预处理、设置输入、NPU、输出获取、后处理、完整检测周期 |
| `RKNN preview timing: mode=matched` | 发送耗时、板端帧年龄、两次发送完成间隔及其 FPS |
| `Preview timing: kind=combined` | 完整同帧记录的接收、校验耗时；header 等待包含下一次结果等待 |
| `Display timing: mode=matched` | 新图像渲染耗时与图像更新 FPS；不重复绘制旧帧来计 FPS |
| `image seq` / `detection seq` / `delta` | 同帧模式应序号相同、时间差为 `0.0 ms` |

新的同帧 FPS 与检测效果仍需实机验证。采集与推理重叠后，稳定周期通常受较慢阶段限制；端到端延迟包含该帧采集、推理与传输，不能简单把各阶段 FPS 相加。升级与终端 A/B 的完整 USB 指令见 README 的“2026-10-07：同帧识别优先预览”。

### 首帧等待与缓冲交接修复

用户实机日志确认 TCP 已连接，模型已初始化，采集持续校验成功，但尚未进入预处理。采集线程的旧槽选择会反复清掉唯一待推理帧的 `ready`，使推理线程可能长期等不到帧。现改为优先写空闲槽、必要时覆盖较旧的待处理帧；推理始终取得完整的最新待处理帧，处理中的像素不会被采集覆盖。

采集线程结束时会发布停止状态并唤醒取帧等待者；采集和推理的诊断开关改为线程各自持有，采集不再逐帧刷 `PCIe frame verified`。每次新连接的首帧打印 `RKNN first inference` 和 `RKNN stage`，用于定位输入设置、NPU 执行与输出获取的阻塞。

本地验证涵盖旧调度选择唯一待处理槽的复现、150 次实际线程交接、序号递增、推理持有期间像素不变、采集退出通知，以及 ARM64 严格编译。部署说明和按启动 PID 核实旧实例的方法见 README 的“已连接但只有采集日志、没有识别画面”；这些检查不代替 RK3568 实机验收。
