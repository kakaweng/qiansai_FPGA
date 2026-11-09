# HDMI + OV5640 经 PCIe 传输并在 RK3568 拼接

第三路以太网输入的实现、接线和完整调试指令见 [ETHERNET_VIDEO.md](ETHERNET_VIDEO.md)。本文件保留双源基线流程。新版 `build_multivideo.sh` 默认生成独立的 `pcie_multivideo_server.ethernet_v6` 和 `pcie_ethernet_status`，不会覆盖原来的双源程序。

本阶段接在已通过的 OV5640 采集诊断之后：让 OV5640 像素进入 DDR 和 PCIe，与 HDMI 按整帧交替传输，在 RK3568 用户程序中拼接成一张 1280×480 图像。

| 位置 | 本次行为 |
|---|---|
| FPGA 输入 | HDMI：1920×1080 RGB565；OV5640：640×480 RGB565 |
| FPGA DDR | HDMI 保留原双缓冲；摄像头独立区域按需保存下一完整有效帧 |
| PCIe | 每次传输一帧，软件依次请求 HDMI、OV5640，共用 DMA 通路 |
| RK3568 | 接收原始 RGB565，检查记录完整性，缩放 HDMI 并左右拼接 |
| Ubuntu | HDC 转发接收 RK 已经拼好的图像，负责校验和窗口显示 |
| 以太网输入 | 保留 source=2，本阶段尚未实现以太网视频接收 |

左半部分是 HDMI 缩成的 640×360 图像，上下各 60 行黑边；右半部分为 OV5640 原尺寸 640×480。两路采集持续工作，PCIe 逐帧调度，没有曝光同步。帧头时间戳表示各路 DMA 完成时间，不是传感器曝光时间。本次还没有适配 OpenHarmony 本地显示面板；查看窗口沿用之前已经跑通的 Ubuntu + HDC 方式。

## 1. 文件与配套关系

| 文件 | 用途 |
|---|---|
| `../../06_hdmi_loop_1/src/camera/video_async_fifo.v` | 摄像头到 DDR 的跨时钟像素 FIFO |
| `../../06_hdmi_loop_1/src/camera/camera_ddr_snapshot.v` | 按需捕获完整有效帧，写 DDR 并保持到读取结束 |
| `../../06_hdmi_loop_1/src/ddr/ddr_write_arbiter.v` | HDMI 与摄像头以完整写 burst 为单位轮流访问 DDR |
| `../../06_hdmi_loop_1/src/pcie/pcie_frame_dma_bridge.v` | 源选择、不同尺寸分块、完整帧 DMA |
| `pango_pcie_multivideo.c` | 新双源内核驱动，只申请一块 4147200 字节 DMA 缓冲 |
| `pcie_multivideo_protocol.h` | 内核、用户程序共同使用的 64 字节记录头与 ioctl |
| `pcie_multivideo_server.c`、`video_mosaic.c`、`video_mosaic.h` | RK3568 采集、拼接、快照和 TCP 输出 |
| `pcie_multivideo_server` | 已生成的 ARM64 Linux 静态程序，可直接发送到 RK |
| `multivideo_stream.py`、`preview_multivideo.py` | Ubuntu 端校验与预览 |
| `stop_multivideo_server.sh` | 停止双源服务，等待正在进行的 DMA 完成或超时 |

新模块与原 `pango_pcie_hdmi_frame_test` 匹配相同 PCI ID，使用前必须停止原采集服务并卸载原驱动。旧 HDMI 位流没有双源 DMA 能力，加载新驱动会拒绝绑定并提示先烧录双源 RGB565 位流。`insmod` 返回成功后还必须检查设备节点和 PCI 驱动绑定。

已有 HDMI 单路预览、RKNN 文件保留。新的双源协议不能交给旧的 `pcie_stream_server`、旧预览或旧 RKNN 程序读取。

## 2. Ubuntu 同步和构建

以下命令都在 Ubuntu 终端执行，`shell` 后的部分才在 RK3568 执行。共享目录继续使用你已经验证的 `/mnt/hgfs/pcie_host`。在同一终端保留变量；命令出现错误时先停止后续步骤，保留本次输出。

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
export FPGA_BDF="0002:21:00.0"
export KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10"
export LLVM_DIR="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin"
export PATH="$LLVM_DIR:$PATH"

cd "$HOME/pcie_host"
SYNC_FILES=(
    Makefile pango_pcie_multivideo.c pcie_multivideo_protocol.h
    pcie_multivideo_server.c video_mosaic.c video_mosaic.h
    build_multivideo.sh pcie_multivideo_server
    multivideo_stream.py preview_multivideo.py
    frame_to_png.py frame_format.py frame_checksum.c build_checksum.sh
    verify_multivideo.py verify_video_mosaic.c
    stop_multivideo_server.sh stop_rknn_server.sh MULTIVIDEO.md
)
sync_multivideo_files()
{
    local filename
    for filename in "${SYNC_FILES[@]}"; do
        cp -v -- "/mnt/hgfs/pcie_host/$filename" "$HOME/pcie_host/" || return 1
        cmp -- "/mnt/hgfs/pcie_host/$filename" "$HOME/pcie_host/$filename" || return 1
    done
}
sync_multivideo_files
```

所有 `cmp` 无输出且没有报错，表示同步一致。构建新内核模块并签名：

```bash
cd "$HOME/pcie_host"
make -C "$KDIR" M="$PWD" ARCH=arm64 LLVM=1 LLVM_IAS=1 pango_pcie_multivideo.ko && \
    "$KDIR/scripts/sign-file" sha1 \
    "$KDIR/certs/signing_key.pem" \
    "$KDIR/certs/signing_key.x509" pango_pcie_multivideo.ko

modinfo -F vermagic pango_pcie_multivideo.ko
modinfo -F signer pango_pcie_multivideo.ko
file pcie_multivideo_server
```

`vermagic` 应与板端 `uname -r` 的 `5.10.184` 配套，签名者应非空，用户程序应为 AArch64 静态 ELF。本 Windows 环境没有你的 Ubuntu 内核构建树，新 `.ko` 必须在 Ubuntu 上构建，未提供预编译模块。已经附带新用户程序，通常无需重编译；需要重编时运行 `bash build_multivideo.sh`，脚本支持 `OHOS_NATIVE` 或 `PREVIEW_ZIG` 指定工具链。

构建 Ubuntu 本机校验加速库，运行离线协议和拼接测试：

```bash
cd "$HOME/pcie_host"
bash build_checksum.sh
python3 verify_multivideo.py
cc -O2 -std=c11 -Wall -Wextra -Werror \
    verify_video_mosaic.c video_mosaic.c -o /tmp/verify_video_mosaic && \
    /tmp/verify_video_mosaic
```

## 3. 停旧服务、重新生成并烧录 FPGA

先关闭 Ubuntu 上的旧预览窗口。RK3568 仍连接、FPGA 仍运行当前位流时，停止服务并卸载原模块：

```bash
sudo "$HDC" list targets
sudo "$HDC" -t "$TARGET" shell id
sudo "$HDC" -t "$TARGET" file send stop_rknn_server.sh /data/local/tmp/stop_rknn_server.sh
sudo "$HDC" -t "$TARGET" file send stop_multivideo_server.sh /data/local/tmp/stop_multivideo_server.sh
sudo "$HDC" -t "$TARGET" shell sh /data/local/tmp/stop_rknn_server.sh
sudo "$HDC" -t "$TARGET" shell sh /data/local/tmp/stop_multivideo_server.sh
sudo "$HDC" -t "$TARGET" shell \
    'if pidof pcie_stream_server >/dev/null; then killall pcie_stream_server; fi'
sudo "$HDC" -t "$TARGET" shell \
    'if grep -q "^pango_pcie_hdmi_frame_test " /proc/modules; then rmmod pango_pcie_hdmi_frame_test; fi'
sudo "$HDC" -t "$TARGET" shell \
    'if grep -q "^pango_pcie_multivideo " /proc/modules; then rmmod pango_pcie_multivideo; fi'
sudo "$HDC" -t "$TARGET" shell lsmod
```

卸载失败表示还有程序占用或尚未退出，先处理该错误。不要在原 DMA 仍运行时重新配置 FPGA。

在 Windows 的 PDS 中打开 `F:\B_file\jichuangsai\Pango_FPGA\06_hdmi_loop_1\hdmi_loop.pds`，检查顶层 `PCIE_RGB565=1`，确认包含新增的三个 RTL 文件。完成综合、布局布线、时序检查、位流生成，下载本次新位流。仅烧录之前通过阶段 1/2 的位流，还不能传输摄像头像素。

FPGA 重新配置后，按你之前跑通的顺序重启 RK3568/重新枚举 PCIe，然后检查：

```bash
sudo "$HDC" list targets
sudo "$HDC" -t "$TARGET" shell uname -r
sudo "$HDC" -t "$TARGET" shell ls /sys/bus/pci/devices
sudo "$HDC" -t "$TARGET" shell ls -l "/sys/bus/pci/devices/$FPGA_BDF/resource0"
```

如果 BDF 改变，更新 `FPGA_BDF`。没有 FPGA 设备时先恢复枚举，后续驱动和图像程序无法替代这一步。

## 4. 部署并加载新驱动

```bash
cd "$HOME/pcie_host"
sudo "$HDC" -t "$TARGET" file send \
    pango_pcie_multivideo.ko /data/local/tmp/pango_pcie_multivideo.ko
sudo "$HDC" -t "$TARGET" file recv \
    /data/local/tmp/pango_pcie_multivideo.ko pango_pcie_multivideo.received.ko
cmp pango_pcie_multivideo.ko pango_pcie_multivideo.received.ko && \
    sudo "$HDC" -t "$TARGET" shell \
    insmod /data/local/tmp/pango_pcie_multivideo.ko profile=1

sudo "$HDC" -t "$TARGET" shell lsmod
sudo "$HDC" -t "$TARGET" shell ls -l "/sys/bus/pci/devices/$FPGA_BDF/driver"
sudo "$HDC" -t "$TARGET" shell ls -l "/dev/pango_pcie_multivideo_$FPGA_BDF"
sudo "$HDC" -t "$TARGET" shell dmesg | tail -n 50
```

本次加载应出现 `dual-source video ready`，设备节点为 `/dev/pango_pcie_multivideo_0002:21:00.0`。模块 probe 不主动采集图像，设备节点出现后运行快照程序才启动 DMA。

```bash
sudo "$HDC" -t "$TARGET" file send \
    pcie_multivideo_server /data/local/tmp/pcie_multivideo_server
sudo "$HDC" -t "$TARGET" shell chmod 755 /data/local/tmp/pcie_multivideo_server
```

## 5. 先验证 OV5640 单路图像

```bash
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_multivideo_server \
    --device "/dev/pango_pcie_multivideo_$FPGA_BDF" \
    --source camera --snapshot /data/local/tmp/camera_test

sudo "$HDC" -t "$TARGET" shell ls -l \
    /data/local/tmp/camera_test_camera.rgb565 /data/local/tmp/camera_test.ppm
sudo "$HDC" -t "$TARGET" file recv \
    /data/local/tmp/camera_test.ppm "$HOME/pcie_host/camera_test.ppm"
xdg-open "$HOME/pcie_host/camera_test.ppm"
```

期望原始文件为 **614400 字节**，PPM 为 **640×480**。在镜头前移动物体后重新执行快照命令，应看到新的图像。检查图像内容、行序及颜色，尤其红蓝是否颠倒。先前 `pixels=307200`、`bad=0` 证明采集时序完整，这一步才检查真实像素是否正确进入 DDR 和 PCIe。

如果摄像头单路失败，先保留快照程序完整输出以及本次 `dmesg`，不要继续用双源模式增加诊断变量。

## 6. 双源拼接快照

HDMI 输入继续设置为 1920×1080，并保持 OV5640 连接：

```bash
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_multivideo_server \
    --device "/dev/pango_pcie_multivideo_$FPGA_BDF" \
    --source both --snapshot /data/local/tmp/dual_test

sudo "$HDC" -t "$TARGET" shell ls -l \
    /data/local/tmp/dual_test_hdmi.rgb565 \
    /data/local/tmp/dual_test_camera.rgb565 /data/local/tmp/dual_test.ppm
sudo "$HDC" -t "$TARGET" file recv \
    /data/local/tmp/dual_test.ppm "$HOME/pcie_host/dual_test.ppm"
xdg-open "$HOME/pcie_host/dual_test.ppm"
```

| 文件 | 预期 |
|---|---|
| `dual_test_hdmi.rgb565` | 4147200 字节 |
| `dual_test_camera.rgb565` | 614400 字节 |
| `dual_test.ppm` | 1280×480，左 HDMI、右 OV5640，由 RK3568 生成 |

可将 `--source both` 改为 `--source hdmi`、使用另一个快照前缀，单独验收 HDMI 回归。每个设备仅允许一个打开者，`Device or resource busy` 时先停止另一个采集程序。

## 7. 连续双源预览

启动 RK 后台拼接服务，建立 HDC 转发，先校验 10 帧：

```bash
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_multivideo_server \
    --device "/dev/pango_pcie_multivideo_$FPGA_BDF" \
    --source both --serve --port 9002 --send-timeout-ms 30000 --daemon
sudo "$HDC" -t "$TARGET" shell tail -n 20 /data/local/tmp/pcie_multivideo.log
sudo "$HDC" -t "$TARGET" fport tcp:9002 tcp:9002

cd "$HOME/pcie_host"
python3 preview_multivideo.py --headless --count 10 --timeout 60
```

每帧应是 `source=4294967295 size=1280x480 bytes=1228800`，序号递增，最后输出 `PASS: 已接收并校验 10 帧`。`capture_gap_ms` 反映两次 DMA 完成时间的间隔，不能用于判断曝光同步。

沿用此前预览可用的 Python 环境打开窗口：

```bash
python3 -c 'import cv2, numpy; print(cv2.__version__, numpy.__version__)'
python3 preview_multivideo.py
```

如果缺少 GUI 依赖，可在 Ubuntu 系统 Python 环境执行 `sudo apt-get install python3-opencv python3-numpy`。运行时 `Q`/Esc 退出窗口，`S` 保存当前拼接 PNG 到 Ubuntu 的 `multivideo_snapshots` 目录。关闭窗口只断开客户端，RK 后台服务继续等待下一次连接。

停止 RK 双源服务：

```bash
sudo "$HDC" -t "$TARGET" shell sh /data/local/tmp/stop_multivideo_server.sh
```

更新可执行程序、卸载新驱动或切回旧 HDMI 服务前，先停止双源服务。关闭 Ubuntu 预览窗口再停服务，可避免窗口继续自动重连。

### 连续预览数帧后连接关闭

实机已收到并校验 5 帧 1280×480 拼接输出，随后客户端 EOF；检查时服务 PID 仍存在、设备节点存在，服务日志只有 `sent=1/2/3`。旧程序仅记录前 3 帧及每 10 帧，因此不能用最后的 `sent=3` 判断实际只发送了 3 帧。旧发送函数对 socket 错误直接返回，不记录失败原因；发送失败后只关闭客户端，服务继续等待连接，这与当前进程仍在的现象相符，具体 errno 尚待实机确认。

配套程序已更新为 `multivideo-transport-v3`：保留 v2 的连接、发送进度、errno 和采集失败日志，并增加 `--check-stream` 板内回环校验客户端。发送无进展超时默认从 5 秒改为 30 秒，支持 `--send-timeout-ms 1～600000`；预览接收超时默认 60 秒，支持 `--timeout 1～600`，EOF/超时报告帧头或像素的已收字节数。单源预览不再伪造 HDMI 与摄像头时间差。超时调整用于验证传输停顿假设，不能证明本次故障已经解决。

此更新只涉及用户程序、预览和诊断，无需为此重新生成位流或编译驱动。Ubuntu 执行：

```bash
cd "$HOME/pcie_host"
for filename in pcie_multivideo_server pcie_multivideo_server.c \
    preview_multivideo.py multivideo_stream.py; do
    cp -v -- "/mnt/hgfs/pcie_host/$filename" "$HOME/pcie_host/" || break
done
sudo "$HDC" -t "$TARGET" shell sh /data/local/tmp/stop_multivideo_server.sh
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pcie_multivideo_server" /data/local/tmp/pcie_multivideo_server
sudo "$HDC" -t "$TARGET" shell chmod 755 /data/local/tmp/pcie_multivideo_server
sudo "$HDC" -t "$TARGET" shell /data/local/tmp/pcie_multivideo_server --version
```

版本应输出 `multivideo-transport-v3`。先用板内回环检查 RK 端用户程序、驱动读取、拼接和本机 socket：

```bash
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_multivideo_server \
    --check-stream --port 9002 --count 300
```

这个命令会在 RK3568 本机连接 `127.0.0.1:9002`，不经过 Ubuntu、HDC 或 USB 视频转发。双源检查应输出 `source=4294967295`，最后输出 `PASS: local stream checked 300 frames`。使用独立日志路径启动服务，保留上次故障日志：

```bash
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_multivideo_server \
    --device "/dev/pango_pcie_multivideo_$FPGA_BDF" \
    --source both --serve --port 9002 --send-timeout-ms 30000 \
    --daemon --log /data/local/tmp/pcie_multivideo_transport_v2.log
python3 preview_multivideo.py --headless --count 30 --timeout 60
sudo "$HDC" -t "$TARGET" shell \
    tail -n 100 /data/local/tmp/pcie_multivideo_transport_v2.log
sudo "$HDC" -t "$TARGET" shell dmesg | \
    grep -Ei 'pango|video DMA|DMA failed|FPGA already busy|pcie|aer|iommu' | tail -n 80
```

HDC 9002 转发仍存在时无需重复建立；若连接 HDC 服务曾重启或转发规则已删除，先重新执行 `fport tcp:9002 tcp:9002`。同一时刻仅运行一个预览客户端。`Video send failed` 的 errno 和字节进度用于定位发送/转发中断；`Capture failed` 结合内核日志定位采集失败。若连续 30 帧通过，再运行 GUI 并观察画面；即便通过，也需要后续长时间运行验收。

## 8. 寄存器和数据约定

| 接口 | 定义 |
|---|---|
| BAR0 `0x150` | 能力签名 `0x4d565331` |
| BAR0 `0x154` | 已支持的 DMA 源位图；本阶段为 `3` |
| BAR0 `0x158` | 源选择寄存器偏移；本阶段为 `0x160` |
| BAR0 `0x15C` | 能力版本 `1` |
| BAR1 `0x160` | 空闲时写源编号：0=HDMI，1=OV5640；忙时忽略改源 |
| BAR1 `0x130`、`0x140` | 原 DMA 目标地址低/高 32 位 |
| BAR1 `0x150` | 原启动寄存器，写 1 请求当前源的一帧 |
| 摄像头 DDR | 控制器地址 `0x0800000`，换算字节地址为 32 MiB；每帧 614400 字节 |
| HDMI DDR | 保留原控制器基址 `0`、`0x0400000`，对应字节地址 0、16 MiB |

BAR0 与 BAR1 的同偏移是两个独立空间，不能混用。原 BAR0 `0x00` 的忙/完成状态及 `0x04`、`0x08` 的 HDMI 元信息保持兼容；摄像头实际长度从源描述和新驱动的记录头获取。

每条记录为小端 64 字节头，接 RGB565LE 像素。依次包括 magic、version、header_bytes、source、format、width、height、pixel_bytes、FNV-1a checksum，以及 64 位 sequence、timestamp_ns、hdmi_timestamp_ns、camera_timestamp_ns。原始源为 0/1，拼接输出源为 `0xffffffff`。接收程序严格检查几何尺寸和长度，避免用 HDMI 固定长度解释摄像头帧。

帧记录中的 FNV 用于检查内核到用户程序、RK 到 Ubuntu 的记录一致性，不构成独立的 FPGA 到主机端到端像素校验；FPGA 数据内容仍需用实拍图像/色条验收。

## 9. 验证状态与问题定位

本机已完成摄像头完整帧/异常恢复/取消请求仿真、HDMI 与摄像头写入竞争、真实 HDMI 写控制器接入仲裁后的图像行序检查、BAR0 窗口测试、原灰度与 RGB565 DMA 回归、摄像头→HDMI→摄像头切换测试。C 拼接测试检查全部 1280×480 像素、黑边和输出边界；Python 九项测试覆盖分片、连续帧、错误帧头、截断、校验失败，以及 EOF/超时字节进度诊断。ARM64 服务程序已严格交叉编译为静态 ELF。PDS 2022.2-SP6.4 的工程解析、Compile 和 Synthesize 已成功；综合报告 `Total LUTs: 18222 of 42800 (42.57%)`，该数字不代表布局布线后的时序结果。实机已通过 RK3568 本机双源回环 300 帧校验；用户在 2026-10-24 反馈半帧接收中断未再出现，当前问题是 HDMI 左右循环错位，详见第 10 节。完整记录与 FNV 校验通过仍不能证明源像素位置正确，画面内容、长期稳定性和最终时序仍需验收。

| 现象 | 下一步 |
|---|---|
| `program the RGB565 HDMI + OV5640 DMA bitstream first` | 确认烧录的是本次新位流，且 `PCIE_RGB565=1` |
| `.ko` 在 `lsmod` 中但无设备节点 | 检查 PCI 设备的 `driver` 链接及本次 probe 日志；成功 insmod 不等于成功绑定 |
| `Device or resource busy` | 停止旧服务/另一个双源快照进程；同一 PCI 设备只能绑定一个驱动 |
| `video DMA timeout` | 保留 `source`、`status` 和相机诊断；驱动已禁止后续采集，需要复位 FPGA、重新枚举、重新加载驱动 |
| 摄像头完整帧计数递增，但快照超时 | 输入采集诊断已通过；重点检查本次 DDR 缓存、读请求和 PCIe 状态 |
| 快照内容/颜色异常 | 用 OV5640 色条或红绿蓝物体检查 RGB565 字节序、行序；输入完整性检查无法替代颜色检查 |
| 双源实时速度低 | 先记录 `profile=1` 的两路 `capture_us`；两路串行 DMA、软件校验、HDC 带宽都会影响速度，本阶段不承诺两路同时 30 fps |
| HDC `[Fail]Device not founded or connected` | 先恢复 `list targets`、`shell id`；不要把连接失败等同于 FPGA 逻辑失败 |

## 10. HDMI 左右错位：2026-10-24 排查记录

本次录屏中双源画面连续刷新，HDMI 桌面内容出现水平方向循环错位。用户确认活动信号为 1920×1080、60 Hz。该现象与此前帧记录接收中断分开验收；收到完整记录且 FNV 一致，仍可能收到位置已经错误的源像素。

检查发现 `src/hdmi_loop.fdc` 没有 HDMI 像素时钟约束。2026-10-15 的布局布线报告将 `hdmi_loop|pixclk_in` 按 1 MHz、1000 ns 分析，而标准 1080p60 为 148.5 MHz、约 6.734 ns。现已增加 `pixclk_in` 的实际周期与异步时钟分组。该修改属于必须补齐的时序约束，尚不能据此确定它是录屏错位的唯一原因。

HDMI 写入回归现增加 148.5 MHz 像素时钟、100 MHz DDR 和每行 280 拍消隐，检查 1920 像素行、行缓存回绕、摄像头写入竞争、帧边界暂停和恢复、冻结帧的全部像素。该用例及现有双源仿真通过，未在这些行为仿真场景中复现左右错位。

2026-10-24 重新运行 Compile、Synthesize、Place & Route 和 Report Timing 后：

| 项目 | 结果与边界 |
|---|---|
| HDMI 时钟识别 | `pixclk_in` 为 148.5001 MHz、6.734 ns |
| HDMI 时钟域 setup WNS | `+0.001 ns`；最差路径在原灰度 UDP 行缓存，裕量很小 |
| HDMI 时钟域 hold WHS | `-0.084 ns`；报告列出的最差路径在原 UDP 行缓存 |
| DDR 时钟域 setup WNS | `+0.277 ns` |
| 全工程状态 | `Some Constraints Violated`，仍有其他时钟域和 hold/复位路径违例 |
| HDMI 输入外部时序 | RGB、DE、VS 等端口尚缺外部输入延迟约束，不能称为完整的输入时序验收 |

PDS 已于 2026-10-24 18:42:41 完成 Generate Bitstream，进程返回 0，新文件位于 FPGA 工程的 `generate_bitstream/hdmi_loop.sbit`。本次位流用于上板对比，不能宣称全工程时序已经收敛或左右错位已经修复。旧位流由 PDS 保存在 FPGA 工程的 `generate_bitstream/bak/hdmi_loop.sbit`。这些改动不改变驱动接口或流协议，不需要更新 RK3568 的 `.ko`、发送程序或 Ubuntu 接收程序。

上板对比时使用静止的 HDMI 桌面，预览窗口按 `S` 保存当前拼接图。若保存的 PNG 也有错位，继续检查显示前的像素路径；若 PNG 正常而窗口错位，检查 Ubuntu 图形显示路径。进一步区分拼接和采集时，先关闭预览窗口，再采一张独立 HDMI 快照：

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
export FPGA_BDF="0002:21:00.0"
cd "$HOME/pcie_host"
sudo "$HDC" -t "$TARGET" shell sh /data/local/tmp/stop_multivideo_server.sh
HDMI_CHECK_TAG="hdmi_alignment_$(date +%Y%m%d_%H%M%S)"
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_multivideo_server \
    --device "/dev/pango_pcie_multivideo_$FPGA_BDF" \
    --source hdmi --snapshot "/data/local/tmp/$HDMI_CHECK_TAG"
sudo "$HDC" -t "$TARGET" file recv \
    "/data/local/tmp/${HDMI_CHECK_TAG}.ppm" \
    "$HOME/pcie_host/${HDMI_CHECK_TAG}.ppm"
wc -c "$HOME/pcie_host/${HDMI_CHECK_TAG}.ppm"
```

完整单源 HDMI PPM 为 1920×1080、6220817 字节。确认长度正确后用 `xdg-open "$HOME/pcie_host/${HDMI_CHECK_TAG}.ppm"` 查看。单源快照也错位时，故障已存在于拼接之前，但仍需要进一步区分 HDMI 输入、FPGA 帧缓存、DMA 与 RK 驱动；不能仅凭快照就排除其中任一环节。

## 11. 性能优化第 1 步：分段计时

驱动版本 `1.1` 扩展 `profile=1`，记录准备、填充、寄存器设置、DMA 等待、复制、FNV、收尾与记录交付。服务版本 `multivideo-profile-v4` 增加 `--profile`，记录两路头部读取、像素读取、用户态 FNV、拼接、输出 FNV 和 socket 发送。协议和采集顺序保持兼容，暂不引入流水、删除校验或修改 FPGA。完整同步、编译、加载与 30 帧采集步骤见 [MULTIVIDEO_PROFILE.md](MULTIVIDEO_PROFILE.md)。驱动耗时包含在服务读取耗时内，两层不能相加；计时用于选择下一步优化对象，本身不代表已提高帧率。

## 12. 性能优化第 2 步：缓存型 DMA 缓冲

驱动 `1.2` 增加加载参数 `cached_dma=1`，尝试普通可缓存连续页面的 streaming DMA 映射，在采集前后执行 DMA 缓存同步，优先验证降低 DMA 缓冲读取和复制耗时。默认 `cached_dma=0` 保留原分配路径；分配或映射失败会回落并打印实际模式。服务继续使用 `multivideo-profile-v4`，FPGA 位流和协议无需更新。实测基线、完整同步与编译、实际模式确认、30 帧测量及回退命令见 [MULTIVIDEO_DMA_CACHE.md](MULTIVIDEO_DMA_CACHE.md)。比较复制与缓存同步的合计耗时及整轮接收时间，不能仅凭校验通过认定缓存内容新鲜或画面位置正确。

## 13. 性能优化第 3 步：CPU 调频策略对比

新增 `check_multivideo_cpu.sh`，使用现有服务的板内流校验功能，分别在原 CPU governor 和临时 `performance` governor 下校验同样的 60 帧，并打印耗时和帧率，结束后恢复原 governor。用于确认 CPU 调频对剩余校验及图像处理开销的影响，本身不修改校验算法或保证提速。脚本实际行为、同步命令、成功标识、异常恢复及测试边界见 [MULTIVIDEO_CPU_PROFILE.md](MULTIVIDEO_CPU_PROFILE.md)。板内接收校验也占用 RK CPU，A/B 的 fps 不能直接当作 Ubuntu 预览 fps。

一次实机板内 60 帧对比中，原 `schedutil` 为 10.71 秒、临时 `performance` 为 6.87 秒，两组均通过并成功恢复原策略。脚本 v3 增加 `hold 180`，限时保持策略且不占用视频客户端，用于按相同流程进一步核实 Ubuntu 接收与窗口预览收益，到时自动恢复。

随后 Ubuntu 各 60 帧均校验通过，耗时为 10.37 / 6.94 秒，用户确认预览改善及原策略恢复。

## 14. 性能优化第 4 步：重叠 HDMI 校验和摄像头采集

服务 `multivideo-overlap-v5` 增加双源可选参数 `--overlap-verify`，读取 HDMI 后启动工作线程计算同一份完整 FNV 校验，主线程同时读取并校验摄像头，两路都成功且工作线程结束后才拼接、校验和发送。默认仍串行，保留现有每帧校验、协议、驱动和 FPGA。新增 `verify_wait_us` 记录摄像头处理后等待工作线程的时间，重叠模式不能直接把各段时间相加。部署、离线负例验证、同频率下的串行/重叠对比及回退见 [MULTIVIDEO_OVERLAP.md](MULTIVIDEO_OVERLAP.md)。该优化尚待 RK 实机验证收益。

## 15. 性能优化第 5 步：摄像头提前采集

只读诊断实测 OV5640 有效输入为 26.288 帧/秒、平均间隔 38.040 ms、0 个坏帧；最近内核样本中摄像头请求到检测完成平均 64.296 ms，占其驱动记录准备时间约 96.5%。FPGA 顶层增加 `CAMERA_DDR_PREFETCH=1`：无请求时背景采集，请求到来后在有效完整帧写完且摄像头确认冻结后上传，释放后恢复背景采集。保留原 DDR 地址和 RK 接口，原模式可用参数 0 或保存的旧位流回退。15 个仿真配置通过，PDS 已完成位流生成，独立文件为 FPGA 工程根目录的 `hdmi_loop.camera_prefetch_v1.sbit`；全工程仍有时序违例，作为可回退的对比版本。实现、最终时序边界、位流 hash，以及原位流/新位流使用同一 v5 服务与 CPU 策略的板内对比步骤见 [CAMERA_PREFETCH.md](../../06_hdmi_loop_1/CAMERA_PREFETCH.md)。

新位流首次反馈：Ubuntu 30 帧全部校验通过，总耗时 2.33 秒，整轮约 12.88 帧/秒；对应内核最近两路各 10 帧中，摄像头 `capture_us` 均值 34.325 ms。用户提供临时 `performance` 保持已就绪并反馈预览流畅。旧样本缺少同时刻 governor，尚未收到完整原位流/新位流 CPU 对照，不能据此给出严格的 FPGA 提速百分比。

用户随后明确确认当前版本的流畅度已经满意，本轮流畅度优化结束，采用摄像头预取位流、驱动 `1.2 cached_dma=1`、v5 服务 `--overlap-verify` 和预览时临时 `performance` 的配置作为后续开发基线。300 帧连续校验与完整性能对照可按需补充，不作为当前流畅度验收的前置条件；基线配置见上述文档末节。
