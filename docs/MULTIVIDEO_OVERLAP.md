# 双源视频性能优化：第 4 步，校验与采集重叠

## 当前基线和变化

驱动 `1.2` 已实际使用缓存型 DMA；服务 v4 下 Ubuntu 同样接收校验 60 帧，原 `schedutil` 为 10.37 秒，临时 `performance` 为 6.94 秒。用户确认窗口改善、第二组结束时仍为 `performance`、最终恢复成功。本步继续保留所有每帧校验，通过重叠不同工作的时间验证进一步收益。

| 模式 | 顺序 |
|---|---|
| v5 默认串行 | 读取 HDMI → 校验 HDMI → 读取摄像头 → 校验摄像头 → 拼接 → 输出校验 → 发送 |
| v5 `--overlap-verify` | 读取 HDMI → 工作线程校验 HDMI，同时主线程读取并校验摄像头 → 等待工作线程且确认两路校验通过 → 拼接 → 输出校验 → 发送 |

服务版本为 `multivideo-overlap-v5`，可选参数仅允许 `--source both`。每帧至多创建一个校验线程并在本帧内回收，不缓存后续帧，不并发读取同一个设备，不提前发送未校验的画面。摄像头读取、格式或校验失败时，也先等待工作线程结束，再返回失败并清理缓冲。线程创建失败会停止采集并打印原因；正常线程无法 join 时直接结束进程，避免继续释放仍可能被线程使用的缓冲。

HDMI 和摄像头完整的 FNV-1a 与头部比对、拼接输出 FNV、Python 接收校验、64 字节协议头、RGB565 格式、1920×1080 / 640×480 源尺寸与 1280×480 拼接尺寸均保留。驱动和 FPGA 无须更新。

`--profile` 增加 `verify_wait_us`，表示摄像头读取和校验完成后，等待 HDMI 校验线程的时间；它包含 join 的开销，不直接等于节省时间。`hdmi_verify_us` 仍表示实际校验线程的墙钟耗时，与摄像头处理部分重叠，因此这些计时不能直接相加。判断收益以同样流程接收完整帧的总耗时为准。

## 本地检查和板端离线检查

本地完成 AArch64 静态编译（`-Wall -Wextra -Werror -pthread`），检查 ELF 架构和静态链接，现有 9 项 Python 协议测试通过。Windows 当前没有 Linux 运行环境，因此新线程回归程序在本地仅完成交叉编译。

2026-10-24 用户在 RK3568 执行 `verify_multivideo_overlap /data/local/tmp`，`overlap-fixture-v2` 的 11 个场景全部 PASS，并出现最终汇总 PASS。正常重叠场景使用真实 pthread 并通过输出一致性和线程回收检查；`Resource temporarily unavailable` 来自最后一个主动注入的线程创建失败场景。这项板端验证没有访问 PCIe，实际视频稳定性和提速仍需后面的两组 60 帧测试确认。

`verify_multivideo_overlap` 是 AArch64 静态离线回归程序，不打开 PCIe 设备。它使用临时文件和人工数据，实际启动 pthread，通过同步门确认摄像头读取与 HDMI 工作线程可以重叠，检查两种模式输出逐字节一致、HDMI 或摄像头像素损坏被拒、摄像头头部错误或截断时回收线程，以及线程创建失败时不输出帧。共 11 个场景，并设置 30 秒超时。中间的 checksum、EOF、header 和 worker 报错是主动注入的负例，最后必须有完整 PASS。

当前离线程序标识为 `overlap-fixture-v2`。旧版调用的 musl `tmpfile()` 固定在 `/tmp` 下创建文件，在本板上创建失败，导致 `Assertion failed: stream`，尚未进入测试。新版使用 `mkstemp`，默认目录为 `/data/local/tmp`，也可用第一个参数指定目录；创建后立即取消文件名，关闭文件或进程结束时回收占用。创建、打开、写入或定位失败会打印具体错误并返回失败。无需创建或修改系统 `/tmp`。

Ubuntu 本机也可从源码构建运行：

```bash
cc -O2 -std=c11 -Wall -Wextra -Werror -pthread \
    verify_multivideo_overlap.c video_mosaic.c \
    -o /tmp/verify_multivideo_overlap
/tmp/verify_multivideo_overlap /tmp
```

本次附带 v5 预编译用户程序。`build_multivideo.sh` 已补充 `-pthread`，目标仍为 AArch64 静态程序。Windows 目录另保存 v4 二进制 `pcie_multivideo_server.profile_v4`，RK 上旧 `/data/local/tmp/pcie_multivideo_server` 保留，v5 发送到独立文件名。

## 同步与发送

关闭 Python 预览，保留当前驱动和服务。先在 Ubuntu 终端 B 执行：

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
export FPGA_BDF="0002:21:00.0"
cd "$HOME/pcie_host"

cp -v -- /mnt/hgfs/pcie_host/pcie_multivideo_server \
    "$HOME/pcie_host/pcie_multivideo_server.overlap_v5" && \
cmp -- /mnt/hgfs/pcie_host/pcie_multivideo_server \
    "$HOME/pcie_host/pcie_multivideo_server.overlap_v5" && \
cp -v -- /mnt/hgfs/pcie_host/verify_multivideo_overlap \
    "$HOME/pcie_host/verify_multivideo_overlap" && \
cmp -- /mnt/hgfs/pcie_host/verify_multivideo_overlap \
    "$HOME/pcie_host/verify_multivideo_overlap" && \
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pcie_multivideo_server.overlap_v5" \
    /data/local/tmp/pcie_multivideo_server.overlap_v5 && \
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/verify_multivideo_overlap" \
    /data/local/tmp/verify_multivideo_overlap
```

确认两次传输完成后检查：

```bash
sudo "$HDC" -t "$TARGET" shell chmod 755 \
    /data/local/tmp/pcie_multivideo_server.overlap_v5 \
    /data/local/tmp/verify_multivideo_overlap
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_multivideo_server.overlap_v5 --version
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/verify_multivideo_overlap /data/local/tmp
```

服务版本应为 `multivideo-overlap-v5`。离线程序开始应有 `Overlap fixture version=overlap-fixture-v2 temp_dir=/data/local/tmp`，最后应有：

```text
PASS: serial/overlapped output equality, damaged frames, truncated capture and worker failure
```

如果程序异常退出、超时或没有最后 PASS，停在这里，当前旧服务可以继续使用。

## 同一临时策略下比较两种模式

继续复用 Ubuntu `9004` 到 RK `9002` 的已有转发；HDC 掉线后先重新建立，再开始测量。测试前完成上述文件同步与离线验证。现有 `check_multivideo_cpu.sh` 应为 `cpu-governor-v3`。

在终端 A 执行限时 300 秒策略，保持该终端运行：

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
sudo "$HDC" -t "$TARGET" shell \
    sh /data/local/tmp/check_multivideo_cpu.sh hold 300
```

看到 `CPU_HOLD_READY seconds=300 ... governor=performance` 后，在终端 B 定义启动函数：

```bash
OVERLAP_TAG="overlap_v5_$(date +%Y%m%d_%H%M%S)"

start_verify_case()
{
    local case_name=$1
    shift
    sudo "$HDC" -t "$TARGET" shell \
        sh /data/local/tmp/stop_multivideo_server.sh || return 1
    sudo "$HDC" -t "$TARGET" shell \
        /data/local/tmp/pcie_multivideo_server.overlap_v5 \
        --device "/dev/pango_pcie_multivideo_$FPGA_BDF" \
        --source both --serve --port 9002 \
        --send-timeout-ms 30000 --profile "$@" \
        --daemon --log "/data/local/tmp/${OVERLAP_TAG}_${case_name}.log"
}
```

先用 v5 的默认串行模式测 60 帧：

```bash
start_verify_case serial && \
/usr/bin/time -f 'SERIAL_HOST_SECONDS=%e' \
    python3 preview_multivideo.py --host 127.0.0.1 --port 9004 \
    --headless --count 60 --timeout 60
```

成功后再启用重叠模式测同样的 60 帧：

```bash
start_verify_case overlap --overlap-verify && \
/usr/bin/time -f 'OVERLAP_HOST_SECONDS=%e' \
    python3 preview_multivideo.py --host 127.0.0.1 --port 9004 \
    --headless --count 60 --timeout 60

sudo "$HDC" -t "$TARGET" shell \
    sh /data/local/tmp/check_multivideo_cpu.sh status
sudo "$HDC" -t "$TARGET" shell \
    tail -n 40 "/data/local/tmp/${OVERLAP_TAG}_overlap.log"
```

两组必须都在等待期内完整通过，结束时策略仍为 `performance`，终端 A 到时没有策略被改写的错误并成功恢复，才作为本次有效对比。重叠服务启动日志应有 `overlap_verify=on`，默认组为 `off`。记录两组耗时、PASS 和 profile 抽样，避免只与之前 v4 单次结果比较。若 HDC 中断，本轮作废，先恢复连接与转发，再安排新一轮。

还有等待时间时，可用同一端口打开窗口观察，按 q 关闭。终端 A 自然到时出现 `CPU_HOLD_COMPLETE` 和 `CPU_GOVERNORS_RESTORED`，恢复原 governor；不要永久保持策略。

## 2026-10-24 板端对比记录

本轮附件中，v5 串行和重叠服务分别显示 `overlap_verify=off`、`overlap_verify=on`，两个连接均为 `completed=60 total_sent=60`。随后第 61 帧发送失败并继续等待客户端，与定帧数客户端主动结束的行为一致，未显示此前那种中途半帧断开的现象；服务端发送完成仍不能代替客户端完整接收及校验 PASS。

补充的 `cpu-governor-v3` 日志显示：原策略为 `schedutil`，限时 300 秒时切为 `performance`、频率 1992000 kHz，等待结束前仍为 `performance`，最后有 `CPU_HOLD_COMPLETE`、`CPU_RESTORE policy=policy0 governor=schedutil` 和 `CPU_GOVERNORS_RESTORED`。这份日志确认策略切换和恢复正常。

每组服务日志各有 9 个计时样本，序号为 1、2、3、10、20、30、40、50、60。以下均值包含全部 9 个样本，单位为毫秒；只代表服务端抽样，不能用来推算整轮客户端吞吐。

| 项目 | 串行 | 重叠 |
|---|---:|---:|
| HDMI header 均值 | 32.238 | 32.351 |
| HDMI 校验均值 | 10.350 | 10.339 |
| 摄像头 header 均值 | 59.844 | 68.230 |
| 等待 HDMI 校验线程均值 | 0 | 0.057 |
| 拼接均值 | 1.461 | 1.457 |
| 输出校验均值 | 3.063 | 3.066 |
| 发送均值 | 1.747 | 1.743 |
| 单次处理总耗时均值 | 112.365 | 110.851 |
| 单次处理总耗时中位数 | 113.367 | 113.330 |
| 第 60 帧总耗时 | 113.367 | 75.408 |

重叠组摄像头处理结束后等待工作线程仅 0.047～0.088 ms，说明 HDMI 校验已在前面的处理中完成。常见处理周期仍约 114 ms；第 60 帧的 75.408 ms 拉低了重叠组均值，尚不能证明稳定提速。附件和后续补充未包含 `SERIAL_HOST_SECONDS`、`OVERLAP_HOST_SECONDS` 和两次客户端 60 帧 PASS，因此整轮收益仍待确认。

核对代码：服务的 header 计时包含驱动同步执行的完整采集流程，并非仅传输 64 字节头。驱动在首次读取时完成 FPGA 请求、等待、DMA 缓存同步、复制和内核校验后才返回头部。FPGA 的 `camera_ddr_snapshot.v` 在收到请求后进入 `CAM_ARM`，等下一个 `frame_start`，采集完整帧，处理 `frame_end`，确认所有写入完成且帧有效后才置位 `frame_ready`；`pcie_frame_dma_bridge.v` 的 `D_WAIT_FRAME` 等待该信号后开始读取。因此提前开始摄像头请求可能主要增加等待同一帧开始的时间，抵消用户态重叠的校验耗时。当前尚需同轮内核 `capture_us`、`checksum_us` 等字段区分各部分，不能把全部 header 耗时解释为 PCIe 传输或纯帧同步等待。

本轮串行的两路驱动序号范围为 1248～1307，重叠为 1309～1368；失败的第 61 帧及后续窗口预览不计入。可在 Ubuntu 提取尚未被覆盖的同轮内核样本，无需重载驱动或重新烧录：

```bash
sudo "$HDC" -t "$TARGET" shell dmesg \
    > "$HOME/pcie_host/overlap_v5_kernel.log"

awk '
/source=[01] bytes=.*capture_us=/ {
    seq = 0
    for (i = 1; i <= NF; i++) {
        if ($i ~ /^seq=/) {
            split($i, parts, "=")
            seq = parts[2] + 0
        }
    }
    if (((seq >= 1248 && seq <= 1307) ||
         (seq >= 1309 && seq <= 1368)) && seq % 10 == 0)
        print
}' "$HOME/pcie_host/overlap_v5_kernel.log"
```

## 2026-10-24 最近内核样本与输入帧率检查

用户随后成功取回内核日志：`profile=Y`，文件为 1696 行、271560 字节，末尾样本为两路各 6 帧、驱动序号 1793～1798，均为 `dma_buffer=cached`。这些是后续运行的最新样本，不属于上文串行/重叠对比的相同序号范围，也没有对应的同时刻 governor 记录，不能直接拿来计算两种模式的提升。

| 内核阶段 | HDMI 均值（ms） | OV5640 均值（ms） |
|---|---:|---:|
| 请求到检测完成 `capture_us` | 17.949 | 64.296 |
| 复制 `copy_us` | 1.717 | 0.247 |
| 校验 `checksum_us` | 10.352 | 1.557 |
| 本帧记录准备总时间 `record_us` | 31.191 | 66.651 |

摄像头 `capture_us` 范围为 41.843～73.332 ms，平均占摄像头 `record_us` 的约 96.5%。它包含帧同步与完整帧采集、DDR 读取、PCIe 传输和软件轮询完成的时间，现有字段无法单独拆出每项。当前数据把下一步优化重点指向摄像头采集请求期间；用户态校验重叠的整轮收益仍不能从这些后续样本中得出。

只读诊断工具 `pcie_video_sources.c` 更新为 `video-source-rate-v2`：保留源描述符和摄像头状态检查，使用 `CLOCK_MONOTONIC` 测量首末两次采样的实际间隔，用输入验证模块的完整帧计数增量输出 `INPUT_RATE`。新增 `--version`；删除过时的“仅 HDMI 支持像素 DMA”阶段提示。计数下降时拒绝本轮测量，避免重置或回绕产生虚假的高帧率；单次采样不输出速率。睡眠中断继续等待，其他睡眠错误打印后退出。

该工具只以 `PROT_READ` 映射 BAR0，不发起像素 DMA、不改变输入源、不需要更新驱动或 FPGA。`good_fps` 是通过输入验证的 OV5640 帧率，`mean_good_period_ms` 是这类帧的平均间隔，不是 PCIe 接收或预览帧率，也不是精确的 VSYNC 波形周期。此测量使用真实间隔，因此不需要为它切换 CPU governor。

本地已用 Zig 以 `-target aarch64-linux-musl -static -O2 -std=c11 -Wall -Wextra -Werror` 交叉编译，并核对 ELF64/AArch64、无动态加载器及动态段、版本和输出字段；程序为 1175352 字节。当前环境不能执行 ARM Linux 程序，板端测量待用户运行。

在 Ubuntu 同步新工具到独立文件名：

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
export FPGA_BDF="0002:21:00.0"
cd "$HOME/pcie_host"

cp -v -- /mnt/hgfs/pcie_host/pcie_video_sources \
    "$HOME/pcie_host/pcie_video_sources.rate_v2" && \
cmp -- /mnt/hgfs/pcie_host/pcie_video_sources \
    "$HOME/pcie_host/pcie_video_sources.rate_v2" && \
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/pcie_video_sources.rate_v2" \
    /data/local/tmp/pcie_video_sources.rate_v2
```

确认传输完成，再检查身份并测量约 10 秒：

```bash
sudo "$HDC" -t "$TARGET" shell \
    chmod 755 /data/local/tmp/pcie_video_sources.rate_v2
sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_video_sources.rate_v2 --version

sudo "$HDC" -t "$TARGET" shell \
    /data/local/tmp/pcie_video_sources.rate_v2 "$FPGA_BDF" 11 1000 \
    2>&1 | tee "$HOME/pcie_host/camera_input_rate_$(date +%Y%m%d_%H%M%S).log"
```

版本应为 `video-source-rate-v2`，最后应有 `INPUT_RATE` 和完整帧检查 PASS。下一步根据输入速率、坏帧和错误标志决定优先检查摄像头时钟/配置，还是改造请求时等待新完整帧的缓存流程。持续采集并锁定完整帧的方案需要处理缓冲区读写归属，不能让 DMA 读取的帧被同时覆盖。

后续实机输入速率测量通过：`INPUT_RATE samples=11 elapsed_seconds=10.005 good_frames=263 bad_frames=0 good_fps=26.288 mean_good_period_ms=38.040`。每次采样均为完整 VGA、错误标志 0。下一步 FPGA 提前采集和完整帧冻结的实现与独立对比步骤见 [CAMERA_PREFETCH.md](../../06_hdmi_loop_1/CAMERA_PREFETCH.md)。这项输入速率不等于 PCIe 或预览输出帧率。

## 回退

当前 v5 不加 `--overlap-verify` 就是默认串行模式，终端 B 执行 `start_verify_case serial` 可回退本项行为。也可停止 v5 后用板上原 `/data/local/tmp/pcie_multivideo_server` 按原命令启动。CPU 仍由终端 A 的限时脚本到时恢复；若连接中断，重连后按 CPU 文档核对状态及残留备份。
