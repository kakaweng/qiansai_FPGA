# RK3568 发起双路录像：准备、开始、停止与接收配对校验

后续进度：用户已确认本页同步v5三路输出正常。本轮直接完成左右车辆辅助、请求反馈和中文驾驶面板v6的代码，跳过区域标定；升级入口、限制与独立回退见[DRIVE_ASSIST_RK3568.md](DRIVE_ASSIST_RK3568.md)。下文保留v5原始流程，v6实机效果仍待验收。

2026-10-28。第一阶段实现：Windows管理前方/右侧录像，RK3568决定何时开始，Ubuntu通过现有USB/HDC连接中转控制。HDMI与以太网仍直接进入FPGA；控制指令不经过FPGA视频协议。无需重训或转换模型，无需重新生成FPGA位流。

## 本阶段的实际行为

| 功能 | 实现 |
|---|---|
| 准备 | 两个视频文件加载后保持暂停；前方输出全屏首帧，右侧通过EVF1发送首帧 |
| 开始 | 点击运行在RK3568的控制面板；板端要求Windows回到共同起点，等实际收到两路对应首帧，再下发共同播放指令 |
| 接收配对 | RK3568在原始RGB565画面中解码时间标记，各路保留6帧；匹配同一播放编号/配对编号/播放状态，源时间差≤150ms、缓存接收年龄≤750ms |
| 开始检测 | 本轮有效PLAYING画面到达后，开启已有HDMI车道和目标识别；前方独立识别不以侧方同步状态作为识别准确率依据 |
| 重播/停止 | 重播使用新播放编号；停止识别并清除待处理/已发布结果；正在推理的旧结果即使稍后完成也不能发布；下一次推理先清空线型/灯色时序历史 |
| 断流 | 重复采到同一配对编号不能刷新证据；超过1秒没有新配对，面板显示同步丢失；可恢复，不据此执行侧方联合放行 |
| 预览 | 为保留流畅性，三路拼接仍展示各路最新画面；时间标记在拼接前读取。缓存中的配对供同步确认使用，本阶段没有把每张拼接预览改成严格同一时间的画面 |
| OV5640 | 继续手动拍手机，明确显示同步未确认，不参与双路配对 |
| 还未接入 | 左右侧目标检测、变道放行状态机、完整中文驾驶仪表、真实传感器曝光同步 |

控制面板/状态机运行在RK3568，浏览器可以在Ubuntu中显示它。该面板目前不是RK3568物理按键或触屏原生应用。开始键通过软件控制两路录像源，无法控制没有接入本程序的普通播放器。

## 一、Windows准备视频源

停止旧的以太网视频发送程序，避免两个程序往FPGA交错发送视频。HDMI显示输出设为1920×1080；推荐Windows“扩展”模式，将连接FPGA的显示输出与电脑控制屏分开。

```powershell
Set-Location 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host'
.\run_sync_hdmi_ethernet.cmd --list-monitors
```

确定哪一个编号连接FPGA。下面示例使用编号0；如果实际HDMI输出是编号1，改成`--monitor 1`。当前自动枚举仅发现编号0，不应凭空指定不存在的显示器。

```powershell
.\run_controlled_source.cmd --monitor 0 --bind 192.168.1.102
```

弹窗先选择前方视频，再选择同一场景的右侧视频。程序显示`SOURCE_CONTROL_READY`后保持运行。首帧全屏是视频准备状态，不代表开始识别。

`Set-Location`和启动器是两条命令，需要分两行执行。也可以直接用单条PowerShell命令启动，避免把两个命令误接在同一行：

```powershell
& 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host\run_controlled_source.cmd' --monitor 0 --bind 192.168.1.102
```

终端先显示`SOURCE_START`，随后显示`SOURCE_SELECT`并弹出置前的Windows选文件窗口。取消任一选择会明确提示启动取消。若不使用文件选择框，可用下方`--scene`或`--front/--right`直接指定文件。当前显示输出可能随HDMI连接改变，启动前重新`--list-monitors`核对，不能固定认为编号0就是FPGA。

也可以明确指定已有Waymo对应视角：

```powershell
.\run_controlled_source.cmd --monitor 0 --bind 192.168.1.102 --scene 'F:\waymo_data\decoded_perception\perception_10980133015080705026_780_000_800_000'
```

其他数据集使用`--front '前方.mp4' --right '右侧.mp4'`；有时间戳CSV可加`--timestamps`或分别指定。两路须来自同一行驶过程；不同起点通过CSV或启动时的`--right-offset-ms`校准。受控模式运行后锁定本地播放、跳转和速度，避免与板端命令冲突。Esc退出全屏或关闭HDMI窗口会将视频标为HELD，须重新开始。

启动器监听当前Windows VMware VMnet8地址`192.168.85.1:9012`。这是控制通道；FPGA的视频目的地址仍是`192.168.1.10:5002`。若虚拟机使用VMnet1，可以追加`--control-host 192.168.17.1`；若地址变化，改为实际Windows VMware网卡地址。不要填FPGA地址。

控制地址、随机令牌写入共享目录`sync_playback_runs/source_control.json`，中转程序自动读取，不需要手动复制令牌。令牌不写进日志。若Ubuntu连接9012超时，请检查VMware网络和Windows防火墙是否允许该Python在对应虚拟网卡上接收TCP9012；无需关闭整个防火墙。

## 二、Ubuntu构建并安装（首次执行）

共享目录应先挂载为`/mnt/hgfs/pcie_host`。现有RK3568驱动及车道/YOLO模型需要已经在板上，沿用之前的启动条件。

```bash
bash /mnt/hgfs/pcie_host/deploy_sync_control_rk3568.sh prepare
sudo -v && bash /mnt/hgfs/pcie_host/deploy_sync_control_rk3568.sh install
```

依次看到`SYNC_PREPARE_PASS`、`SYNC_INSTALL_PASS`。构建在`$HOME/pcie_sync_rk3568`进行，复用原RKNN头文件与转换好的模型，不运行apt或pip。准备和安装阶段不切换正在运行的v4服务。

## 三、启动板端控制服务和预览

先关闭旧预览，再执行：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_sync_control_rk3568.sh start
```

看到`SYNC_START_PASS`。该操作切换到独立文件名的`pcie_multivideo_server.scene_sync_v5`，保留旧v4文件；建立Ubuntu9011→板端9010和Ubuntu9004→板端9002转发。

Ubuntu第二个终端打开三路预览，并保持运行：

```bash
bash /mnt/hgfs/pcie_host/preview_hdmi_lane_fast.sh
```

目前采集循环随预览连接运行；因此不打开预览时，板端会停在“等待首帧”，这是有意的接收屏障。

Ubuntu第三个终端运行中转：

```bash
python3 /mnt/hgfs/pcie_host/sync_control_relay.py
```

看到`RELAY_CHECK_PASS`和`RELAY_READY`后保持此终端运行。

如果先启动中转、后启动Windows视频源，或文件尚未选择完成，中转会显示`RELAY_WAIT_CONFIG`并等待；配置生成后自动继续，不需要手动创建JSON或重跑模型。若Windows已显示`SOURCE_CONTROL_READY`但Ubuntu仍等待，检查共享目录是否映射到同一个工程。`--check`是一次性诊断模式，失败会给出可读提示并退出。

在Ubuntu浏览器打开：

```text
http://127.0.0.1:9011
```

点击“开始检测 / 从头重播”。命令由板端控制服务生成，Ubuntu只转发。状态应按顺序推进：

```text
视频源回到起点 → 等待两路首帧到达板端 → 下发共同播放指令
→ 等待播放画面配对 → 检测中：已确认画面配对
```

重启RK3568后，先按原流程加载PCIe驱动，随后重新执行本节`start`、预览和中转即可；不需要重新prepare/install。Windows视频源也须保持运行或重新打开。

## 验收与回退

| 观察 | 应有行为 |
|---|---|
| 只启动链路和预览，没有点击开始 | 前方/右侧停在首帧，前方显示等待开始，不运行本轮识别 |
| 点击开始 | 两路回到共同起点，接收确认后开始播放，HDMI识别恢复 |
| 播放中再次点击开始 | 重新回到起点，播放编号变化；旧配对与推理结果被清除 |
| 点击停止 | 视频保持当前画面，前方本轮识别停止 |
| Windows关闭视频源 | 超时后显示同步丢失；不把重复旧帧当新证据 |
| 录像结束或Esc退出全屏 | 显示录像结束/保持状态，须再次开始 |
| 停在等待两路首帧 | 检查预览是否运行、网口视频是否到达、HDMI是否1920×1080全屏；原始画面左上角黑白标记须完整传输 |

日志：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_sync_control_rk3568.sh log
```

若启动在模型检查通过后报`Sync control port occupied`，表示板端控制器未能监听TCP9010或UDP9013，不能算启动成功。2026-10-28已修复控制器生命周期：`start`会自动上传最新启动脚本及进程辅助脚本，按真实启动参数扫描本项目控制器；即使PID文件缺失、过期或被失败启动覆盖也能停止旧实例，且不终止仅仅提到该路径的shell/传输命令或占端口的其他程序。确认控制器监听成功后才停止旧视频服务；失败会打印相关/proc端口条目，PID文件也只在新控制器就绪后更新。

关闭旧预览后，直接重试相同的`start`命令即可应用此脚本修复，无需prepare、install、模型转换或FPGA更新。若仍失败，保留`SYNC_CONTROLLER_DIAGNOSTICS`之后的输出；不要按端口直接批量杀进程。回归覆盖PID丢失、过期/复用、多实例、重复停止、僵尸进程和停止超时；使用模拟/proc和替代kill验证，不向真实无关进程发信号。

后续实机日志确认TCP9010 inode32404、UDP9013 inode32405仍被占用，但argv扫描未识别控制器。进程识别现已增加`/proc/PID/exe`和`maps`中精确路径的可执行映射检查（含原子更新后标为deleted的旧文件），不依赖加载器是否改写argv；还会沿socket inode和`/proc/PID/fd`输出`PORT_OWNER pid=... project_controller=yes/no comm=... exe=...`。只确认属于项目的控制器会被停止；若端口仍由其他/未识别进程持有，停止启动流程并保留视频服务。无需更新模型或二进制，start会上传新脚本。真实持有进程身份和恢复结果以本次实机输出为准，不将加载器参数改写当作已被实机证实的原因。

恢复已验收的原HDMI识别v4：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_sync_control_rk3568.sh rollback
```

画面左上角的黑白码是本阶段源时间传输方案，HDMI占192×192、右侧占64×64；它会遮挡少量图像，受控模式不能关闭。文字F/T/TX是辅助核对，机器码具有CRC32；它没有修改EVF1协议，不需要FPGA解析。该方案验证录像内容配对，不代表传感器曝光同步，也不代表已经测出从曝光到显示的总延迟。

## 本地验证记录

已在Windows实际运行：6项通用播放器测试；真实Waymo素材的隐藏Tk操作测试；Python生成标记→RGB565→生产C解码/缓存匹配；生产C控制器→HTTP中转→隐藏Tk播放器的完整准备/播放/停止/重播；无接收确认不得播放；CRC错误、混合播放编号、状态不一致、源时间差过大、缓存过期、重复冻结帧拒绝；断流恢复；两个模型线程的重播重置/旧推理结果拒绝测试（Windows原生线程兼容层）。

新控制器和带双模型的完整服务已用`-Wall -Wextra -Werror`交叉编译为ARM64 GNU/Linux。Ubuntu原生pthread回归由prepare脚本执行；真实HDMI/FPGA/USB及虚拟机控制网络尚待用户实机执行上述流程后验收。未将模拟接收测试写成实机通过。
