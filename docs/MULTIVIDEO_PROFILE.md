# 双源视频性能优化：第 1 步，分段计时

2026-10-24 的基线为 30 帧 / 6.01 秒，约 4.99 fps。末尾 15 组内核日志中，HDMI 启动到检测完成平均 16.73 ms，摄像头平均 49.56 ms，同源相邻完成日志间隔约 195.5 ms。尚有约 129 ms 未被旧 `capture_us` 覆盖，不能据此将它全部归入 CPU 校验或 HDC。

本步补充分段计时，不改变源选择、DMA 顺序、图像内容、校验算法、64 字节记录头或 socket 协议。无需重新生成或烧录 FPGA。它用于确定下一步优化对象，本身不承诺提高帧率。

## 1. 版本和字段

| 组件 | 确认方式 |
|---|---|
| 新驱动 | `modinfo -F version pango_pcie_multivideo.ko` 和板上 `/sys/module/pango_pcie_multivideo/version` 均为 `1.1` |
| 新服务 | `/data/local/tmp/pcie_multivideo_server --version` 为 `multivideo-profile-v4` |
| 驱动计时 | 加载参数 `profile=1` |
| 服务计时 | 启动参数 `--profile`；连接内前 3 帧及每 10 帧打印一条 `profile source=...` |

以下字段单位全部是微秒，除以 1000 为毫秒。它们是墙钟耗时，包含线程被调度出去的时间，不是 CPU 专用计时。

| 驱动字段 | 范围 |
|---|---|
| `prepare_us` | 进入采集到检查完毕，包含锁等待与寄存器检查 |
| `fill_us` | DMA 缓冲区填充值 |
| `submit_us` | 开启 bus master 和设置源、DMA 目标地址等，直到准备写启动寄存器 |
| `capture_us` | 写启动寄存器到检测完成；包含源帧等待、DDR/PCIe 流程和轮询调度 |
| `copy_us` | DMA 缓冲区复制到内核记录缓冲区 |
| `checksum_us` | 设置头部字段及计算源像素 FNV |
| `finish_us` | 完成元数据、关闭 bus master 等收尾 |
| `record_us` | 上述采集各段总和，截止到记录准备完成；不含随后打印日志和 `copy_to_user` |
| `user_copy_us` | 一条记录所有成功 `copy_to_user` 的累计耗时；记录交付完成时，源序号前 3 帧及每 10 帧打印 |

| 服务字段 | 范围 |
|---|---|
| `hdmi_header_us` / `camera_header_us` | 阻塞读取记录头；当前驱动在这里完成采集、内核复制和 FNV，因此包含驱动 `record_us` |
| `hdmi_pixels_us` / `camera_pixels_us` | 从驱动读取完整像素，包含内核到用户空间复制 |
| `hdmi_verify_us` / `camera_verify_us` | 用户程序再次校验源像素 FNV |
| `mosaic_us` | 缩小 HDMI 并拼接两路画面 |
| `output_checksum_us` | 拼接输出 FNV；单源模式为 0，因为转发原始源头部 |
| `send_us` | 将完整记录交给本机 socket；受 socket 缓冲和下游回压影响，不代表 Ubuntu 已经收到或显示 |
| `total_us` | 本轮采集开始到成功提交本机 socket 的总耗时，不包含本轮之后的日志打印、下一轮之间的调度间隙和 Ubuntu 显示 |

服务层各段可以与 `total_us` 对照，舍入和少量未分段代码会产生差值。驱动层字段已经包含在服务的读取时间中，不能把两层时间再相加。用服务 `hdmi_seq` / `camera_seq` 对齐驱动对应的 `source` / `seq`；拼接输出 `sequence` 是另一个计数器，不能假定三者相同。服务记录发送成功后才打印分段行，发送失败时继续保留原来的错误诊断。

## 2. Ubuntu 同步和编译驱动

先关闭预览窗口，保持 FPGA 的已验收位流和当前 RK 启动状态。以下命令在 Ubuntu Bash 中运行。

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
export FPGA_BDF="0002:21:00.0"
export KDIR="$HOME/openharmony/out/kernel/OBJ/linux-5.10"
export LLVM_DIR="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin"
export PATH="$LLVM_DIR:$PATH"
cd "$HOME/pcie_host"

PROFILE_TAG="multivideo_profile_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$HOME/pcie_host/backups/$PROFILE_TAG"
cp -p pango_pcie_multivideo.ko pcie_multivideo_server \
    "$HOME/pcie_host/backups/$PROFILE_TAG/"

sync_profile_files()
{
    local filename
    for filename in pango_pcie_multivideo.c pcie_multivideo_protocol.h \
        pcie_multivideo_server.c pcie_multivideo_server MULTIVIDEO_PROFILE.md; do
        cp -v -- "/mnt/hgfs/pcie_host/$filename" "$HOME/pcie_host/" || return 1
        cmp -- "/mnt/hgfs/pcie_host/$filename" "$HOME/pcie_host/$filename" || return 1
    done
}
sync_profile_files
```

同步必须全部成功；共享目录不可用时先恢复挂载。服务 ARM64 静态可执行文件已在 Windows 工程中重新生成，Ubuntu 无需为本步重编它。驱动必须使用 Ubuntu 上与 RK 内核匹配的构建目录编译、签名：

```bash
make -C "$KDIR" M="$PWD" \
    ARCH=arm64 LLVM=1 LLVM_IAS=1 \
    pango_pcie_multivideo.ko && \
"$KDIR/scripts/sign-file" sha1 \
    "$KDIR/certs/signing_key.pem" \
    "$KDIR/certs/signing_key.x509" \
    pango_pcie_multivideo.ko

modinfo -F version pango_pcie_multivideo.ko
modinfo -F vermagic pango_pcie_multivideo.ko
modinfo -F signer pango_pcie_multivideo.ko
file pcie_multivideo_server
```

应确认驱动版本 `1.1`、vermagic 对应 `5.10.184 ... aarch64`、签名者非空，服务是 ARM aarch64 静态 ELF，且编译和签名均未报错。

## 3. 停服务、替换程序和重新加载驱动

```bash
sudo "$HDC" list targets
sudo "$HDC" -t "$TARGET" shell id
sudo "$HDC" -t "$TARGET" shell \
    sh /data/local/tmp/stop_multivideo_server.sh
sudo "$HDC" -t "$TARGET" shell rmmod pango_pcie_multivideo
```

若 RK 刚重启且模块尚未加载，`rmmod` 会提示模块不存在；确认 `lsmod` 后可以继续。若提示模块正在使用，先处理仍在运行的采集进程。

```bash
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pango_pcie_multivideo.ko" \
    /data/local/tmp/pango_pcie_multivideo.ko
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pcie_multivideo_server" \
    /data/local/tmp/pcie_multivideo_server
sudo "$HDC" -t "$TARGET" shell \
    chmod 755 /data/local/tmp/pcie_multivideo_server
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_multivideo_server --version
sudo "$HDC" -t "$TARGET" shell \
    insmod /data/local/tmp/pango_pcie_multivideo.ko profile=1
sudo "$HDC" -t "$TARGET" shell \
    cat /sys/module/pango_pcie_multivideo/version
sudo "$HDC" -t "$TARGET" shell \
    cat /sys/module/pango_pcie_multivideo/parameters/profile
sudo "$HDC" -t "$TARGET" shell \
    ls -l "/dev/pango_pcie_multivideo_$FPGA_BDF"
```

每次传输都应看到完成输出。继续前确认板端服务为 `multivideo-profile-v4`、模块为 `1.1`、profile 为 `Y`，并且设备节点存在。若出现 DMA 超时、`needs_reset` 或描述不匹配，应先恢复 FPGA/PCIe 状态，不要通过反复卸载驱动掩盖故障。

## 4. 采集同样的 30 帧并保存两份日志

```bash
STREAM_LOG="/data/local/tmp/${PROFILE_TAG}.log"
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_multivideo_server \
    --device "/dev/pango_pcie_multivideo_$FPGA_BDF" \
    --source both --serve --port 9002 \
    --send-timeout-ms 30000 --profile \
    --daemon --log "$STREAM_LOG"
sudo "$HDC" -t "$TARGET" shell tail -n 5 "$STREAM_LOG"
sudo "$HDC" -t "$TARGET" fport tcp:9002 tcp:9002

/usr/bin/time -f 'HOST_TOTAL_SECONDS=%e' \
    python3 preview_multivideo.py \
    --host 127.0.0.1 --port 9002 \
    --headless --count 30 --timeout 60 \
    2>&1 | tee "$HOME/pcie_host/${PROFILE_TAG}.host.log"

sudo "$HDC" -t "$TARGET" shell cat "$STREAM_LOG" | \
    tee "$HOME/pcie_host/${PROFILE_TAG}.server.log"
sudo "$HDC" -t "$TARGET" shell dmesg | \
    grep -E 'pango_pcie_multivideo.*(record_us=|record_delivery=)' | \
    tail -n 100 | tee "$HOME/pcie_host/${PROFILE_TAG}.kernel.log"
```

服务启动日志应显示 `profile=on`。发送到 socket 不等于接收端已完整收帧，因此统计仍以客户端 `PASS` 与接收耗时为准。客户端到 30 帧主动退出后，服务可能已开始下一帧并遇到 `Broken pipe`，需要结合客户端退出时点判断。

将 `.host.log`、`.server.log`、`.kernel.log` 用于分析。新服务可配旧驱动，但缺少驱动细分字段；本步需要两者都更新。使用同样的视频输入和电源设置进行比较，优先看第 10/20/30 帧的稳态数据；日志与计时本身也会引入少量开销。后续正式帧率验收应关闭服务 `--profile`，并把驱动 `profile` 设为 `0` 再测。

## 5. 本地验证边界

本地已使用 Zig 0.13.0，以 `-target aarch64-linux-musl -static -O2 -std=c11 -Wall -Wextra -Werror` 完成服务交叉编译，退出码为 0。ELF 检查确认 ARM64、小端、无动态解释器，含 v4 版本和分段计时标识；文件为 1,216,832 字节，SHA-256 为 `6a6b6ae613b8e86e1376a03137f52d7ed6c7114cae57557b914c4e9ff24ac4e1`。`python -m unittest verify_multivideo.py` 的 9 项协议回归通过。驱动 `.ko` 的实际编译、签名、加载与硬件运行验证由上述 Ubuntu/RK 步骤完成；Windows 上的服务交叉编译和协议测试不能代替驱动或实机验收。
