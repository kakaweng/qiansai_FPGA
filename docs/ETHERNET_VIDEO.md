# 第三路图像：主机以太网输入 → FPGA → PCIe → RK3568

本版扩展现有双源设计，拼接继续在 RK3568 上完成。FPGA 负责三路采集、独立缓存和 PCIe 上传。

```text
HDMI ──────────────────┐
OV5640 ────────────────┼─ FPGA DDR → PCIe → RK3568 拼接 → HDC → Ubuntu 预览
Windows / Ubuntu Python → FPGA 网口 ─┘
```

## 版本与画面

| 项目 | 本版 |
|---|---|
| FPGA 位流 | `06_hdmi_loop_1/hdmi_loop.ethernet_video_v1.sbit`，以实际构建记录为准 |
| 内核驱动 | `pango_pcie_multivideo.c`，模块版本 `1.3`；需要在 Ubuntu 重新编译签名 |
| 板端服务 | `pcie_multivideo_server.ethernet_v6`，`--version` 为 `multivideo-ethernet-v6` |
| 网络发送 | `send_ethernet_video.py`，标准库即可发送彩条、RGB565 原始文件和移动条 |
| 只读网络状态 | `pcie_ethernet_status`，AArch64 静态程序 |
| 保留双源 | `--source both`；旧 `pcie_multivideo_server` 和预取基线位流保留 |
| 第三路输入 | 固定 640×480、RGB565 小端；默认 FPGA IP `192.168.1.10` |
| 三路输出 | 固定 1280×480、RGB565；每帧仍为 1,228,800 字节 |

位流已生成，2101696 字节，SHA256 为 `58e2c6b17e13a282ffb67b8908568088ce78aa04bf16b49f1bb070f670774626`。完整资源与时序记录见 [ETHERNET_VIDEO_BUILD.md](../../06_hdmi_loop_1/ETHERNET_VIDEO_BUILD.md)。

三路布局：HDMI 在左侧，显示区域 640×360，垂直居中；右上为 OV5640，右下为 Ethernet，两幅均缩小到 320×240，并在右侧 640 像素区域内水平居中。三路模式的输出 source 为 `4294967294`；原双路仍为 `4294967295`。

第三路尚无完整帧时，其窗口为黑色，另外两路继续运行。网络停止后保留第三路最后一幅完整图。没有跨三路的同步曝光保证，输出使用各源采集到的帧。

## 1. 接线、下载与网络

1. 主机的**有线网卡连接 FPGA 网口**。原 HDC 连接继续连接 RK3568。这里以太网是新增的输入，不是替换 HDC 输出。
2. 下载第三路新位流，保持 FPGA 供电，再重启 RK3568，让 PCIe 重新枚举。不要在位流重配置后沿用旧 PCIe/驱动状态。
3. 推荐先在 Windows 发图，Ubuntu 虚拟机继续负责 HDC 和预览，可避免先调整 VMware 网络。
4. 为直接连接 FPGA 的专用有线网卡设置 IPv4 `192.168.1.102`、掩码 `255.255.255.0`；无需网关或 DNS。不要把 Ubuntu 的 VMware NAT 网卡 `ens33` 当作 FPGA 的物理连接。

Windows PowerShell 查看网卡：

```powershell
Get-NetAdapter | Format-Table Name, Status, LinkSpeed, InterfaceDescription
Get-NetIPAddress -AddressFamily IPv4 | Format-Table InterfaceAlias, IPAddress, PrefixLength
```

在 Windows“网络连接”中找到连接 FPGA 的网卡，设置上述静态地址。该网段需要与其他正在使用的网络避免冲突。FPGA 默认 MAC 为 `00:11:22:33:44:55`；现有 ARP 模块负责应答，FPGA 不实现 ICMP，`ping` 失败不能单独判定接收失败。

如果由 Ubuntu 发图，先让直接连接 FPGA 的物理网卡进入虚拟机，或为该网卡建立 VMware 桥接，再在这个接口设置同网段地址。通过 `ip -br link` 和 `ip -4 addr show` 确认接口。下面为管理员配置专用接口的示例，接口名必须替换为实际名称：

```bash
ip -br link
ip -4 addr show
FPGA_IF="替换为直接连接FPGA的接口名"
sudo ip link set dev "$FPGA_IF" up
sudo ip addr add 192.168.1.102/24 dev "$FPGA_IF"
```

## 2. Ubuntu 终端 A：恢复环境并同步文件

以下同步目录沿用现有 VMware 共享文件夹。如果第一条 `ls` 报不存在，先恢复共享文件夹挂载，再继续；不要使用旧驱动配新协议。

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
export FPGA_BDF="0002:21:00.0"
export KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10"
export LLVM_DIR="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin"
export PATH="$LLVM_DIR:$PATH"
cd "$HOME/pcie_host"

sudo "$HDC" list targets
sudo "$HDC" -t "$TARGET" shell id
sudo "$HDC" -t "$TARGET" shell cat /proc/sys/kernel/random/boot_id
sudo "$HDC" -t "$TARGET" shell ls /sys/bus/pci/devices

ls -lh /mnt/hgfs/pcie_host/pcie_multivideo_server.ethernet_v6 \
    /mnt/hgfs/pcie_host/pango_pcie_multivideo.c \
    /mnt/hgfs/pcie_host/send_ethernet_video.py

sync_ethernet_video_files()
{
    local filename
    for filename in \
        Makefile pango_pcie_multivideo.c pcie_multivideo_protocol.h \
        pcie_multivideo_server.c video_mosaic.c video_mosaic.h \
        build_multivideo.sh pcie_multivideo_server.ethernet_v6 \
        pcie_ethernet_status.c pcie_ethernet_status \
        multivideo_stream.py preview_multivideo.py frame_to_png.py frame_format.py \
        send_ethernet_video.py check_multivideo_cpu.sh \
        stop_multivideo_server.sh ETHERNET_VIDEO.md; do
        cp -v -- "/mnt/hgfs/pcie_host/$filename" "$HOME/pcie_host/" || return 1
        cmp -- "/mnt/hgfs/pcie_host/$filename" "$HOME/pcie_host/$filename" || return 1
    done
}
sync_ethernet_video_files
```

## 3. 终端 A：编译驱动、上传程序、加载

不要在新驱动编译失败后继续执行上传步骤。

```bash
make -C "$KDIR" M="$PWD" \
    ARCH=arm64 LLVM=1 LLVM_IAS=1 \
    pango_pcie_multivideo.ko && \
"$KDIR/scripts/sign-file" sha1 \
    "$KDIR/certs/signing_key.pem" \
    "$KDIR/certs/signing_key.x509" \
    pango_pcie_multivideo.ko
```

编译和签名成功后：

```bash
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/stop_multivideo_server.sh" \
    /data/local/tmp/stop_multivideo_server.sh
sudo "$HDC" -t "$TARGET" shell sh /data/local/tmp/stop_multivideo_server.sh
sudo "$HDC" -t "$TARGET" shell lsmod
```

刚重启且 `lsmod` 为空时无需卸载。若有旧 `pango_pcie_multivideo`，在服务停止后执行一次：

```bash
sudo "$HDC" -t "$TARGET" shell rmmod pango_pcie_multivideo
```

然后上传并加载新文件：

```bash
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pango_pcie_multivideo.ko" \
    /data/local/tmp/pango_pcie_multivideo.ko
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pcie_multivideo_server.ethernet_v6" \
    /data/local/tmp/pcie_multivideo_server.ethernet_v6
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pcie_ethernet_status" \
    /data/local/tmp/pcie_ethernet_status
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/check_multivideo_cpu.sh" \
    /data/local/tmp/check_multivideo_cpu.sh

sudo "$HDC" -t "$TARGET" shell chmod 755 \
    /data/local/tmp/pcie_multivideo_server.ethernet_v6 \
    /data/local/tmp/pcie_ethernet_status
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_multivideo_server.ethernet_v6 --version
sudo "$HDC" -t "$TARGET" shell \
    insmod /data/local/tmp/pango_pcie_multivideo.ko profile=1 cached_dma=1
sudo "$HDC" -t "$TARGET" shell cat /sys/module/pango_pcie_multivideo/version
sudo "$HDC" -t "$TARGET" shell ls -l "/dev/pango_pcie_multivideo_$FPGA_BDF"
```

应分别看到 `multivideo-ethernet-v6`、`1.3` 和字符设备。HDC 传输出现失败或中断时，先重连，再重新上传该文件；不能只凭进程退出码判断传输完整。

## 4. Windows 终端：先发送静态彩条

```powershell
Set-Location 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host'
python send_ethernet_video.py --self-test
python send_ethernet_video.py --host 192.168.1.10 --bind 192.168.1.102 --fps 5 --packet-gap-us 100 --save-expected ethernet_expected.rgb565
```

保持发送程序运行。程序输出 `submitted=` 只代表 UDP 已交给主机系统，接收是否成功以 FPGA 计数和 PCIe 回读结果为准。`--fps` 是发送上限，实际帧率会受 Python、系统调度和包间隔影响。

若改用 Ubuntu 发图，同步文件后执行：

```bash
cd "$HOME/pcie_host"
python3 send_ethernet_video.py --host 192.168.1.10 \
    --bind 192.168.1.102 --fps 5 --packet-gap-us 100 \
    --save-expected ethernet_expected.rgb565
```

## 5. 终端 A：检查接收并做逐字节回读验证

```bash
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_ethernet_status "$FPGA_BDF" 10
```

预期 `available=1`，`input_frame_id`、`complete_frames` 持续增加。无发送时 `available=0` 是正常的。能力不存在表示仍在运行旧位流或 PCIe 状态尚未重新建立。

计数正常后，在静态彩条持续发送期间执行：

```bash
ETH_TAG="ethernet_check_$(date +%Y%m%d_%H%M%S)"
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_multivideo_server.ethernet_v6 \
    --device "/dev/pango_pcie_multivideo_$FPGA_BDF" \
    --source ethernet \
    --snapshot "/data/local/tmp/$ETH_TAG" && \
sudo "$HDC" -t "$TARGET" file recv \
    "/data/local/tmp/${ETH_TAG}_ethernet.rgb565" \
    "$HOME/pcie_host/${ETH_TAG}_ethernet.rgb565" && \
python3 send_ethernet_video.py \
    --verify-received "$HOME/pcie_host/${ETH_TAG}_ethernet.rgb565"
```

应得到 `614400 字节完全一致`。默认彩条由同一段确定性 Python 代码生成，所以可以在 Windows 发送、Ubuntu 验证。图片或 `--raw` 输入必须在验证时提供**相同的源文件参数**。验证失败时不判定通过，先检查文件是否被 HDC 截断、是否仍在发送同一静态图。

需要查看第三路完整大小图像时：

```bash
sudo "$HDC" -t "$TARGET" file recv \
    "/data/local/tmp/${ETH_TAG}.ppm" "$HOME/pcie_host/${ETH_TAG}.ppm"
xdg-open "$HOME/pcie_host/${ETH_TAG}.ppm"
```

## 6. 终端 A：启动三路服务

```bash
sudo "$HDC" -t "$TARGET" shell sh /data/local/tmp/stop_multivideo_server.sh
STREAM_LOG="/data/local/tmp/ethernet_all_$(date +%Y%m%d_%H%M%S).log"
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_multivideo_server.ethernet_v6 \
    --device "/dev/pango_pcie_multivideo_$FPGA_BDF" \
    --source all --overlap-verify \
    --serve --port 9002 --send-timeout-ms 30000 \
    --profile --daemon --log "$STREAM_LOG"
sudo "$HDC" -t "$TARGET" shell tail -n 10 "$STREAM_LOG"
sudo "$HDC" -t "$TARGET" fport ls
ss -ltn 'sport = :9004'
```

若已有 `tcp:9004 tcp:9002` 映射，直接复用；没有映射且 Ubuntu 9004 空闲时执行：

```bash
sudo "$HDC" -t "$TARGET" fport tcp:9004 tcp:9002
```

若报告端口已占用，先核对 `fport ls` 和 `ss`，不要重复创建同一监听。HDC 掉线后转发可能消失，恢复映射即可；这不等同于 FPGA 采集失败。

## 7. Ubuntu 终端 B：临时 performance

沿用已验证有效的临时策略，窗口结束后自动恢复原 governor，不修改开机策略：

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
sudo "$HDC" -t "$TARGET" shell \
    sh /data/local/tmp/check_multivideo_cpu.sh hold 300
```

看到 `CPU_HOLD_READY` 后，在终端 A 继续下一步。结束后确认 B 输出 `CPU_GOVERNORS_RESTORED`。

## 8. 终端 A：30 帧检查与预览

```bash
cd "$HOME/pcie_host"
/usr/bin/time -f 'TRIPLE_HOST_SECONDS=%e' \
    python3 preview_multivideo.py \
    --host 127.0.0.1 --port 9004 \
    --headless --count 30 --timeout 60
```

出现 30 帧 PASS 后打开窗口：

```bash
python3 preview_multivideo.py --host 127.0.0.1 --port 9004 --timeout 60
```

保持 Windows 发送端运行。先确认三窗均正确，再停止静态发送，用移动条检查更新：

```powershell
python send_ethernet_video.py --host 192.168.1.10 --bind 192.168.1.102 --fps 10 --packet-gap-us 100 --animate
```

发送静态照片（需安装 Pillow）：

```powershell
python -m pip install Pillow
python send_ethernet_video.py --host 192.168.1.10 --bind 192.168.1.102 --image 'D:\images\test.png' --fps 5
```

`--image` 会缩放到 640×480；`--raw` 必须正好 614400 字节且为 RGB565 小端。当前工具用于彩条、图片和移动图案验证，不采集桌面或解码视频文件。

## 缓存和协议约束

| 项目 | 定义 |
|---|---|
| 应用协议 | EVF1，24 字节头 + 1024 字节像素，每帧 600 包 |
| 头字节序 | 大端：magic、frame_id、offset、length、flags、frame_bytes、CRC32 |
| 像素字节序 | RGB565 小端 |
| CRC32 | 与 `zlib.crc32(prefix20 + payload1024)` 一致，保护帧号、偏移及像素 |
| 网络 | IPv4、IHL=5、不分片、MTU 1500；默认 UDP 5002 |
| 丢包/乱序/重复 | 当前帧放弃，从下一个 offset=0 恢复；无 ACK、无重传 |
| 完整帧保护 | DDR 三槽，分别用于新帧写入、最新完整帧和 PCIe 锁定帧 |
| DDR 地址 | 控制器地址以 4 字节为单位；Ethernet 槽基址 `0x0900000`，步长 `0x0040000` |
| 输入帧号 | `pcie_ethernet_status` 的 `input_frame_id` 来自主机；服务日志 `ethernet_capture_seq` 为 PCIe 采集次数 |
| abandoned_frames | DDR 层被下一 START 取代的未完成帧/异常结束计数，不是 UDP 丢包数或 CRC 错包数 |

现有 UDP 入口为兼容原控制功能不按目的端口过滤；新图像仅接受 EVF1 + 正确长度/偏移/CRC。应用层 CRC 用于拒绝损坏数据；此版本没有新增完整 Ethernet FCS、IP/UDP 头校验和验证器。应在专用、可信的直连网络调试。

## 本地验证与板端边界

本地仿真覆盖真实 GMII/IPv4/UDP 解码、640×480 完整帧逐字节 DDR 数据、缓存锁定与新帧连续发布、损坏/短包/超长包/分片/乱序/重复/背压恢复；PCIe 第三源测试覆盖 300 块上传、DDR 基址和源切换。Python 的本机 UDP 600 包收发及图像比对、三路 C 画布逐像素检查已执行。

Windows 可以交叉编译 AArch64 服务和工具，不能在本机加载 RK3568 内核模块。内核编译、签名、加载、网口实收、端到端图像比对及三路实际帧率必须以上板结果为准。旧工程已有时序违例，第三路构建结果和剩余时序风险见 FPGA 工程的 `ETHERNET_VIDEO_BUILD.md`。

可选板端离线测试（不访问 PCIe）：复制 `verify_multivideo_ethernet`、`verify_multivideo_overlap.ethernet_v6`，chmod 755 后分别运行，参数均可指定 `/data/local/tmp`。这些测试会故意注入错误，预期日志包含 checksum/EOF，最后必须出现 PASS。

## 回退双源

停止服务后，用本版服务的 `--source both --overlap-verify` 即可在新位流上回到双源布局。完整硬件回退使用保留的 `hdmi_loop.camera_prefetch_v1.sbit`，下载后重启 RK3568；驱动 1.3 仍兼容旧双源能力标识，服务使用 `both`。旧 `pcie_multivideo_server` 文件未被新 v6 二进制覆盖。
