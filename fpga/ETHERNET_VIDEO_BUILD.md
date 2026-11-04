# 第三路以太网视频：构建与验证记录

构建日期：2026-10-25。目标器件 PGL50H、FBG484、速度等级 -6；PDS 2022.2-SP6.4 build 146967。

功能链路：主机 EVF1 UDP → FPGA 网口 → 三槽 DDR 帧缓存 → PCIe source=2 → RK3568 三路拼接。接线、Ubuntu 驱动编译签名、发送、回读和预览完整流程见 [ETHERNET_VIDEO.md](../09_pcie_test/pcie_host/ETHERNET_VIDEO.md)。

## 交付文件

| 文件 | 字节数 | SHA256 |
|---|---:|---|
| `hdmi_loop.ethernet_video_v1.sbit` | 2101696 | `58e2c6b17e13a282ffb67b8908568088ce78aa04bf16b49f1bb070f670774626` |
| `../09_pcie_test/pcie_host/pcie_multivideo_server.ethernet_v6` | 1268296 | `d28439094128866ac04273379fddc9ac1c817c1c9d3426f07d25b01787dd99a8` |
| `../09_pcie_test/pcie_host/pcie_ethernet_status` | 1046552 | `2ca60896f1fa823475d8a2b8bfac0aa05ed13176bd1a1b1e2c1f7712952c59dd` |

服务和状态工具为 ELF64、little endian、AArch64（e_machine=183）、静态链接。服务器标识 `multivideo-ethernet-v6`。驱动源码模块版本为 1.3，需在用户 Ubuntu 的实际 KDIR 中编译签名，未在 Windows 生成可供该内核加载的新 `.ko`。

新位流由 `generate_bitstream/hdmi_loop.sbit` 复制；PDS 从 compile 到 gen_bit_stream 用时 11 分钟，最终进程退出码为 0，日志包含 `Generating Programming File done` 和 `Process "Generate Bitstream" done`。

原双源位流 `hdmi_loop.camera_prefetch_v1.sbit` 保留，SHA256 仍为 `a731b28dcf766b71e699e0dd6239051057c09ddc10e891f82801287bb105829f`。原 RK 服务 `pcie_multivideo_server` 保留，为 `multivideo-overlap-v5`。本轮修改前的 FPGA src/sim/pds 备份在相邻 `third_video_baseline_20261025` 目录。

## 本地验证

| 验证 | 结果与范围 |
|---|---|
| 原双源回归 | 15 个配置通过：摄像头按需/预取、完整 VGA、HDMI 帧冻结、DDR 读命令、BAR、灰度/RGB565 和双源切换 |
| 新网络 RTL | 2048 字节压力帧和 614400 字节完整帧通过；Python 独立生成 CRC 数据，真实 GMII/IPv4/UDP 输入 |
| DDR 图像 | 每字节核对随帧号、字节位置及高位地址变化的图案，覆盖多个帧槽 |
| 错误与恢复 | 像素/帧号/长度损坏、短包、超长包、IP 分片、乱序、重复、DDR 背压和 FIFO 满恢复通过 |
| 帧槽保护 | DMA 锁住旧帧时继续发布多个新帧，旧槽无覆盖；释放后获取最新完整帧 |
| 原网口控制 | 单字节 `1`/`0` 开始/停止命令通过，不被图像包误触发 |
| PCIe 第三源 | 300 块完整帧、Ethernet DDR 基址、源切换和末拍延迟通过 |
| BAR0 扩展 | 新增 0x160～0x16f 状态窗口通过，保留原窗口 |
| 状态跨时钟 | 独立时钟下 128 位状态快照无撕裂，通过 |
| 主机 UDP | `verify_ethernet_host.py`：600 包本机 UDP 收发重组、CLI 静态回读比对、损坏拒绝通过 |
| 预览协议 | `verify_multivideo.py` 9 项通过，包含 source=2 和三源画布 |
| 三源画布 | 本机 gcc 编译 C 检查，全部 1280×480 像素、缩放位置、黑边和缺失 Ethernet 黑窗正确 |
| AArch64 构建 | 服务、状态工具、三源离线 fixture、原重叠校验 fixture 均以 `-O2 -Wall -Wextra -Werror` 编译通过 |
| 板端离线 fixture | 已提供交叉编译程序；Windows 未运行其 Linux 可执行文件 |
| 实物链路 | 未访问实物板卡；网口实收、PCIe 原图比对、内核模块加载及三源帧率待上板验证 |

复现 RTL 测试：`sim/run_multivideo.ps1`、`sim/run_ethernet.ps1`。复现板端程序构建：`../09_pcie_test/pcie_host/build_ethernet_video.ps1 -ZigPath <zig.exe>`，或 Ubuntu 的 `build_multivideo.sh`。

## 资源与最终时序

片上 DRM 用量 **133/134**。网络到 DDR 的跨时钟 FIFO 使用 32 深度的分布式 RAM，避免新增缓冲导致 DRM 超限；默认 camera FIFO 存储选择保持 auto。三块完整图像仍存放在外部 DDR。

时序依据最终 `report_timing/hdmi_loop.rtr`，报告时间 2026-10-25 00:50:48，**不是布局布线中间估算值**：

| 同域时钟 | Slow setup WNS | Slow hold WHS |
|---|---:|---:|
| HDMI pixclk_in | +0.459 ns | +0.019 ns |
| DDR ddrphy_clkin | +0.942 ns | -0.284 ns |
| camera cam_pclk | +3.381 ns | +0.223 ns |
| system sys_clk | +11.053 ns | -0.448 ns |
| Ethernet 125 MHz 派生时钟 | -0.978 ns | -0.104 ns |

最终报告仍为 **Some Constraints Violated**，没有达到整个工程时序收敛。Ethernet 同域最差 setup 路径位于既有 UDP 视频发送链路 `u_udp_video_rgb_pkt/tx_pixel_addr` → `u_eth_top/u_udp/u_udp_tx/gmii_txd`。报告还包含 DDR/摄像头/以太网之间的跨时钟路径和 recovery/removal 违例；需逐项核查同步结构、复位释放和时序约束，不能直接认定均为假路径。此版未通过增加宽泛 false-path 来隐藏这些问题。

新文件为上板调试位流。仿真通过不等于实物稳定性已验证，也不构成全部时序通过或维持原双路帧率的保证。先完成静态图逐字节回读，再检查三路动态预览。
