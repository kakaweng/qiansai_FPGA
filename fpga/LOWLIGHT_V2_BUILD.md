# 暗光增强v2构建与验证记录（2026-11-02）

本版基于实体灯反馈v1，替换HDMI暗光分支，加入保边水平降噪、平滑增益及弱/中/强档位。操作、链路和同帧效果对照见 [LOWLIGHT_V2.md](LOWLIGHT_V2.md)。没有更换识别模型或修改RK驾驶规则。

## 下载产物

| 项目 | 结果 |
| --- | --- |
| 发布文件 | [hdmi_loop.lowlight_v2.sbit](hdmi_loop.lowlight_v2.sbit) |
| 长度 | 2,101,696字节 |
| SHA256 | `2fb62110b6aaf5229c02ded9215aed87fddf8b79fd90d5cd65398aef0ed8bda5` |
| 构建目录 | `../lowlight_build_final_20261102/` |
| 发布清单 | 构建目录 `release_manifest.json`，记录关键生产源哈希、时序报告、链路范围与实机状态 |
| 保留功能 | KEY1六模式；非暗光模式KEY2/3原阈值；KEY4原UDP开关；KEY5/6/7路口方向；LF01实体灯回写；三路采集 |
| 生效范围 | 物理HDMI输出及原彩色UDP抓图；PCIe→RK推理输入继续原始RGB565 |

独立发布文件与构建目录 `generate_bitstream/hdmi_loop.sbit` 的SHA256相同。原 `hdmi_loop.drive_feedback_v1.sbit`、`hdmi_loop.junction_keys_v2.sbit` 和 `hdmi_loop.ethernet_video_v1.sbit` 未覆盖。修改前源文件及位流另存于 `../lowlight_baseline_20261102/`。

## PDS与资源

| 项目 | 结果 |
| --- | --- |
| 工具/器件 | PDS 2022.2-SP6.4 build146967；PGL50H / FBG484 / -6 |
| 命令 | `pds_shell.exe -project ../lowlight_build_final_20261102/hdmi_loop.pds -run gen_bit_stream` |
| 生成结果 | 退出码0；`Generating Programming File done`、`Process "Generate Bitstream" done`均存在 |
| 最终轮总耗时 | PDS报告12分36秒 |
| 布局布线后资源 | LUT 20,541/42,800；FF 15,727/64,200；DRM 132.5/134；APM 6.5/84 |
| 相对实体灯v1 | LUT增加383，FF增加168，DRM增加0.5，APM增加3；未新增帧缓存 |
| 关键日志 | 构建目录 `build_run.log`（PowerShell重定向为UTF-16）、`report_timing/hdmi_loop.rtr` |

`source_manifest.json`记录本轮8项关键输入（顶层、增强模块、ROM、约束、反馈/PCIe源及工程文件）。发布脚本对比当前生产源与构建快照，检查PDS生成标记、验证结果和位流长度。PDS会重写工程文件时间戳与任务状态，因此工程setup规范化对比，原始输入保留为 `project_input.pds`。

## 时序

| 同域时钟 | Slow setup WNS/ns | Slow hold WHS/ns | Fast setup WNS/ns | Fast hold WHS/ns |
| --- | ---: | ---: | ---: | ---: |
| HDMI pixclk_in，148.5MHz | +0.302 | +0.039 | +2.247 | +0.033 |
| sys_clk | +12.961 | -0.151 | +15.117 | -0.018 |
| DDR ddrphy_clkin | +0.423 | -0.123 | +3.042 | -0.151 |
| camera cam_pclk | +4.264 | +0.226 | +6.737 | +0.185 |
| Ethernet派生125MHz | -0.704 | -0.100 | +1.831 | -0.069 |

HDMI像素域的同域建立/保持检查在两个角均通过；报告列出的3条涉及 `u_hdmi_lowlight/` 的详细路径没有违例。没有修改约束或添加false-path以隐藏新增电路问题。

工程整体仍为 **Some Constraints Violated**，包括表中其他域保持/以太网建立及跨域、恢复/移除约束；旧版也存在这些类别的违例，详见 [DRIVE_FEEDBACK_BUILD.md](DRIVE_FEEDBACK_BUILD.md)。这不等于已完成整个工程的时序签核。发布清单如实记录 `timing_closed=false`、`board_verified=false`；没有把所有跨域违例自动认定为假路径。

第一轮构建 `../lowlight_build_20261102/` 发现ROM→档位选择→DSP输入路径不足，增加独立增益寄存器；第二轮 `../lowlight_build_pipelined_20261102/` 定位到OSD字形选择，改用固定行译码并拆两级。前两轮位流只作诊断记录，没有作为下载产物发布。最终模块延迟10个像素时钟，顶层输出寄存器另1拍；每时钟仍处理一个像素。

## 验证范围

| 检查 | 结果与记录 |
| --- | --- |
| 最终逐像素/时序 | 218,573个向量通过：三档全灰阶、随机RGB、夜间实片采样、行边界/消隐、HS/VS/DE及帧内调档；与整数参考一致。构建目录 `verification.json` |
| 最终实际顶层 | `lowlight_top_tb`通过：物理HDMI/UDP增强、原彩色恢复、11拍同步延迟、PCIe原RGB565保持 |
| 最终按键 | 实际顶层原按键回归通过：六模式、三档饱和限幅、原阈值分开保存、KEY5～7隔离 |
| 三源完整视频 | 第一轮完成 `sim/run_multivideo.ps1`，退出0；日志 `../lowlight_build_20261102/rtl_multivideo.log`。后续仅调整增强内部流水线/OSD，重跑相关像素、实际顶层与按键验证，没有重复整个长视频回归 |
| 实体灯RTL | `sim/run_drive_feedback.ps1`通过，本轮未修改LED协议/灯光逻辑 |
| 曲线/滤波 | 三档全256项非递减、黑白端点保持、无溢出、强边缘保持；合成平坦噪声标准差比约0.636，仅验证滤波作用 |
| 同帧参考对照 | 193帧、1920×426对比视频完整解码；1/7/14秒原图与新旧算法图已目视检查；RTL档位标记图已检查 |
| 实机 | 待下载位流后核对物理HDMI颜色、清晰度、噪点、调档接缝及原按键/灯/三路功能 |

同帧对照目录为 `lowlight_review_20261102/`，对比视频SHA256为 `99e3c3960fcb0771ebf32201673ab0a665745e3caf351b438068f7f00b5aaf4f`。这些是整数参考输出，已与RTL数值对应，不能代替FPGA实机采集或真实夜间去噪精度评估。

只测试物理HDMI时无需启动RK。若同时恢复RK识别和实体灯，在下载后保持FPGA供电，重新枚举PCIe、恢复原驱动与前视服务，再打开原 `preview_front_feedback.sh`；不需要重训或重新转换模型。
