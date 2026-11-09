# FPGA 路口方向键接入当前冻结预演

本版以2026-11-02修正暂停误报后的预览为基础。用户确认只接入路口左转、直行、右转三个指令，同时保留原视频和暗光增强按键。v2将新增方向键放到KEY5～KEY7，KEY1～KEY4恢复原接线。方向键通过 FPGA → PCIe只读寄存器 → RK3568独立适配服务 → HDC → Ubuntu预览，最终调用原有路线选择动作。

| FPGA按键 | 引脚/现有端口 | 路口动作 | 对应原键盘 |
| --- | --- | --- | --- |
| KEY1 | K18 / key_mode | 原HDMI图像模式切换，含暗光增强 | 原功能保留 |
| KEY2 | L15 / key_th_inc | 原处理阈值增加，含暗光增强阈值 | 原功能保留 |
| KEY3 | J17 / key_th_dec | 原处理阈值减小，含暗光增强阈值 | 原功能保留 |
| KEY4 | K16 / key_upload | 保持原UDP视频开关 | 不增加路口指令 |
| KEY5 | J16 / key_route_left | 左转 | J |
| KEY6 | J19 / key_route_straight | 直行 | I |
| KEY7 | H20 / key_route_right | 右转 | L |
| KEY8 | H17 | 预留，本次未接入 | 无 |

按方向键会打开路口界面并选中路线；已有快照时保留人物初始位置、回到推演起点。按键只选择方向，N仍用于冻结分析、G仍用于播放，原鼠标按钮与J/I/L也保留。左/右变道请求继续使用原A/D操作。本版不自动控制源播放器或实际车辆。

## 文件与基线

- 新位流：`../../06_hdmi_loop_1/hdmi_loop.junction_keys_v2.sbit`，构建/校验记录见同目录 `JUNCTION_KEYS_BUILD_V2.md`。原三路位流不变；上一版`junction_keys_v1`占用了原按键，已作废，不要再下载。
- RK3568静态程序：`rk3568_fpga_keys_deploy/rk_fpga_keys`，SHA256记录在该目录 `manifest.json`；无需安装RKNN、Python或动态库。
- 预览启动：`preview_front_fpga_keys.sh`；不需要方向键时仍使用原 `preview_pie_anticipation.sh`。
- 本次涉及的修改前文件及原位流备份：`../../junction_keys_baseline_20261102/`，包含路径、长度和SHA256清单。原 `frozen_junction.py` 保持不变。
- 原三路固件与软件位置见 [FIRMWARE_CATALOG.md](FIRMWARE_CATALOG.md)。新位流仍保留HDMI、OV5640、以太网采集能力，单/三路预览主要由RK服务的 `--source hdmi/all` 决定。

KEY1～KEY4与KEY5～KEY7使用独立输入，原模式/阈值控制不再被屏蔽。原四键仍由FPGA直接执行，关闭Ubuntu预览或独立按键服务不会禁用它们。KEY1的原循环为灰度→滤波→二值化→边缘→暗光增强→原彩色，随后循环。RTL参数 `JUNCTION_KEYS=0` 只关闭新增PCIe方向键能力，不影响原视频按键；参数变更需要重新构建。

## 安装与使用

1. 在Windows的PDS下载器选择 **`hdmi_loop.junction_keys_v2.sbit`** 下载。位流重配后保持FPGA供电，重启RK3568，让PCIe重新枚举。v1和v2沿用相同事件协议，适配服务无法仅凭旧ABI自动区分物理键分配，必须核对下载文件名和SHA256。
2. 共享目录正常挂载时，在Ubuntu终端恢复驱动、启动现有前视识别服务：

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
export FPGA_BDF="0002:21:00.0"
sudo -v
sudo "$HDC" list targets
sudo "$HDC" -t "$TARGET" shell \
  'if [ ! -d /sys/module/pango_pcie_multivideo ]; then insmod /data/local/tmp/pango_pcie_multivideo.ko profile=1 cached_dma=1; fi'
SYNC_PROFILE=front_pie bash /mnt/hgfs/pcie_host/deploy_sync_control_rk3568.sh start
```

此命令复用已经安装在板上的当前模型和前视程序。共享目录不存在时，先恢复VMware共享挂载；不创建一个空目录假装同步成功。

3. 在同一Ubuntu终端安装、启动独立按键服务：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_fpga_junction_keys.sh
```

应依次看到 `FPGA_KEY_INSPECT_PASS`、`FPGA_KEYS_BOARD_START_PASS`、`FPGA_KEYS_LINK_PASS`、`FPGA_KEYS_DEPLOY_PASS`。旧位流会明确报 `FPGA_KEY_CAPABILITY_ABSENT`；必须先下载新位流，单独更新预览不能让旧RTL产生按键信息。部署不重训、不转换模型、不更换当前视频服务。

4. 关闭旧预览，在第二个Ubuntu终端运行：

```bash
bash /mnt/hgfs/pcie_host/preview_front_fpga_keys.sh
```

5. 请在预览打开后再按KEY5/6/7；启动前已经按下的键不会补发。正常播放几秒，在Windows暂停原片，按FPGA方向键进入路口并选择路线，按N分析；快照就绪后G播放。推演中换方向会回到同一快照的起点，G再次播放。重启RK或HDC转发丢失后，重新启动当前识别服务，并再次执行步骤3、4。

## 按键没有画面反馈时

2026-11-02修正预览方向显示：冻结推演已更新方向，但原绘制层仍引用普通路口方向，导致路线与按钮可能保持旧方向。现统一使用冻结推演的方向；新增实际compose回归验证三种路线与按钮高亮。关闭旧窗口，重新运行`preview_front_fpga_keys.sh`即可复制并启用修正，无需重烧v2、重启RK服务或更新模型。

当前入口先打印`FPGA_KEY_PREVIEW_VERSION route-display-v2`。随后各层日志含义如下：

| 日志 | 含义 |
| --- | --- |
| `FPGA_KEYS_ENABLED` | 当前视频进程已启用硬件键 |
| `FPGA_KEY_BASELINE` | 已连通，并建立初始事件基线；此时再按键，不重放之前的事件 |
| `FPGA_KEY_EVENT` | 新的物理事件已被预览接收 |
| `FPGA_KEY_APPLIED` | 界面已执行路线选择 |
| `FPGA_KEY_IGNORED` | 例如当前处于标定状态，路线键暂不执行 |
| `FPGA_KEY_LINK_WAIT` | 连接、超时或报文异常；原版这里静默重连，现明确输出 |
| `FPGA_KEY_EXPIRED` | UI未及时消费事件，超过500ms，旧事件已丢弃 |

部署的`FPGA_KEYS_DEPLOY_PASS`仅证明服务和转发可用，不能代替按键到画面的验收。`event=29`表示计数7、方向1（左转），单个值无法证明后续按键有变化，也不能单凭ABI1区分v1/v2物理分配。

若关闭并重开当前预览仍没有反馈，在另一个Ubuntu终端运行只读监视器，20秒内依次按下并完全松开KEY5、KEY6、KEY7：

```bash
python3 /mnt/hgfs/pcie_host/fpga_junction_keys.py --watch --seconds 20
```

监视器独立于视频绘制，输出事件字、事件计数、方向与请求往返时间；允许保留预览同时运行。计数不变时继续核对实际下载位流与物理按键，尚不能据此断定具体硬件故障；计数变化而UI没有`EVENT/APPLIED`时核对预览日志。监视器采用2秒取样期限，预览原来的200ms单次读超时/350ms整次取样期限保持不变，避免无证据放宽旧事件时效。

此修正8项按键/显示/网络监视测试与17项冻结推演回归通过（合计25项）；实机事件及显示仍需按以上日志验收。

## 协议与验证边界

| 部分 | 策略 |
| --- | --- |
| 物理键 | 低有效、两级同步；在PCIe时钟域直接整体消抖，不跨时钟传多位事件计数 |
| 消抖 | 2,500,000周期，125MHz约20ms、62.5MHz约40ms；长按只发一次，释放后再按才增加事件 |
| 多键/开机按住 | 组合键拒绝；必须完整释放并稳定后才接受下一次；开机按住不产生路线动作 |
| PCIe BAR0 | 0x170=`0x4a4b3031`，0x174原子事件字（高30位计数、低2位方向），0x178按下掩码，0x17c ABI=1；保留原0x000/0x100～0x16f窗口 |
| RK服务 | 只读映射resource0，不写BAR、不申请视频DMA；监听127.0.0.1:9014，每连接输出一行JSON状态 |
| 转发/预览 | Ubuntu9016→RK9014；后台轮询，不在绘图线程等待网络；整次取样最多350ms；仅使用新事件，首次连接/重连建立基线，不重放历史按键；未消费事件超过500ms清除 |
| 保留的功能 | 模型、检测阈值、视频时间/同源配对、冻结快照、礼让规则和Claude界面布局复用当前版本 |

上一版Windows Python完整186项回归通过，v2复跑6项按键协议与场景测试通过。新增`sim/junction_keys_legacy_tb.v`直接例化实际顶层，验证原六种图像模式（含暗光增强）、阈值增减、KEY4视频开关脉冲均保留，且与KEY5/6/7路线事件互不干扰。RK静态程序已重新交叉编译。真实按键、电气连接、HDC及整条链路需上板验收；v2最终位流和回归结果见`JUNCTION_KEYS_BUILD_V2.md`，不把生成位流等同于全部时序约束通过。
