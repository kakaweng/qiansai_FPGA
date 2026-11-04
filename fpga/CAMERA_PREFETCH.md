# OV5640 采集优化第 5 步：提前采集，在完整帧边界冻结

## 实测依据

2026-10-24，RK3568 的只读输入诊断通过：10.005 秒内新增 263 个有效帧、0 个坏帧，`good_fps=26.288`，`mean_good_period_ms=38.040`；每帧为 640×480 RGB565，错误标志为 0。

此前最近 6 对驱动样本中，摄像头 `capture_us` 平均 64.296 ms、范围 41.843～73.332 ms，`record_us` 平均 66.651 ms，复制和校验平均分别为 0.247 / 1.557 ms。`capture_us` 包含采集、DDR、PCIe 和软件轮询，不能全部视为纯帧同步等待。结合收到请求后才等待下一帧开始的原实现，本步优先减少这一额外等待。

## 修改后的行为

| 阶段 | 原模式 | 本步预取模式 |
|---|---|---|
| 没有摄像头上传请求 | 不写摄像头帧缓存 | 持续将摄像头完整帧写入原 DDR 区域 |
| 请求在一帧中途到来 | 放弃当前帧，等待下一帧开始，再采完整帧 | 继续完成当前已开始采集的帧 |
| 可以上传的条件 | 完整、有效帧已写完 | 完整、有效帧已写完，且摄像头端已确认冻结，确认属于本次请求 |
| 上传期间 | 保持该帧 | 保持该帧，停止摄像头写入 |
| 上传释放后 | 等下一次请求 | 恢复背景采集 |
| 异常或 FIFO 溢出帧 | 拒绝并重试 | 拒绝并重试 |

本步仍使用单帧缓存；请求到来后可能需要等当前帧采完。它没有实现“从两个缓存中立即选择最新完整帧”，也不承诺达到 26 帧/秒的拼接输出。正常帧下，本步目标是减少等待下一帧开始的额外时间；DDR 竞争、HDMI 采集、校验及输出仍占用时间。

摄像头的 DDR 基址仍为控制器地址 `28'h0800000`，PCIe 源选择、帧大小、64 字节协议头、校验和 RK3568 接口沿用现有实现。本步会在 HDMI 工作时增加背景摄像头写入，因此同轮验证必须检查 HDMI 完整性和实际帧率，不能仅测摄像头。

## 代码与回退开关

| 文件 | 变化 |
|---|---|
| `src/camera/camera_ddr_snapshot.v` | 增加 `PREFETCH` 参数，模块默认值为 0，保留原按请求采集路径 |
| `src/hdmi_loop.v` | 顶层 `CAMERA_DDR_PREFETCH=1`，将参数传给摄像头缓存模块 |
| `sim/camera_snapshot_tb.v` | 增加背景采集、帧中请求、冻结保护、取消、异常恢复和复位测试 |
| `sim/run_multivideo.ps1` | 同时运行原模式与预取模式的小尺寸、VGA 和异步时钟测试 |

数据令牌在请求到来前也能继续写入，不会因新请求清空已经写入的半帧。只有摄像头确认停止继续写入后发出的结束令牌，且请求代次与当前请求一致，才允许发布 `frame_ready`；因此不能单靠“之前曾写完一帧”提前放行 DMA。上传结束后释放冻结；取消后遗留的冻结令牌不应被下一次请求复用。

回退可烧录工程根目录保存的 `hdmi_loop.before_camera_prefetch_20261024.sbit`；也可将顶层 `CAMERA_DDR_PREFETCH` 默认值改为 0 后重新生成位流。保存的原位流为 2101696 字节，SHA-256 为 `f6c6432b3d38073b3d97a8cb422dd81f2333eb835d4b7132fd171dd1b5739ea2`。

## 本地验证

Icarus 回归共 15 个配置通过：原模式小尺寸和 VGA；预取模式两种异步时钟的小尺寸压力测试和 VGA；BAR0 描述符；HDMI 帧边界暂停/恢复、1920 像素行宽和共享 DDR 竞争，包括 148.5 MHz HDMI / 100 MHz DDR；DDR 读命令尾拍；BAR1 启动；灰度 HDMI、RGB565 HDMI 和摄像头 DMA 上传。

预取压力测试逐像素检查整帧内容，验证请求在一帧中途到来时能发布同一帧、冻结期间经过后续帧仍不覆盖、释放后恢复背景采集、坏帧与 FIFO 溢出恢复、采集中取消并重新请求、DDR 尾部受阻时取消已排队的冻结令牌、不同请求相位和复位。完整 VGA 配置检查背景采集、帧中请求、冻结和恢复；异常压力使用小尺寸配置。

复现仿真：

```powershell
.\sim\run_multivideo.ps1 -IverilogDirectory 'C:\Users\PC\AppData\Local\Temp\pango_iverilog_portable\mingw64\bin'
```

PDS 2022.2-SP6.4 已于 2026-10-24 22:47 完成 RTL 编译、综合、布局布线、时序分析和位流生成，进程返回 0。位流已复制为工程根目录中的独立文件，避免下一轮构建替换默认输出后混淆版本：

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| `hdmi_loop.camera_prefetch_v1.sbit` | 2101696 字节 | `a731b28dcf766b71e699e0dd6239051057c09ddc10e891f82801287bb105829f` |
| `hdmi_loop.before_camera_prefetch_20261024.sbit` | 2101696 字节 | `f6c6432b3d38073b3d97a8cb422dd81f2333eb835d4b7132fd171dd1b5739ea2` |

最终时序依据 `report_timing/hdmi_loop.rtr`（22:45:22），旧版对照为本轮保留的 `report_timing/bak/hdmi_loop.rtr`（18:51:57），不是布局布线中途的 slack，也不是 `place_route` 下的中间摘要。

| 慢速角落、同一时钟域 | 旧版 setup WNS | 预取版 setup WNS | 旧版 hold WHS | 预取版 hold WHS |
|---|---:|---:|---:|---:|
| HDMI `pixclk_in` | +0.001 ns | +0.025 ns | -0.084 ns | -0.072 ns |
| DDR `ddrphy_clkin` | +0.277 ns | +0.592 ns | -0.279 ns | -0.155 ns |
| 摄像头 `cam_pclk` | +2.509 ns | +2.493 ns | +0.252 ns | +0.226 ns |

新旧报告的全工程状态均为 `Some Constraints Violated`。预取版仍有其他时钟域 setup、跨时钟路径、hold 和复位 recovery/removal 违例；数值随布局布线变化，不能因旧版也有违例就将其忽略。HDMI 外部输入延迟约束也尚不完整。本位流作为可回退的性能对比版本，不能宣称全工程时序收敛。本地仿真和位流生成不替代板端 DDR、PCIe、图像位置和性能验证。

## 板端对比：先保存原位流基线

这一轮使用同一个 v5 服务、相同 `--overlap-verify` 设置和驱动 `1.2 cached_dma=1`。先用 RK 板内回环校验对比 FPGA 采集收益，随后再观察 Ubuntu 预览；板内 fps 不等于 HDC 预览 fps。

当前板上仍运行原位流时，在一个 Ubuntu 终端设置环境并定义测量函数。关闭 Python 预览，避免另一个客户端占用服务。

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
export FPGA_BDF="0002:21:00.0"
cd "$HOME/pcie_host"

measure_prefetch_case()
{
    local case_name=$1
    local run_tag="camera_prefetch_${case_name}_$(date +%Y%m%d_%H%M%S)"
    local board_log="/data/local/tmp/${run_tag}.log"
    local compare_exit

    sudo "$HDC" -t "$TARGET" shell \
        sh /data/local/tmp/stop_multivideo_server.sh || return 1
    sudo "$HDC" -t "$TARGET" shell \
        /data/local/tmp/pcie_multivideo_server.overlap_v5 \
        --device "/dev/pango_pcie_multivideo_$FPGA_BDF" \
        --source both --serve --port 9002 \
        --send-timeout-ms 30000 --profile --overlap-verify \
        --daemon --log "$board_log" || return 1

    sudo "$HDC" -t "$TARGET" shell \
        sh /data/local/tmp/check_multivideo_cpu.sh compare 60 9002 \
        2>&1 | tee "$HOME/pcie_host/${run_tag}.cpu.log"
    compare_exit=${PIPESTATUS[0]}
    printf 'CPU_COMPARE_EXIT=%s\n' "$compare_exit"

    sudo "$HDC" -t "$TARGET" shell cat "$board_log" \
        2>&1 | tee "$HOME/pcie_host/${run_tag}.server.log"
    sudo "$HDC" -t "$TARGET" shell dmesg \
        > "$HOME/pcie_host/${run_tag}.kernel.log"
    printf 'RESULT_PREFIX=%s/%s\n' "$HOME/pcie_host" "$run_tag"
    return "$compare_exit"
}

measure_prefetch_case before
```

本函数会停止并重新启动视频服务。CPU 脚本先以原 governor 检查 60 帧，再以临时 `performance` 检查 60 帧，完成后立即恢复原策略。确认两次 60 帧 PASS、两条 `CPU_CASE`、`CPU_COMPARE_COMPLETE`、`CPU_GOVERNORS_RESTORED`，并保存输出。HDC 退出码不代替这些成功标识；若断线，重连后先核对调频状态，不能把不完整记录作为对比结果。

## 烧录与重启

原位流基线完整结束后，先停止视频服务并卸载驱动，确保没有采集正在执行：

```bash
sudo "$HDC" -t "$TARGET" shell \
    sh /data/local/tmp/stop_multivideo_server.sh
sudo "$HDC" -t "$TARGET" shell rmmod pango_pcie_multivideo
```

在 Windows PDS Programmer 中烧录本步独立保存的 `hdmi_loop.camera_prefetch_v1.sbit`，完整路径为 `F:\B_file\jichuangsai\Pango_FPGA\06_hdmi_loop_1\hdmi_loop.camera_prefetch_v1.sbit`。烧录完成后重启 RK3568，让 PCIe 重新枚举。仅烧录 FPGA 而不重启 RK3568，可能保留旧 PCIe 状态，之前已出现协议错误，因此这轮也需要重启。

若操作会使 FPGA 掉电，需要确保重启后运行的仍是预取版：仅下载到 FPGA SRAM 的位流在掉电后会丢失。优先保持 FPGA 供电，仅重启 RK3568。

## 重启后加载原驱动并测新位流

HDC 恢复连接后，在上述 Ubuntu 终端执行：

```bash
sudo "$HDC" list targets
sudo "$HDC" -t "$TARGET" shell id
sudo "$HDC" -t "$TARGET" shell ls /sys/bus/pci/devices
sudo "$HDC" -t "$TARGET" shell \
    ls -l "/sys/bus/pci/devices/$FPGA_BDF/resource0"

sudo "$HDC" -t "$TARGET" shell \
    insmod /data/local/tmp/pango_pcie_multivideo.ko profile=1 cached_dma=1
sudo "$HDC" -t "$TARGET" shell \
    cat /sys/module/pango_pcie_multivideo/version
sudo "$HDC" -t "$TARGET" shell \
    cat /sys/module/pango_pcie_multivideo/parameters/cached_dma
sudo "$HDC" -t "$TARGET" shell \
    ls -l "/dev/pango_pcie_multivideo_$FPGA_BDF"

measure_prefetch_case after
```

版本应为 `1.2`，`cached_dma` 应为 `Y`；实际路径继续以内核 `dma_buffer=cached` 为准。这里只使用原先已同步并验证过的驱动和 v5 服务，不需要重新编译 RK3568 代码。若 Ubuntu 终端也已关闭，需要重新执行前面的环境和函数定义。

对比两轮 `CPU_CASE name=performance` 的耗时、fps 和对应内核 `source=1 capture_us`，并确认 HDMI 校验与画面位置正常。两轮 governor 设置和服务参数必须一致，不能把旧位流的 `schedutil` 与新位流的 `performance` 作对比。板内通过后再恢复 HDC 转发与窗口预览，观察两路动态内容是否更新且无撕裂。

## 2026-10-24 新位流首次板端反馈

用户下载新位流、重启 RK3568 后提供日志，前缀为 `camera_prefetch_after_20261024_082232`。HDC 转发为 Ubuntu `9004` 到 RK `9002`；Ubuntu 连续收到双源拼接序号 123～152，均为 1280×480、1228800 字节，30 帧全部校验通过，`AFTER_HOST_SECONDS=2.33`。按整轮时间折算约 12.88 帧/秒、77.67 ms/帧；这包含程序启动、连接和校验，不能直接当作窗口显示帧率或端到端延迟。

同次附件中的内核序号 113～122，两路各 10 帧，全部为 `dma_buffer=cached`：

| 内核阶段 | HDMI 均值（ms） | OV5640 均值（ms） |
|---|---:|---:|
| `capture_us` | 16.309 | 34.325 |
| `copy_us` | 1.485 | 0.232 |
| `checksum_us` | 10.369 | 1.562 |
| `record_us` | 29.367 | 36.656 |

摄像头 `capture_us` 范围为 27.222～41.247 ms，较此前 6 帧样本均值 64.296 ms 明显缩短；HDMI 校验均值仍约 10.37 ms。该变化与提前采集减少额外等帧的目标一致，但旧样本没有同时刻 governor，当前也未收到完整的原位流/新位流 `CPU_CASE` 对照，因此不能将差值作为严格控制变量下的 FPGA 提速百分比。

用户同时提供 `hold 300` 的 `CPU_HOLD_READY`，CPU 策略为 `performance`、频率 1992000 kHz，开始时 SoC/GPU 温度为 40000/40625 毫摄氏度，并反馈预览已经很流畅。此次反馈尚未包含保持结束后的 `CPU_GOVERNORS_RESTORED`，也未附 30 帧测试同时刻的 governor 查询；起始温度不能代表持续运行后的温升。

客户端 `capture_gap_ms` 范围为 20.2～56.6 ms、均值约 46.39 ms，表示两路采集完成时间戳之差，不能用倒数计算输出帧率或解释为端到端延迟。当前证据支持短轮接收校验通过及主观预览改善。300 帧连续校验和完整 CPU 对照可按需补充，不作为当前流畅度验收的前置条件。

## 2026-10-24 流畅度验收与后续开发基线

用户明确确认“这一版的流畅度我已经满意了”。本轮原图传输流畅度优化到此结束，当前版本作为后续开发和 YOLO 接入时的传输基线。

| 项目 | 基线配置 |
|---|---|
| FPGA 位流 | 工程根目录 `hdmi_loop.camera_prefetch_v1.sbit`，文件 hash 见上文 |
| 摄像头缓存 | `CAMERA_DDR_PREFETCH=1`，提前采集、完整帧冻结 |
| RK 驱动 | `pango_pcie_multivideo.ko`，版本 `1.2`，`profile=1 cached_dma=1` |
| RK 服务 | `pcie_multivideo_server.overlap_v5`，版本 `multivideo-overlap-v5` |
| 服务模式 | `--source both --serve --port 9002 --send-timeout-ms 30000 --profile --overlap-verify` |
| CPU 条件 | 预览反馈时 `hold 300` 已就绪，临时使用 `performance`；到期恢复，未配置永久策略 |
| Ubuntu 预览 | HDC `tcp:9004` 转发到 RK `tcp:9002`，客户端 `127.0.0.1:9004` |

后续接入推理时保留这套位流、驱动和服务配置作为对照，分别记录传输、推理和显示的耗时。此次验收范围为用户认可的预览流畅度；上文实测和时序报告的记录范围保持不变。
