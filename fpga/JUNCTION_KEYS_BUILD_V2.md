# 路口方向键v2：保留原视频按键（2026-11-02）

v1将KEY1～KEY3改作方向键，屏蔽了原HDMI图像模式和阈值调节，不符合保留原功能的要求，现已作废。v2恢复原按键接线，使用空闲KEY5～KEY7新增路口方向选择。原模型、视频DMA和当前冻结推演逻辑均未修改。

| 按键 | 引脚 | v2功能 |
| --- | --- | --- |
| KEY1 | K18 | 原HDMI图像模式切换，包含暗光增强 |
| KEY2 | L15 | 原处理阈值增加，包含暗光增强阈值 |
| KEY3 | J17 | 原处理阈值减小，包含暗光增强阈值 |
| KEY4 | K16 | 原UDP视频开关 |
| KEY5 | J16 | 路口左转 |
| KEY6 | J19 | 路口直行 |
| KEY7 | H20 | 路口右转 |
| KEY8 | H17 | 预留，本次未接入 |

KEY1～KEY4仍直接在FPGA中处理，不依赖Ubuntu预览或新增按键服务。KEY5～KEY7经原只读PCIe事件通道传递到当前预览；N分析、G播放保持原操作。

## 产物与构建

| 交付物 | 字节数 | SHA256 |
| --- | ---: | --- |
| `hdmi_loop.junction_keys_v2.sbit` | 2,101,696 | `4ccbd7dcd07ee22bf496e5d1dd4ba8fc9936c8908f608bdd803bda711f3ae5df` |
| `../09_pcie_test/pcie_host/rk3568_fpga_keys_deploy/rk_fpga_keys` | 1,135,064 | `118b69adb46205b0514312cd44fd7954de0297c3f20864233301296607c4a3dc` |
| 原三路`hdmi_loop.ethernet_video_v1.sbit` | 2,101,696 | `58e2c6b17e13a282ffb67b8908568088ce78aa04bf16b49f1bb070f670774626`（保持原文件） |

独立构建目录：`../junction_keys_build_v2_20261102/`。PDS2022.2-SP6.4 build146967，目标PGL50H/FBG484/-6，命令如下：

```powershell
& 'F:\PDS_2022.2\PDS_2022.2-SP6.4\bin\pds_shell.exe' `
  -project 'F:\B_file\jichuangsai\Pango_FPGA\junction_keys_build_v2_20261102\hdmi_loop.pds' `
  -run gen_bit_stream
```

PDS退出码0，日志包含`Generating Programming File done`和`Process "Generate Bitstream" done`，总耗时17分44秒。发布位流与`generate_bitstream/hdmi_loop.sbit`哈希一致；构建目录的顶层RTL和FDC与当前工程文件哈希一致。资源DRM132/134、IO169/296。本轮修改前的v1源码、位流及适配程序保存在`../junction_keys_v1_superseded_20261102/`，最初三路基线仍在`../junction_keys_baseline_20261102/`。

## 验证

| 检查 | 结果 |
| --- | --- |
| 新旧按键顶层隔离 | `sim/junction_keys_legacy_tb.v`通过；实际例化hdmi_loop，验证KEY1六种原图像模式（含暗光增强）、KEY2/3阈值增减、KEY4开关脉冲，与KEY5/6/7路线事件互不干扰；原键和方向键同时按下也能独立执行 |
| 方向键消抖 | `sim/junction_keys_tb.v`通过，含长按不连发、组合键拒绝、开机按住和复位 |
| BAR与完整视频回归 | `sim/run_multivideo.ps1`通过，包含新旧按键、BAR0/BAR1、HDMI冻结、摄像头预取及完整帧、灰度/RGB565、三源切换与三路启用 |
| 以太网回归 | `sim/run_ethernet.ps1`通过，含跨时钟状态、2048/614400字节帧、600包、CRC与异常恢复 |
| 预览与按键协议 | 复跑6项`tests/test_fpga_junction_keys.py`全部通过；本轮未修改模型、预览识别或冻结逻辑，未重复运行上一版已通过的完整186项Python回归 |
| RK适配程序 | Zig交叉编译通过，`aarch64-linux-musl -O2 -std=c11 -Wall -Wextra -Werror -static`，更新服务提示为KEY5/6/7 |
| 启动入口 | Bash入口语法和PowerShell构建脚本解析通过 |

回归日志：独立构建目录`rtl_multivideo.log`、`rtl_ethernet.log`；PDS日志`build_run.log`。仿真使用与原工程兼容的Icarus12。

## 时序与实机边界

报告`../junction_keys_build_v2_20261102/report_timing/hdmi_loop.rtr`仍为**Some Constraints Violated**。没有添加宽泛false-path掩盖约束。

| 同域时钟 | Slow setup WNS/ns | Slow hold WHS/ns |
| --- | ---: | ---: |
| sys_clk | +11.982 | +0.040 |
| HDMI pixclk_in | -0.772 | +0.038 |
| DDR ddrphy_clkin | -0.633 | -0.158 |
| camera cam_pclk | +3.583 | +0.255 |
| Ethernet派生125MHz | -1.316 | -0.104 |

报告还包含跨域与恢复约束违例，不能把所有违例认定为旧问题或假路径；也没有独立PCIe DMA时钟条目可用于新增按键逻辑的完整频率签核。因此当前位流用于上板联调，尚未完成时序收敛及实机稳定性验收。应同时检查原视频/暗光按键与新增路线按键；原三路位流保留回退。

v1和v2沿用JK01/ABI1事件协议，物理按键分配需要以实际下载位流为准。必须下载v2，不能仅更新软件后继续使用v1。

完整安装与重启命令见 [FPGA_JUNCTION_KEYS.md](../09_pcie_test/pcie_host/FPGA_JUNCTION_KEYS.md)。
