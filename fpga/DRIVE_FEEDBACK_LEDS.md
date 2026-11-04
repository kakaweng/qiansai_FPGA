# 辅助驾驶实体LED反馈v1（2026-11-02）

本版增加三盏业务灯，表达当前预览所呈现的请求、通行、停车、礼让、报警和未知状态。不是重新训练模型，也不在FPGA中重复实现识别；当前HDMI单路识别和2.5D沿用已有逻辑。

## 指示含义

| 场景 | 三盏业务灯 | 说明 |
| --- | --- | --- |
| 左/直/右方向已选、等待开始 | 相应方向灯1Hz慢闪 | 请求已收到，不表示已获通行许可 |
| 模拟车辆在选定路线行驶，或当前有效规则给出通行状态 | 相应方向灯常亮 | 2.5D下表示模拟动作；不是对真实车辆发执行命令 |
| 红/黄灯停车，或模拟暂停/结束 | 三灯常亮 | 区别于有方向的通行常亮 |
| 当前路线与行人存在冲突，需要礼让 | 三灯双闪，每秒100ms亮、200ms灭、100ms亮、600ms灭 | 行人须由已有规则/路线冲突确认；不按画面全部行人数触发 |
| 实线变道请求被拒绝、模拟违规/闯灯报警 | 三灯4Hz快闪 | 软件至少保持2秒，新的有效报警可延长；停止或未知状态撤销旧报警保持 |
| 当前所需结果未知、过期、连接断开 | 三灯1Hz慢闪 | 撤销通行常亮，未知不会显示成可以走 |
| 虚线变道请求 | 左/右灯1Hz慢闪 | 只说明边界条件与请求反馈；单路前视未证明侧后方安全，不显示完全允许变道 |
| 未启动/停止检测、正常关闭反馈预览 | 三业务灯灭 | A3/B2原初始化灯保持原含义 |

报警优先于停车/礼让和方向请求；没有报警时，当前停车/礼让状态优先于虚线请求。在冻结快照模式中，灯跟随固定场景推演；允许离线重播已采集的快照，日志 `simulated=1` 明确区分。在实时模式中，控制/画面断开会进入未知。

## 灯与按键

| 输出 | 芯片脚位 | 新含义 | 引脚依据 |
| --- | --- | --- | --- |
| drive_led[0] | A2 | 左方向业务灯 | 本板PCIe示例 `ipcore/pcie_test/pnr/example_design/pango_pcie_top.fdc` 的 pclk_led |
| drive_led[1] | B3 | 直行业务灯 | 同示例的 rdlh_link_up |
| drive_led[2] | C5 | 右方向业务灯 | 同示例的 smlh_link_up |
| ddr_init_done | A3 | 原DDR初始化 | 保持原接线 |
| led_int | B2 | 原初始化完成 | 保持原接线 |

实际板上LED丝印与亮灯极性仍需目视对应，表中不擅自命名为板上LED1/2/3。默认高电平点亮，支持软件 `--fpga-led-active-low` 反相，不需要重新综合。原KEY1图像/暗光模式、KEY2/3阈值、KEY4 UDP开关及KEY5/6/7方向事件均保持。

## 反馈链路与寄存器

```text
当前板端识别/规则 + Ubuntu当前2.5D推演
  → 预览后台5Hz最新状态
  → HDC TCP9017 → RK本机TCP9015
  → rk_fpga_feedback → PCIe BAR1 0x180
  → FPGA灯光状态机 → A2/B3/C5
```

| BAR | 字节偏移 | 内容 |
| --- | --- | --- |
| BAR1写 | 0x180 | 单个32位命令：`0x4c310000 | active_low<<9 | simulated<<8 | direction<<4 | mode` |
| BAR0读 | 0x180 | 魔数LF01，即 `0x4c463031` |
| BAR0读 | 0x184 | 最近接受的32位命令，用于真实回读确认 |
| BAR0读 | 0x188 | bit8=1500ms租期有效，bits2:0=当前逻辑亮灯位 |
| BAR0读 | 0x18c | ABI=1 |

mode：0灭、1方向待处理、2方向通行、3停车、4礼让、5未知、6报警；direction：0无、1左、2直、3右。严格检查魔数、保留位、模式、完整低4字节写使能；GO和PENDING必须有方向。

RX写地址保留物理BAR基址的高页位，因此按 `wr_addr[7:0]==0x18` 解码4KiB内偏移，与现有DMA桥一致；上游BAR1命中已经限定。不会解码为原DMA的0x130/0x140/0x150/0x160控制寄存器。新增BAR0的0x180状态窗口，原0x000和0x100～0x170保持。

软件仅写专用0x180，不提供任意MMIO接口。5Hz心跳不重置闪灯相位；停止更新1500ms后FPGA撤销旧通行并改未知慢闪。明确OFF命令不因租期到期重新闪灯。预览刷新停止750ms时后台先发未知；程序崩溃/链路失联时由FPGA超时兜底。该计时不改变感知结果有效期，也不补足模型漏检。

硬件时间以PCIe `pclk_div2=125MHz`为基准，当前IP最大/目标速率均为Gen2。若实际协商降为Gen1，时钟为62.5MHz，闪灯周期和硬件失联期限都会翻倍（约3秒）；软件750ms刷新检查不变。部署时打印实际链路速率，不能只根据IP配置认定实机已达到Gen2。此次时序报告未单列PCIe时钟，新增灯光逻辑也未完成该时钟域的完整时序签核。

## 构建与发布

源文件：`src/pcie/drive_feedback_leds.v`、顶层及FDC、PCIe顶层/状态读窗口。新模块已加入PDS工程。原发布的 `hdmi_loop.junction_keys_v2.sbit` 与 `hdmi_loop.ethernet_video_v1.sbit`保留。

独立构建目录为 `../drive_feedback_build_20261102`，生产源指纹保存在 `source_manifest.json`。准备及构建命令：

```powershell
Set-Location 'F:\B_file\jichuangsai\Pango_FPGA\06_hdmi_loop_1'
& '..\09_pcie_test\pcie_host\.venv-lane\Scripts\python.exe' tools/prepare_drive_feedback_build.py
& 'F:\PDS_2022.2\PDS_2022.2-SP6.4\bin\pds_shell.exe' -project 'F:\B_file\jichuangsai\Pango_FPGA\drive_feedback_build_20261102\hdmi_loop.pds' -run gen_bit_stream
```

PDS已成功生成，源指纹一致；新位流单独发布为 `hdmi_loop.drive_feedback_v1.sbit`，不覆盖旧版本。当前报告仍有时序违例，产物用于上板联调。详细记录见 [DRIVE_FEEDBACK_BUILD.md](DRIVE_FEEDBACK_BUILD.md)。

## 上板后使用

新位流需要重新下载。先关闭旧预览，按原流程让RK重新枚举PCIe、恢复HDMI单路服务和方向键服务；然后在Ubuntu执行：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_fpga_feedback.sh
```

依次应出现协议、LF01能力、启动、链路与 `FPGA_FEEDBACK_DEPLOY_PASS` 标记。检查失败时脚本会中止，不向不支持LF01的旧位流写入。

若已下载新位流并重启RK，但出现 `FPGA_FEEDBACK_CAPABILITY_ABSENT`，先核对是否恢复了PCIe驱动。共享目录和HDC可用不代表PCIe内存访问已启用；原驱动在probe中调用 `pcim_enable_device`。恢复命令：

```bash
sudo -v
sudo "$HDC" -t "$TARGET" shell \
  'if [ ! -d /sys/module/pango_pcie_multivideo ]; then insmod /data/local/tmp/pango_pcie_multivideo.ko profile=1 cached_dma=1; fi'
sudo "$HDC" -t "$TARGET" shell \
  'cat /sys/bus/pci/devices/0002:21:00.0/enable; readlink /sys/bus/pci/devices/0002:21:00.0/driver; ls -l /dev/pango_pcie_multivideo_0002:21:00.0'
sudo -v && bash /mnt/hgfs/pcie_host/deploy_fpga_feedback.sh
```

设备enable应为非0、驱动应为pango_pcie_multivideo。更新后的检查会输出 `FPGA_FEEDBACK_PCIE_STATE`；若启用后仍读不到LF01，保留 `FPGA_FEEDBACK_CAPABILITY_READ`实际读数再定位，不反复盲目下载位流或重装模型。日志前面的9项测试使用本地模拟连接，测试期间的GO/UNKNOWN不是实体灯结果。

先目视测试实体灯，期间关闭其他反馈预览：

```bash
python3 /mnt/hgfs/pcie_host/test_fpga_feedback_lights.py
```

顺序为左、直、右、三灯停车、方向请求慢闪、礼让双闪、未知慢闪、报警快闪。测试通过标记只表示命令被FPGA接受，需目视核对实际灯。若亮灭恰好相反，用 `--active-low` 重试。

正式识别预览：

```bash
bash /mnt/hgfs/pcie_host/preview_front_feedback.sh
```

低电平亮灯板卡使用：

```bash
bash /mnt/hgfs/pcie_host/preview_front_feedback.sh --fpga-led-active-low
```

每次状态变化在终端打印 `FPGA_LED_APPLIED mode=... direction=... simulated=... reason=...`，只有FPGA回读确认命令后才打印APPLIED。方向键先收到事件，下一次刷新即提交当前界面方向。沿用原N分析/G播放操作，不能把KEY选路线误认为已按下播放。

只想恢复原预览时关闭窗口，使用 `preview_front_fpga_keys.sh`；正常关闭时业务灯关闭。恢复旧位流亦可，新增反馈预览会报告能力缺失而拒绝工作。

## 验证边界

新增RTL覆盖灯效、极性、心跳不断相位、租期超时、完整字节使能、错误命令/地址及复位；BAR0新增状态与原窗口回归通过。原新旧按键隔离、完整三源视频回归通过。Python新增9项反馈测试、2项实际界面集成、原8项按键与17项冻结预演回归共36项通过，C协议边界本机测试与AArch64静态编译通过。

尚需实机确认：板上灯丝印/极性、PCIe实体回写、显示状态与灯光一致，以及断开预览后撤销通行。此前工程已有时序违例，新位流生成不等于时序已收敛；以本轮时序报告为准。
