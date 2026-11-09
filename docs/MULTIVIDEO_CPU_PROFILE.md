# 双源视频性能优化：第 3 步，CPU 调频策略对比

## 1. 当前结论与本步目的

缓存型 DMA 驱动 `1.2` 已由实机日志确认进入 `dma_buffer=cached` 路径。第 4～30 帧的两路复制及缓存同步合计，从原来的平均 56.140 ms 降到 6.707 ms，减少约 88.1%。这只是该阶段的收益。

随后同一个预览连接发送 375 帧，用户确认最后主动关闭窗口。该次日志没有中途重连，末尾下一帧的 `Broken pipe` 对应正常客户端退出。序号增长和校验通过不能独立证明画面内容位置或新鲜度，仍以动态画面检查为准。

服务日志去掉连接内最初三条后，37 个稳态计时抽样如下：

| 服务阶段 | 平均耗时 |
|---|---:|
| HDMI 读头，含内核采集、复制、源校验 | 53.458 ms |
| HDMI 读像素 | 2.412 ms |
| HDMI 用户态源 FNV | 19.600 ms |
| OV5640 读头，含内核采集、复制、源校验 | 68.696 ms |
| OV5640 读像素 | 0.760 ms |
| OV5640 用户态源 FNV | 7.735 ms |
| 缩放拼接 | 3.360 ms |
| 拼接输出 FNV | 12.276 ms |
| 提交 socket | 4.399 ms |
| 整轮 `total_us` | 172.712 ms |

用户态三次 FNV 合计 39.611 ms，内核源校验还包含在读头阶段内。此前内核抽样中 HDMI 源校验有较大波动，可能受到 CPU 调频、调度、缓存或系统负载影响，目前没有实机 CPU 频率数据确认原因。本步先用同一代码对比调频策略，避免直接改动校验算法。

2026-10-24 使用 `cpu-governor-v2` 完成一次板内对比，两组各 60 帧全部校验通过：

| 策略 | 总耗时 | 测试帧率 | 测试前、后采样频率 |
|---|---:|---:|---|
| 原 `schedutil` | 10.71 s | 5.60 fps | 1800 / 1104 MHz |
| 临时 `performance` | 6.87 s | 8.73 fps | 1992 / 1992 MHz |

同帧数耗时减少约 35.9%，吞吐提高约 55.9%。两组前后的温度采样约为 39.4～40℃，不能据此推断运行中的峰值或长期温度。日志包含 `CPU_COMPARE_COMPLETE` 和 `CPU_GOVERNORS_RESTORED`，已恢复原 `schedutil`。这一次结果确认调频策略会影响当前板内处理速度，尚需按第 5 节验证 Ubuntu 实际接收和窗口预览。

随后按第 5 节完成 Ubuntu 各 60 帧接收校验，原策略耗时 10.37 秒、约 5.79 fps；临时 `performance` 耗时 6.94 秒、约 8.65 fps。同帧数耗时减少约 33.1%，吞吐提高约 49.4%，用户确认窗口观感改善，并确认第二组结束时仍为 `performance`、限时结束后出现 `CPU_GOVERNORS_RESTORED`。这是本次完整 Python 启动、连接及接收校验流程的均值，不是独立测得的显示帧率或端到端延迟。

`total_us` 是抽样的服务处理时间；它的倒数不是 Ubuntu 实收或显示帧率。之前旧路径第 10/20/30 帧的三个抽样平均 212.831 ms，与本轮抽样的数量和运行时段不同，仅作参考。

## 2. 脚本行为

新增 [check_multivideo_cpu.sh](check_multivideo_cpu.sh)，在 RK3568 上执行：

当前脚本标识为 `CPU_PROFILE_VERSION=cpu-governor-v3`。沿用 v2 的写入方法：写入 governor 前先回读，若已经等于目标值则不再写入；否则先生成普通临时文件，通过 `dd bs=4096 count=1` 将整个短字符串作为一块写入 sysfs，再回读确认。这样避免依赖板端 shell 的 `printf` 是否把一个字符串分成多次写入。若写工具报错但回读确认目标已生效，则记录 `CPU_WRITE_WARNING`；若不一致，则记录 `CPU_WRITE_FAILED` 并停止。保存原策略的文件一直保留到回读确认恢复成功。

| 命令 | 行为 |
|---|---|
| `status` | 只读当前调频策略、可用策略、当前频率、频率上下限和温度 |
| `compare 60 9002` | 保存各 policy 的原调频策略，先按原策略校验 60 帧，再临时使用 `performance` 校验 60 帧，结束后恢复原策略 |
| `hold 180` | 保存原策略并切到 `performance`，输出 `CPU_HOLD_READY` 后等待 180 秒，到时恢复；期间不启动视频接收客户端，供另一个终端运行 Ubuntu 测试 |
| `restore` | 上次进程已结束但保留了备份时，按备份恢复；上次进程仍运行则拒绝并发恢复 |

两组都使用现有 v4 服务的 `--check-stream`，校验每帧协议头、像素 FNV 和递增序号。视频发送与接收都在 RK 本机，HDC 负责命令及日志；不需要本地 `fport`。同一时刻保持一个采集客户端，运行脚本前关闭 Ubuntu 的窗口和无窗口预览，保留板端双源服务。

`performance` 请求系统在现有约束下采用较高 CPU 性能，实际频率仍受频率上限、温度和系统策略约束。脚本只切换 CPU governor，不改电压、频率上下限、NPU 或 FPGA。测试期间功耗和温度可能上升，脚本会打印频率及温度用于判断。测试后恢复的是原 governor，动态频率会继续按系统策略变化。

正常退出、采集失败以及收到 HUP/INT/TERM/PIPE 时都会尝试恢复；恢复期间忽略 PIPE，避免输出通道中断阻止后续 policy 的恢复。恢复失败会保留 `/data/local/tmp/multivideo_cpu_profile.saved` 备份供手动恢复。强制杀进程或 HDC 丢失不能保证立即执行清理；重连后可用 `status` 与 `restore` 核对。已有备份时不覆盖，所有 policy 原本都为 `performance` 时报告跳过重复比较。

板内校验客户端本身也在 RK 上计算输出 FNV，因此这是额外带有板内接收校验负载的 A/B 对比。两组之间可以比较，但不能把它的 fps 直接当作 Ubuntu 预览 fps，也不能直接与前述 172.712 ms 对比。若性能有明显提升，下一步再按相同 Ubuntu 客户端测法验证。

## 3. Ubuntu 同步、发送

已有驱动应为 `1.2`、实际缓冲为 `cached`，服务为 `multivideo-profile-v4`、双源模式、板端端口 `9002`。本步无需编译、重载驱动或重新生成 FPGA。

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
cd "$HOME/pcie_host"

cp -v -- /mnt/hgfs/pcie_host/check_multivideo_cpu.sh \
    "$HOME/pcie_host/check_multivideo_cpu.sh" && \
cmp -- /mnt/hgfs/pcie_host/check_multivideo_cpu.sh \
    "$HOME/pcie_host/check_multivideo_cpu.sh" && \
sudo "$HDC" -t "$TARGET" file send \
    "$HOME/pcie_host/check_multivideo_cpu.sh" \
    /data/local/tmp/check_multivideo_cpu.sh

sudo "$HDC" -t "$TARGET" shell \
    sh /data/local/tmp/check_multivideo_cpu.sh status
```

确认传输完成后运行对比。期间保持 HDMI 信号、摄像头和当前服务不变。

如果上一版留下了保存目录，先用新脚本执行 `restore`，确认 `CPU_GOVERNORS_RESTORED` 后再比较。当前值已恢复时，会跳过 sysfs 写入并清理已确认的备份：

```bash
sudo "$HDC" -t "$TARGET" shell \
    sh /data/local/tmp/check_multivideo_cpu.sh restore
```

```bash
CPU_TAG="multivideo_cpu_$(date +%Y%m%d_%H%M%S)"

sudo "$HDC" -t "$TARGET" shell \
    sh /data/local/tmp/check_multivideo_cpu.sh compare 60 9002 \
    2>&1 | tee "$HOME/pcie_host/${CPU_TAG}.log"
```

成功时应有两次 60 帧 `PASS`、两条 `CPU_CASE`，随后 `CPU_COMPARE_COMPLETE`、各 policy 的 `CPU_RESTORE` 和 `CPU_GOVERNORS_RESTORED`。计时使用 `/proc/uptime` 的百分之一秒刻度，覆盖校验客户端启动到返回；打印的 fps 为该组完整通过时的帧数除以耗时。

```text
CPU_CASE name=original frames=60 elapsed_seconds=... fps=...
CPU_CASE name=performance frames=60 elapsed_seconds=... fps=...
CPU_COMPARE_COMPLETE
CPU_RESTORE policy=policy0 governor=...
CPU_GOVERNORS_RESTORED
```

`CPU_COMPARE_SKIPPED reason=already_performance` 表示原本已经采用该策略；`performance governor unavailable` 表示系统未提供该策略；`CPU_COMPARE_INVALID reason=governor_changed` 表示第二组结束时策略已被其他系统服务改变。这些情况都不能作为有效的调频收益对比，保存完整输出即可。

如果缺少恢复完成标识，重连后先检查：

```bash
sudo "$HDC" -t "$TARGET" shell \
    sh /data/local/tmp/check_multivideo_cpu.sh status
sudo "$HDC" -t "$TARGET" shell \
    sh /data/local/tmp/check_multivideo_cpu.sh restore
```

若仍提示比较进程在运行，等待其结束后再核对。若恢复报错，保留备份与报错，不删除状态目录。

## 4. 验证边界

本地对脚本进行 POSIX shell 语法检查，并用临时目录模拟 cpufreq 和校验客户端，验证多 policy 正常恢复、两组任一采集失败后的恢复、终止信号后的恢复、策略不支持时停止、已为 performance 时跳过、参数校验，以及恢复失败保留备份和后续手动恢复。模拟测试不覆盖板端 sysfs 权限、系统策略干预、实际 CPU 频率、温度和视频性能；上述结果必须由 RK 实机输出确认。

v2 另覆盖写工具报错但目标已生效、写入被拒后停止第二组，以及旧版本已经实际恢复但残留备份时无须再次写 sysfs 的三种情况。此前普通文件模拟没有复现 OpenHarmony `printf` 对 sysfs 的实际写入行为；v2 的整块写入和回读已通过上述一次实机切换和恢复，具体的旧版报错机制仍未直接追踪。

v3 增加限时 `hold` 模式的模拟检查：不占用视频客户端、等待结束恢复、多 policy 切换、收到终止信号恢复、写入被拒时不报告就绪，以及时长参数检查。模拟等待使用替身，不代表板端 180 秒实测。

## 5. Ubuntu 接收与窗口预览对比

本步仅同步第 3 节的脚本，执行 `status` 确认 `cpu-governor-v3`。驱动、双源服务、协议、画面大小与校验保持一致，继续使用板端 `9002`，Ubuntu 使用 `9004`。同一时刻只运行一个视频接收客户端。

先在 Ubuntu 终端 B 检查转发：

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
cd "$HOME/pcie_host"
sudo "$HDC" -t "$TARGET" fport ls
```

若已有 `tcp:9004` 到 `tcp:9002` 的转发，直接复用；若没有，执行下列命令，成功后再继续。端口被其他监听占用时先核实，不要直接终止未知进程。

```bash
sudo "$HDC" -t "$TARGET" fport tcp:9004 tcp:9002
```

此时原策略应已恢复为 `schedutil`。先测相同的 60 帧：

```bash
sudo "$HDC" -t "$TARGET" shell \
    sh /data/local/tmp/check_multivideo_cpu.sh status

/usr/bin/time -f 'ORIGINAL_HOST_SECONDS=%e' \
    python3 preview_multivideo.py \
    --host 127.0.0.1 --port 9004 \
    --headless --count 60 --timeout 60
```

再在 Ubuntu 终端 A 启动限时策略。保持该终端运行：

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"

sudo "$HDC" -t "$TARGET" shell \
    sh /data/local/tmp/check_multivideo_cpu.sh hold 180
```

看到 `CPU_HOLD_READY seconds=180 ... governor=performance` 后，立即回终端 B 执行：

```bash
/usr/bin/time -f 'PERFORMANCE_HOST_SECONDS=%e' \
    python3 preview_multivideo.py \
    --host 127.0.0.1 --port 9004 \
    --headless --count 60 --timeout 60

sudo "$HDC" -t "$TARGET" shell \
    sh /data/local/tmp/check_multivideo_cpu.sh status
```

该次 60 帧必须在终端 A 的等待期内完整通过，测试后状态应仍为 `performance`，且终端 A 没有报告策略被修改。若到时恢复后才完成，则这次结果混合两种策略，不作为有效比较。两组都通过时，用 `60 / HOST_SECONDS` 估算该次完整启动、接收和校验流程的平均帧率，包含 Python 启动和连接开销，不是纯显示帧率。

还在等待期内时，在终端 B 打开窗口观察两路动态画面，按 `q` 结束：

```bash
python3 preview_multivideo.py \
    --host 127.0.0.1 --port 9004 --timeout 60
```

终端 A 到时应输出 `CPU_HOLD_COMPLETE`、各 policy 的 `CPU_RESTORE` 和 `CPU_GOVERNORS_RESTORED`。保持终端 A，等待自然结束；HDC 中断后重连用 `status` 核实，若原脚本尚在运行则等待其到时，进程结束后有残留备份才执行 `restore`。`hold` 仅在开始和结束核对 governor，不能排除期间系统短时干预，实际频率仍受温度及系统约束。

记录两组 `PASS`、`ORIGINAL_HOST_SECONDS`、`PERFORMANCE_HOST_SECONDS`、窗口观感及最终恢复标识。此步骤不重复板内 60+60 对比，也不永久设置 `performance`。
