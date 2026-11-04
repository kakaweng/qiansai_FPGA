# OV5640 接入：阶段 1、2

最终目标是 HDMI、OV5640、以太网三路视频进入 FPGA，再通过 PCIe 到 RK3568，最后拼接显示。本阶段完成统一源描述和 OV5640 的 DVP 像素采集、完整帧检查。现有 HDMI 的 DDR、DMA 控制寄存器、1920×1080 像素传输及主机流协议继续使用原来的接口。

阶段 1/2 已在实际 OV5640 上通过：`init=1 seen=1 valid=1 errors=0x00`，每帧 `307200` 像素、`480` 行、每行 `1280` 字节，连续采样 `bad=0`，有效帧计数约每秒增加 26～27。下一阶段的摄像头 DDR、双源 PCIe DMA 与 RK3568 拼接代码已加入；新的配套文件、重新烧录要求和完整终端命令见 [双源上板说明](../09_pcie_test/pcie_host/MULTIVIDEO.md)。下文表格描述的是阶段 1/2 的历史边界。

## 本阶段边界

| 项目 | 当前实现 |
|---|---|
| HDMI 图像 | 原有 1920×1080 PCIe DMA |
| 摄像头配置 | 沿用 `reg_config.v` 的 OV5640、640×480、RGB565 配置 |
| 摄像头输入 | 新增两个 8 位字节合成 RGB565、帧/行边界输出 |
| 摄像头完整性 | 检查每行 1280 字节、480 行、307200 像素；统计有效帧和异常帧 |
| RK3568 摄像头验收 | 通过 PCIe BAR0 读取描述和采集统计，无需摄像头 UDP 发送命令 |
| 以太网输入 | 只预留源编号，描述中的尺寸、格式、字节数为零，表示尚未实现 |
| 摄像头图像传输 | 待下一阶段接入摄像头帧缓存和 PCIe 调度 |
| 多路拼接显示 | 待多路缓存、传输和 RK3568 显示适配完成 |

摄像头在 FPGA 内采集，不以以太网作为本方案的传输路径。工程原有 UDP 功能仍存在，但本阶段验收不使用它。

## 文件

| 文件 | 用途 |
|---|---|
| `src/camera/ov5640_capture.v` | DVP 字节配对、帧边界、完整性检查、统计 |
| `src/video_source_status.v` | 统一帧描述、摄像头状态跨时钟域握手快照 |
| `src/hdmi_loop.v` | 接入两个新模块；摄像头采集独立于 HDMI 像素时钟 |
| `src/pcie/pango_pcie_top.v` | 转交新的源信息总线 |
| `ipcore/pcie_test/example_design/rtl/ipsl_pcie_dma_ctrl/ipsl_pcie_dma.v` | 增加 BAR0 信息读出窗口 |
| `hdmi_loop.pds` | 添加新 RTL 源文件 |
| `sim/ov5640_capture_tb.v` | VGA 彩色测试向量、异常帧、恢复和跨时钟域复位仿真 |
| `../09_pcie_test/pcie_host/pcie_video_sources.c` | RK3568 只读状态验收工具 |
| `../09_pcie_test/pcie_host/build_video_sources.sh` | ARM64 用户程序构建 |
| `../09_pcie_test/pcie_host/pcie_video_sources` | 已交叉编译的 ARM64 Linux 静态可执行程序，可发送到 RK3568 |

## 统一帧描述

每个源使用四个小端 32 位寄存器，描述的是输入/输出约定，摄像头的描述出现不代表其像素已经可供 DMA 读取。

| 相对偏移 | 字段 |
|---:|---|
| `+0x00` | `source_id`：0=HDMI，1=OV5640，2=以太网 |
| `+0x04` | `[15:0]=width`、`[31:16]=height` |
| `+0x08` | `pixel_format`：0=未实现，1=GRAY8，2=RGB565LE |
| `+0x0C` | `frame_bytes`：主机最终收到的像素字节数 |

| BAR0 基址 | 源 | 当前描述 |
|---:|---|---|
| `0x100` | HDMI | 1920×1080；由 `PCIE_RGB565` 决定 RGB565LE 或 GRAY8 |
| `0x110` | OV5640 | 640×480 RGB565LE，614400 字节 |
| `0x120` | 以太网 | 预留；尺寸、格式、长度为 0 |

原 BAR0 `0x00`～`0x0C` 状态和 BAR1 `0x130`、`0x140`、`0x150` HDMI DMA 命令保持原有定义。新增窗口位于 BAR0，注意不要与 BAR1 同偏移的 DMA 命令混淆。PCIe IP 内部 RAM 地址以 16 字节为单位，因此新窗口解码为 `0x10`～`0x14`。

## 摄像头诊断寄存器

| BAR0 地址 | 字段 |
|---:|---|
| `0x130` | 状态：高 16 位 `0xC101`；`[15:8]` 异常标志；bit0 初始化完成、bit1 已接收帧、bit2 最近帧有效、bit3 正在采集 |
| `0x134` | 有效完整帧计数 |
| `0x138` | 异常帧计数 |
| `0x13C` | 最近结束帧的 RGB565 像素配对数量 |
| `0x140` | 低 16 位为最近帧行数；高 16 位为最后一行有效字节数 |
| `0x144` | 最近帧所有已配对 RGB565 像素的 32 位累加和，按模 2^32 计算 |
| `0x148` | 低 16 位为首像素；高 16 位为末像素 |
| `0x14C` | 已结束帧序号，即有效帧计数与异常帧计数之和 |

| 异常标志 | 含义 |
|---:|---|
| `0x01` | 某行存在未配对的最后一个字节 |
| `0x02` | 某行字节数不等于 1280，或行字节计数溢出 |
| `0x04` | 帧行数/总像素数不符，或出现多余行 |
| `0x08` | VSYNC 边界上 HREF 仍有效，帧/行同步冲突 |

错误标志可以组合。例如少一个字节可能同时出现 `0x07`。完整帧只在 VSYNC 进入帧间消隐时发布，异常帧不会产生 `frame_valid`。

统计先在 `cam_pclk` 域采样为一个稳定快照，再通过请求/应答握手送到 PCIe 时钟域。主机读取 `0x14C` 前后进行序号比较，以避免跨两次快照读取。摄像头无 PCLK 时不会产生新快照；寄存器为零或统计停止不等于摄像头正常。

`init=1` 表示现有寄存器配置流程完成，并不单独证明 SCCB 地址/传感器身份正确。完整帧计数持续增加才是本阶段的主要输入验收依据。

## 像素接口约定

采集模块在 `cam_pclk` 上升沿读取 DVP。默认 VSYNC 高电平为帧间消隐，下降沿开始新帧，上升沿结束帧；HREF 为高电平行有效。初始化完成后等待新的 VSYNC 边界，丢弃当时正在进行的半帧。

| 信号 | 含义 |
|---|---|
| `cam_pixel_data[15:0]` | RGB565 像素数值，不是两个字节组成的网络包 |
| `cam_pixel_valid` | 每两个有效 DVP 字节产生一个单拍脉冲 |
| `cam_line_start/end` | 行起止事件，单拍脉冲 |
| `cam_frame_start/end` | 帧起止事件，单拍脉冲 |
| `cam_frame_valid/error` | 帧结束时的完整性结果，单拍脉冲 |

顶层参数 `CAM_VSYNC_ACTIVE_HIGH=1` 和 `CAM_HIGH_BYTE_FIRST=1` 分别选择 VSYNC 极性及字节顺序。默认先收到 RGB565 高字节。后续存储时，像素值 `16'hF800` 对应 RGB565LE 字节 `00 F8`。

输入完整帧检查无法证明 RGB 颜色顺序正确。颜色验收应使用 OV5640 色条或实际图像；现有 `reg_config.v` 的 `TEST_PATTERN_EN` 为 0，保持正常摄像头输出。需要传感器色条时可将该值改为 1，并重新生成位流。仿真中的色条由测试平台生成，与真实传感器色条分开验收。

**下一阶段不能把 `cam_pixel_valid` 直接接到现有 `wr_buf.wr_en`。**目前 `wr_buf` 会在使能为零时清零行内计数，并依据使能上升沿计数行。必须先加连续行输出缓存，或将写帧接口改为显式 `pixel_valid/line_start/line_end`。

## 软件验证

本次已通过 Icarus Verilog 12 的摄像头测试和 BAR0 解码测试，顶层中新接入的模块与接口通过编译，未加载完整厂商仿真库。主机验收工具已交叉编译为 ARM64 Linux 静态 ELF。真实摄像头 DVP 时序、PDS 综合/布局布线和实际 HDMI 回归仍需上板确认。当前电脑上的 ModelSim 10.6d 可以做语法编译，但启动仿真内核报 `CreateProcess error: 1392`，行为仿真因此使用 Icarus 完成。

从 `sim` 目录运行 ModelSim：

```powershell
vsim -c -do run_ov5640.do
```

也可以运行 Icarus Verilog：

```powershell
iverilog -g2012 -s ov5640_capture_tb -o ov5640_capture_tb.vvp ../src/camera/ov5640_capture.v ../src/video_source_status.v ov5640_capture_tb.v
vvp ov5640_capture_tb.vvp
```

成功时输出 `PASS: full VGA RGB565...`。覆盖完整 VGA、末行结束与 VSYNC 同拍、奇数字节、短行、长行、少行、多行、异常后的下一正常帧、反向字节序、反向 VSYNC、描述字段及两侧独立复位。

新增 BAR0 窗口可单独做解码回归：

```powershell
iverilog -g2012 -i -s pcie_video_info_tb -o pcie_video_info_tb.vvp ../ipcore/pcie_test/example_design/rtl/ipsl_pcie_dma_ctrl/ipsl_pcie_dma.v pcie_video_info_tb.v
vvp pcie_video_info_tb.vvp
```

该测试使用 `-i` 跳过未加载的 PCIe 包处理子模块，并强制内部 RAM 读接口，只验证寄存器窗口及原 HDMI 状态/读数据选择，不模拟物理 PCIe 链路。

## FPGA 与 RK3568 上板验收

1. 在 PDS 中重新打开 `hdmi_loop.pds`，确认两个新增源文件在工程中；完成编译、综合、布局布线、时序检查和位流生成。旧 `.sbit` 不包含新窗口。
2. 下载新位流，按现有 PCIe 流程重新枚举 FPGA，接好 OV5640。核对 PCLK、VSYNC、HREF、电源和模块是否自带 XCLK。
3. 已附带交叉编译的 ARM64 Linux 静态程序 `pcie_video_sources`，可直接同步后进入第 4 步。需要重编译时，在原有 Ubuntu 构建环境中同步 `pcie_video_sources.c`、`build_video_sources.sh`，构建用户程序：

```bash
cd ~/pcie_host
bash build_video_sources.sh
```

若 SDK 不在默认位置：

```bash
OHOS_NATIVE=/实际路径/native bash build_video_sources.sh
```

也支持 `PREVIEW_ZIG=/实际路径/zig bash build_video_sources.sh`。该程序不需要内核模块签名，也不要求修改已有 HDMI 驱动。

4. 沿用已有 HDC 连接，将程序发送到 RK3568：

```bash
"$HDC" -t "$TARGET" file send pcie_video_sources /data/local/tmp/pcie_video_sources
"$HDC" -t "$TARGET" shell chmod 755 /data/local/tmp/pcie_video_sources
"$HDC" -t "$TARGET" shell ls /sys/bus/pci/devices
```

确认 PCIe FPGA 的实际 BDF，再以板端 root 权限运行。以下 BDF 是示例，应替换为原 HDMI 验证时使用的设备：

```bash
"$HDC" -t "$TARGET" shell /data/local/tmp/pcie_video_sources 0000:01:00.0 10 1000
```

程序只读 BAR0，不写 DMA 命令，可在现有 HDMI 预览运行时观察摄像头采集统计。如果系统禁止用户空间映射 PCIe BAR，需要使用板端 root 环境，或下一阶段改为由驱动导出诊断状态。

| 验收项目 | 预期 |
|---|---|
| 描述 | HDMI=1920×1080；OV5640=640×480；Ethernet=未实现 |
| 初始化与帧状态 | `init=1 seen=1 valid=1 errors=0x00` |
| 最近帧大小 | `pixels=307200 lines=480 line_bytes=1280` |
| 连续输入 | `good` 随采样递增，`bad` 在采样期间不增加 |
| 正常图像 | 移动物体时像素累加和通常变化；静止画面或色条无需变化 |
| HDMI 回归 | 按原 `PREVIEW.md`/`COLOR.md` 使用已有驱动和应用，仍能收到原尺寸画面 |

工具默认观察 5 次、每次间隔 1 秒，成功返回 0；采集异常返回 2；参数、映射或元数据问题返回 1。单次采样只能检查已有帧，多次采样才能证明新完整帧持续到来。统计和字节配对属于本阶段验收，尚不能在 RK3568 显示摄像头图像。

## 接下来的实现顺序

先加入摄像头专用行/帧缓存，与 HDMI 共享 DDR 控制器时增加写入仲裁和独立帧地址；再让 PCIe 调度两路完整帧，给主机流增加源编号与实际尺寸；最后在 RK3568 将两路解码后的图像拼接显示。以太网接收作为第三路沿用相同帧描述和调度机制。
