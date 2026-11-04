# 路口方向键位流构建记录（2026-11-02）

> 本文记录的v1占用了原KEY1～3，现已作废。当前使用保留原按键、KEY5/6/7选方向的v2，见 [JUNCTION_KEYS_BUILD_V2.md](JUNCTION_KEYS_BUILD_V2.md)。本文件及v1产物只作历史追溯。

基于现有HDMI/OV5640/Ethernet三路采集工程，新增KEY1左转、KEY2直行、KEY3右转的只读事件寄存器；原三路位流保留。模型及视频传输格式未修改。

| 交付物 | 长度 | SHA256 |
| --- | ---: | --- |
| `hdmi_loop.junction_keys_v1.sbit` | 2,101,696字节 | `57f8fbee6022b3cd0bb75aecc559d60b9454f5e11f16c84d24ef49468356a382` |
| 原 `hdmi_loop.ethernet_video_v1.sbit` | 2,101,696字节 | `58e2c6b17e13a282ffb67b8908568088ce78aa04bf16b49f1bb070f670774626`（保持原文件） |
| `../09_pcie_test/pcie_host/rk3568_fpga_keys_deploy/rk_fpga_keys` | 1,135,048字节 | `9724a4c118402569a6973a5b8ffa0415d554c5d284604f79db5c38b270a25a5b` |

FPGA目标：PGL50H、FBG484、-6。工具：PDS2022.2-SP6.4 build146967。独立构建工程位于 `../junction_keys_build_20261102/`，没有覆盖原 `generate_bitstream/hdmi_loop.sbit`。该目录包含构建时的src、IP、pds及最终报告。根目录当前源文件的按键端口注释在构建后补充了新用途，逻辑与构建版本一致。

构建命令：

```powershell
& 'F:\PDS_2022.2\PDS_2022.2-SP6.4\bin\pds_shell.exe' `
  -project 'F:\B_file\jichuangsai\Pango_FPGA\junction_keys_build_20261102\hdmi_loop.pds' `
  -run gen_bit_stream
```

最终进程退出码0，日志包含 `Generating Programming File done`、`Process "Generate Bitstream" done`。全流程约14分48秒；导出位流与独立构建产物SHA256一致。DRM用量128/134。

## 时序结果与限制

最终报告 `../junction_keys_build_20261102/report_timing/hdmi_loop.rtr` 的摘要仍为 **Some Constraints Violated**。未增加宽泛false-path来隐藏问题，不能将“成功生成位流”称为完整时序收敛。

| 同域时钟 | Slow setup WNS | Slow hold WHS |
| --- | ---: | ---: |
| sys_clk | +13.580ns | -0.340ns |
| HDMI pixclk_in | +0.873ns | +0.013ns |
| DDR ddrphy_clkin | +0.431ns | -0.280ns |
| camera cam_pclk | +4.114ns | +0.312ns |
| Ethernet125MHz派生时钟 | -0.483ns | -0.100ns |

报告还有跨域及恢复/移除约束违例；原工程本身也存在这些类目，不能据此把新报告的每条违例都判为旧问题或假路径。Clock Summary没有独立PCIe DMA时钟条目，因此该报告不能作为新增PCIe域按键逻辑的完整频率签核。当前交付为上板联调版本，仍需真实按钮与视频链路验收。

## 本地验证

- ModelSim与Icarus按键测试：方向映射、按下/释放消抖、长按、连续同方向、组合键及部分释放、开机按住、复位均通过。
- BAR0仿真：新增0x170窗口返回按键数据，原HDMI/Camera/Ethernet描述、状态和DMA/RAM读取选择保持正确。
- Windows Python186项回归通过，含6项方向键协议/后台TCP/本地场景测试，原冻结识别与推演回归继续通过。
- RK适配服务以 `-target aarch64-linux-musl -O2 -std=c11 -Wall -Wextra -Werror -static` 交叉编译；ELF64小端、e_machine183，没有PT_INTERP。未在Windows执行ARM程序。
- Icarus12完整旧视频RTL回归通过：Camera按需/预取及完整VGA、HDMI帧冻结、DDR读命令、BAR1启动、灰度/RGB565及三源切换/完整帧；以太网2048/614400字节、错误拒绝和帧槽保护均通过。Icarus13对既有DMA bridge中“声明前引用active_source”报错，未为此修改无关视频RTL。

安装操作、按键行为及协议见 [FPGA_JUNCTION_KEYS.md](../09_pcie_test/pcie_host/FPGA_JUNCTION_KEYS.md)。实机未连接，不能把本地回归当成硬件验收。
