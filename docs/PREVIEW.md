# 阶段 4D：HDMI 经 PCIe 的实时窗口预览

彩色升级见 [COLOR.md](COLOR.md)。新版接收端支持原始 RGB565LE 以及 V4 彩色同帧识别；必须同步新增 `frame_format.py`。本页早期 GRAY8 性能与字节数是灰度实机记录。彩色原始流每帧为 40 + 4,147,200 字节。

每帧耗时测量及新一天重新编译/签名/启动的完整步骤见 [TIMING.md](TIMING.md)。

连续 RKNN 识别和带框实时窗口见 [RKNN.md](RKNN.md#阶段-5b5c连续-rknn-识别与实时带框预览)。新版窗口兼容原始预览和识别结果，使用 `--require-detections` 可以确认连接的是识别服务。

## 目标与数据路径

```text
HDMI → FPGA DDR → PCIe DMA → RK3568 stream 字符设备
                                  ↓
                         pcie_stream_server（C）
                                  ↓ TCP 二进制流
                         HDC USB 端口转发
                                  ↓
                         preview_stream.py（Ubuntu）
                                  ↓
                         OpenCV 实时窗口
```

| 项目 | 说明 |
| --- | --- |
| RK3568 程序 | `pcie_stream_server.c`：循环读取、校验并发送帧；板端无需 Python/OpenCV |
| 已编译程序 | `pcie_stream_server`：ARM64 Linux 静态 ELF，无动态加载器依赖，可直接发送到板端 |
| Ubuntu 程序 | `preview_stream.py`：后台收帧并校验，主线程显示最新帧 |
| 帧格式 | 40 字节小端记录头 + 2,073,600 字节 GRAY8 像素 |
| 连接 | 默认板端 `127.0.0.1:9000`，通过 HDC 转发至 Ubuntu `127.0.0.1:9000` |
| 图像 | 接收完整 1920×1080 灰度帧，默认按 960×540 显示 |
| 窗口操作 | `Q` / `Esc` / 关闭窗口退出；`S` 保存原始分辨率 PNG 和 PGM |
| 断线处理 | 保留最后完整帧并标记断线，每 2 秒尝试重连；半帧丢弃，不显示为新图像 |
| 板端日志 | 后台运行时写入 `/data/local/tmp/pcie_preview_server.log`，记录发送字节数和错误原因 |
| FPGA/驱动 | 沿用已通过连续 30 帧测试、修复重复打开问题的位流及 `.ko` |

这是实时接收新帧，不需要先写入板端文件再下载。`HDC shell` 只启动程序；像素从
TCP 端口转发传输，不通过 shell 的标准输出。无需配置 RK3568 与虚拟机的网络 IP。

当前五帧测试的板端完成时间戳约相隔 0.51 秒，预览起初可能只有约 2 fps。
该值是当前整条采集流水线的刷新速度；实时窗口不能提高采集速度，也不代表 HDMI
输入本身的帧率。后续接入 RKNN 后可以在这个预览路径增加识别结果叠加。

## 1. Ubuntu 同步并安装窗口依赖

以下命令在 Ubuntu 图形桌面的终端执行。重新打开的每个终端都要设置 HDC 变量。

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"

sudo mkdir -p /mnt/hgfs
if ! mountpoint -q /mnt/hgfs; then
    sudo vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other
fi

mkdir -p "$HOME/pcie_host"
cp -v /mnt/hgfs/pcie_host/pcie_stream_server.c \
    /mnt/hgfs/pcie_host/pcie_stream_server \
    /mnt/hgfs/pcie_host/build_preview.sh \
    /mnt/hgfs/pcie_host/preview_stream.py \
    /mnt/hgfs/pcie_host/rknn_stream.py \
    /mnt/hgfs/pcie_host/draw_detections.py \
    /mnt/hgfs/pcie_host/capture_stream.py \
    /mnt/hgfs/pcie_host/frame_to_png.py \
    /mnt/hgfs/pcie_host/frame_format.py \
    /mnt/hgfs/pcie_host/frame_checksum.c \
    /mnt/hgfs/pcie_host/build_checksum.sh \
    /mnt/hgfs/pcie_host/PREVIEW.md \
    "$HOME/pcie_host/"

sudo apt-get install python3-opencv python3-numpy
cd "$HOME/pcie_host"
bash build_checksum.sh
python3 -c 'import cv2, numpy; print("OpenCV", cv2.__version__)'
file pcie_stream_server
```

共享目录已包含从这份源码交叉编译的 ARM64 静态可执行文件，可以直接进入第 2 步。

`build_checksum.sh` 在 Ubuntu 本机编译 `libpango_frame_checksum.so`，供 Ubuntu
Python 加速整帧 FNV-1a 校验。它与板端 ARM64 发送程序分别构建，不发送到 RK3568。
构建时验证整帧 `bytes` 和 `bytearray` 的校验值，窗口启动应显示
`校验后端：C/FNV-1a`。库不存在或无法加载时，自动使用原 Python 算法，并在启动
信息中显示原因；此时仍校验每帧，但帧率可能受 Python 校验限制。
它是用户空间程序，不需要 `KDIR`、内核头文件或 `sign-file`。

只有修改发送程序源码后才需要重新编译。可运行 `bash build_preview.sh`，脚本自动
查找 OpenHarmony Native SDK 的 `native/sysroot` 和 clang；也可以用已安装的 Zig：
`PREVIEW_ZIG=/实际路径/zig bash build_preview.sh`（目标 `aarch64-linux-musl`，静态链接）。
当前随附程序使用 Zig 0.13.0 的 clang/musl 编译，已通过 ARM64 ELF 和无动态加载器检查。
编译报错时先解决编译问题，不发送旧程序。板端执行和 USB 转发仍需实机验证。

如果提示找不到 Native SDK，先查找本机已有的 SDK：

```bash
find "$HOME/openharmony/prebuilts/ohos-sdk" \
    -path '*/native/sysroot/usr/include/stdio.h' -print
```

可用 `OHOS_NATIVE=/实际路径/native bash build_preview.sh` 指定它。只有 HDC 或
内核编译工具并不保证已安装 Native SDK；若查找没有结果，需要先补齐兼容的
OpenHarmony Native SDK。不要填入示例占位路径直接执行。

## 2. 确认驱动节点并发送程序

```bash
sudo "$HDC" list targets
sudo "$HDC" -t "$TARGET" shell ls -l /dev/pango_pcie_stream_0002:21:00.0
```

若节点存在且当前驱动就是 30 帧验证成功的版本，可直接继续。
若 RK3568 重启后节点消失，重新加载已经编译并签名的驱动：

```bash
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pango_pcie_hdmi_frame_test.ko" \
    /data/local/tmp/pango_pcie_hdmi_frame_test.ko
sudo "$HDC" -t "$TARGET" shell insmod /data/local/tmp/pango_pcie_hdmi_frame_test.ko
sudo "$HDC" -t "$TARGET" shell ls -l /dev/pango_pcie_stream_0002:21:00.0
```

更新可执行程序前先停止旧进程，避免覆盖正在运行的文件；这会结束当前预览连接。
确认当前没有正在读取 stream 的其他程序：

```bash
sudo "$HDC" -t "$TARGET" shell \
    'if pidof pcie_stream_server >/dev/null; then killall pcie_stream_server; fi'
sudo "$HDC" -t "$TARGET" shell pidof pcie_stream_server
```

第二条没有 PID 输出后，再发送新用户空间程序并建立转发：

```bash
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pcie_stream_server" \
    /data/local/tmp/pcie_stream_server
sudo "$HDC" -t "$TARGET" shell chmod 755 /data/local/tmp/pcie_stream_server
sudo "$HDC" -t "$TARGET" fport tcp:9000 tcp:9000
```

## 3. 终端 A：后台运行 RK3568 发送程序

```bash
sudo "$HDC" -t "$TARGET" shell /data/local/tmp/pcie_stream_server --daemon
sudo "$HDC" -t "$TARGET" shell pidof pcie_stream_server
sudo "$HDC" -t "$TARGET" shell tail -n 20 /data/local/tmp/pcie_preview_server.log
```

应看到：

```text
Preview server started: pid=实际PID log=/data/local/tmp/pcie_preview_server.log
PCIe preview v2 listening on 127.0.0.1:9000, record_bytes=2073640 pid=实际PID
```

第一条启动命令返回终端提示符是正常现象：发送程序已脱离 HDC shell 会话并在板端
后台运行。程序等待客户端连接后才打开 stream 设备；一次只服务一个客户端。
此时不要同时执行 `dd` 或其他读取同一 stream 节点的命令。也可以省略 `--daemon`
以前台模式运行进行调试，此时需要保持该终端和会话运行。

## 4. 终端 B：运行 Ubuntu 预览窗口

```bash
cd "$HOME/pcie_host"
python3 preview_stream.py
```

| 窗口字段 | 含义 |
| --- | --- |
| `Seq` | 驱动本次绑定期间的成功采集序号，不是 HDMI 源帧序号 |
| `Capture` | 按板端相邻记录完成时间戳计算的采集频率 |
| `Receive` | 按 Ubuntu 相邻记录接收并校验完成时间计算的频率 |
| `Seq gaps` | 本连接收到的驱动序号跳号总数，不是 HDMI 源帧丢失统计 |
| `Display skips` | 窗口处理较慢时，为显示最新画面跳过的已接收帧数 |
| `Last received` | 距离最新一帧接收完成的时间，不是端到端视频延迟 |
| `Disconnected` / `Connecting` | 已断开或正在重连，保留画面属于最后完整帧 |
| `Reconnect attempts` | 本次预览连接失败后重试的次数 |

改变 HDMI 输入画面，窗口应随新帧更新。窗口不会无限积累旧帧：接收线程只保留最新
一帧供显示。网络和驱动本身仍可能存在缓冲，因此不能据此承诺固定延迟。

收到不完整帧或 TCP 断线时，程序清空本次未完成记录，保留最后完整图像，并用红字
标记连接状态。即使初次连接被拒绝，窗口也会等待发送程序恢复；默认每 2 秒重试。
重连只恢复 TCP 客户端，不能自动恢复已经重启的板子、未加载的驱动或失效的 HDC
端口转发。连接重新建立后会重新基准化序号和时间戳；`Seq gaps` 不跨连接统计。
帧头或校验错误仍然作为错误退出，避免将损坏图像当作正常画面。

使用 `--no-reconnect` 可让 TCP 断线立即报错退出，便于定位故障。

按 `S` 保存截图至当前目录的 `preview_snapshots/`，不包含窗口中的文字叠加。
原始图像仍然是 1920×1080，显示缩放不会改变截图像素。

程序只有在检测到窗口曾经可见后，才根据可见性变化判断窗口关闭，避免启动时立即退出。
部分 OpenCV 后端不支持窗口可见性查询，此时会打印提示并继续显示，请使用 `Q`、`Esc`
或终端中的 `Ctrl+C` 退出。

退出窗口后，板端关闭本次 stream 读取并返回等待连接；再次执行 Python 命令可重新
连接。后台发送程序需要显式停止；在已设置 HDC 变量的终端执行：

```bash
sudo "$HDC" -t "$TARGET" shell killall pcie_stream_server
```

## 5. 无窗口检查与故障定位

在没有窗口连接时，可先验证 TCP 帧传输：

```bash
python3 "$HOME/pcie_host/preview_stream.py" --headless --count 5
```

正常时打印接收帧序号、校验和和频率，接收 5 帧后退出。随后启动正常窗口即可。
无窗口检查默认不自动重连，便于检查当前连接是否完整。

### 断线后检查板端和转发

第一次收帧中途 EOF 说明当前 TCP 通道提前结束；随后 `Connection refused` 说明
当时端口没有建立可用连接。这两条信息还不能单独判断板端是否重启。先执行：

```bash
sudo "$HDC" list targets
sudo "$HDC" -t "$TARGET" shell cat /proc/uptime
sudo "$HDC" -t "$TARGET" shell pidof pcie_stream_server
sudo "$HDC" -t "$TARGET" shell ls -l /dev/pango_pcie_stream_0002:21:00.0
sudo "$HDC" -t "$TARGET" shell tail -n 40 /data/local/tmp/pcie_preview_server.log
sudo "$HDC" -t "$TARGET" shell dmesg | tail -n 40
```

| 证据 | 处理 |
| --- | --- |
| 设备不在 HDC targets 中 | 恢复板端连接后再继续，窗口可保持等待 |
| uptime 明显重新从小值开始 | 板子发生过重启；恢复驱动、发送程序和转发，并保留内核日志定位重启原因 |
| 没有发送程序 PID | 检查日志后重新启动 `pcie_stream_server --daemon` |
| stream 节点不存在 | 重新加载已验证的驱动并检查 probe 结果 |
| PID 和节点存在但连接失败 | 重新执行 `hdc -t "$TARGET" fport tcp:9000 tcp:9000` 并查看结果 |
| 日志 `Send failed` | 查看帧序号、已发送字节数、errno；窗口重连并不能消除该错误的根因 |

| 现象 | 检查步骤 |
| --- | --- |
| `Connection refused` | 确认终端 A 正在运行，且 HDC fport 成功 |
| `Address already in use` | 检查是否已启动同一发送程序；HDC 转发与程序分别运行在主机和板端，不冲突 |
| 板端 `Device or resource busy` | 停止读取 stream 节点的 `dd` 或其他预览客户端 |
| 板端设备不存在 | RK3568 重启后重新加载 `.ko`，再检查 stream 节点 |
| 没有图形桌面/GUI 支持 | 在 Ubuntu 图形桌面终端运行；使用 apt 提供的 OpenCV 而不是 opencv-python-headless |
| 连接后马上返回命令提示符、没有窗口 | 更新共享目录中的 `preview_stream.py`；旧版可能将启动时的窗口状态误判为关闭 |
| `--headless --count 5` 校验通过但没有画面 | 这是无窗口检查模式；启动 `python3 preview_stream.py` 才显示窗口 |
| 板端显示 `Broken pipe` | 客户端关闭后发送下一帧发现连接断开；若正常预览启动后立即出现，检查 Ubuntu 端是否提前退出 |
| 读帧超时/校验失败 | 查看板端发送程序日志及 `dmesg`；保留错误后按已有 DMA 故障步骤恢复 |
| 重启或断线后连接失效 | 确认 HDC 在线，恢复驱动、发送程序和端口转发，再启动窗口 |
| 窗口持续显示 `LAST FRAME` | 这不是新采集画面；检查板端日志和转发，恢复后自动更新 |

若要通过局域网连接，可让板端以 `--bind 0.0.0.0 --port 9000` 监听，再在 Ubuntu
使用 `--host RK3568的实际IP`。当前流程默认使用 USB 转发。

## 验收标准

| 检查 | 成功条件 |
| --- | --- |
| TCP 接收 | `--headless --count 5` 正常退出且校验通过 |
| 实时画面 | 窗口内容对应 HDMI 输入，改变输入时画面更新 |
| 反复连接 | 关闭后再次启动窗口正常，RK3568 不重启 |
| 截图 | `S` 生成 1920×1080 PNG/PGM，内容正常 |
| 后续识别 | 完成本阶段后再加入 RKNN 推理及识别框叠加 |
