# 辅助驾驶实体灯反馈v1构建记录（2026-11-02）

本轮在方向键v2上增加三业务灯反馈及PCIe专用回写通道，保留原KEY1～4图像/暗光功能、KEY5～7方向键及三源视频采集。灯义和安装步骤见 [DRIVE_FEEDBACK_LEDS.md](DRIVE_FEEDBACK_LEDS.md)。

## 产物

| 交付物 | 字节数 | SHA256 |
| --- | ---: | --- |
| `hdmi_loop.drive_feedback_v1.sbit` | 2,101,696 | `837014ba23d44673ab957ce5e6fa2ddc2ba57e7971bea23eedc12ae3983752fa` |
| `../09_pcie_test/pcie_host/rk3568_fpga_feedback_deploy/rk_fpga_feedback` | 1,042,896 | `d645b51abd8f0091da8b85f19abf7e9f8cde63d5b2f3055bb7b0985107383fe8` |
| 保留的方向键v2位流 | 2,101,696 | `4ccbd7dcd07ee22bf496e5d1dd4ba8fc9936c8908f608bdd803bda711f3ae5df` |
| 保留的原三路位流 | 2,101,696 | `58e2c6b17e13a282ffb67b8908568088ce78aa04bf16b49f1bb070f670774626` |

位流发布路径没有覆盖原版本。原修改前四份生产源保存在 `../drive_feedback_baseline_20261102/`；独立构建为 `../drive_feedback_build_20261102/`，`source_manifest.json`记录五份生产源SHA256，发布脚本同时核对当前工程和构建快照。新文件与构建目录 `generate_bitstream/hdmi_loop.sbit`逐字节哈希一致。

后续部署诊断补充：适配器在能力不匹配时输出实际LF01 magic、ABI和原按键magic；启动脚本先报告PCIe enable及driver，设备未启用时明确提示恢复原驱动，避免将未启用的BAR访问直接归因为位流错误。适配器重新交叉编译及协议测试通过；FPGA位流未改变。

## PDS结果

| 项目 | 结果 |
| --- | --- |
| 工具/器件 | PDS 2022.2-SP6.4 build146967；PGL50H / FBG484 / -6 |
| 运行命令 | `pds_shell.exe -project ../drive_feedback_build_20261102/hdmi_loop.pds -run gen_bit_stream` |
| 退出及生成标记 | 退出码0；`Generating Programming File done`和`Process "Generate Bitstream" done`均存在 |
| PDS报告总耗时 | 13分43秒 |
| 资源 | LUT 20,158/42,800；FF 15,559/64,200；DRM 132/134 |
| 生成日志 | `../drive_feedback_build_20261102/build_run.log`（Windows PowerShell重定向为UTF-16） |
| 发布清单 | 构建目录 `release_manifest.json`，主机目录 `rk3568_fpga_feedback_deploy/fpga_manifest.json` |

## 本地验证

| 项目 | 范围/结果 |
| --- | --- |
| 新LED RTL | `sim/drive_feedback_tb.v`通过：全部模式、方向、极性、心跳不重置相位、超时、OFF保持、非法命令/地址、字节使能及复位；含BAR基址页位不为0的写地址 |
| BAR0状态 | `pcie_video_info_tb.v`通过：LF01新增窗口、原窗口及数据选择 |
| 原按键和三路视频 | `sim/run_multivideo.ps1`通过，日志 `../drive_feedback_build_20261102/rtl_multivideo.log`；实际顶层验证KEY1六模式含暗光、KEY2/3阈值、KEY4上传与KEY5/6/7隔离；含HDMI/摄像头完整帧、预取、灰度/RGB565、多源切换与三路启用 |
| Python反馈 | `tests/test_fpga_feedback.py` 9项通过，覆盖停车/礼让优先、非相关行人不触发、实线/虚线、未知、断线、模拟场景、报警保持、非阻塞提交及真实命令ACK |
| 实际预览集成 | `tests/test_fpga_feedback_ui.py` 2项通过，实际LiveDashboard核对硬件选路、显示方向和灯光命令一致；冻结播放、暂停、断线及返回实时一致 |
| 原预览回归 | `tests/test_fpga_junction_keys.py` 8项、`tests/test_frozen_junction.py` 17项通过；本轮相关Python合计36项 |
| C适配器 | 共享协议本机边界测试通过；Zig交叉编译 `aarch64-linux-musl -O2 -std=c11 -Wall -Wextra -Werror -static`通过 |
| 入口语法 | Bash `-n`和Python编译检查通过；发布脚本对UTF-16构建日志与生产源哈希校验通过 |

没有重复运行与本轮修改无关的全部训练/推理回归。没有更换模型、感知阈值、行人横道关联、右转规则或界面布局。

## 时序与上板边界

当前报告仍为 **Some Constraints Violated**，清单明确记录 `timing_closed=false`、`board_verified=false`。下表为本轮同域慢角报告，不能只凭位流生成成功判断视频与PCIe稳定性已验收。

| 同域时钟 | Slow setup WNS/ns | Slow hold WHS/ns |
| --- | ---: | ---: |
| sys_clk | +12.999 | -0.296 |
| HDMI pixclk_in | -0.299 | -0.062 |
| DDR ddrphy_clkin | +0.759 | -0.156 |
| camera cam_pclk | +2.790 | +0.253 |
| Ethernet派生125MHz | -0.776 | -0.104 |

报告另有跨时钟、恢复和移除约束违例；不能全部当作假路径，也没有添加宽泛false-path掩盖。PCIe `pclk_div2`未在时钟汇总中独立列出，因此新增反馈逻辑尚未完成完整频率签核。本产物为上板联调版本，旧版本保持可回退。

硬件灯效按Gen2 / 125MHz计算；若实际协商为Gen1 / 62.5MHz，硬件闪灯周期与1500ms名义租期均翻倍。部署脚本打印 `current_link_speed`以便核对；仿真验证的是时钟周期计数，没有替代真实链路速率测量。

尚需上板目视与端到端确认：A2/B3/C5对应的实际灯丝印及极性、LF01实体回写确认、KEY5/6/7选路与灯同步、行人礼让/停车/报警显示一致、预览关闭灭灯、异常断开后撤销旧通行，以及视频与原按键功能。当前未连接Ubuntu/RK，未执行这些实机验收。

目前灯光镜像“板端判断＋Ubuntu当前预演状态”，由预览后台5Hz回传；冻结预演的通行灯代表模拟车辆动作。待DevEco本地化时，可把相同映射与适配服务移入RK本地应用。
