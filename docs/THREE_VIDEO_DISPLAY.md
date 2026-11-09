# 三路低负担传输展示（2026-11-02）

三路模式只展示HDMI、OV5640和以太网并行采集及传输；当前HDMI单路的车道、目标、斑马线、2.5D、冻结推演独立保留。两种模式共用板端9002，切换前先关闭旧预览。

| 部分 | 本版行为 |
| --- | --- |
| FPGA | 沿用现有三路采集；当前 `hdmi_loop.junction_keys_v2.sbit` 也保留了三路，无需为此重复烧写 |
| RK3568 | 启动留存的静态 `pcie_multivideo_server.ethernet_v6`，不加载模型；RK负责拼接 |
| 三路布局 | 左HDMI 640×360；右上OV5640 320×240；右下以太网320×240；拼接输出1280×480 RGB565 |
| Ubuntu | 独立目录 `~/pcie_three_video`；接收线程只保存最新完整、校验通过的帧 |
| 显示 | 只在新帧到达时转换；断流状态4Hz刷新；复用静态装饰，不轮询驾驶面板 |
| 校验 | 板端输入校验及输出FNV-1a完整保留；Ubuntu必须使用C加速库 |
| 发送等待 | 默认2000ms，可用 `THREE_SEND_TIMEOUT_MS` 在100～30000ms间调整；这是单次发送阻塞的限制，不是端到端延迟上限 |
| 原按键 | KEY1～4的原视频/暗光/阈值/UDP功能保留；纯传输界面没有路口模型，KEY5～7在此模式不切换模拟路线 |

## 重启后的Ubuntu终端一

先确保FPGA已下载对应位流、PCIe已枚举，OV5640与HDMI输入连接正常。共享目录丢失时执行：

```bash
sudo -v
sudo mkdir -p /mnt/hgfs
if ! mountpoint -q /mnt/hgfs; then
    sudo vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other
fi
ls /mnt/hgfs/pcie_host/deploy_three_video_rk3568.sh
```

仍找不到目录时，需要在VMware设置中恢复名为 `pcie_host` 的共享文件夹，指向Windows的 `09_pcie_test/pcie_host`。

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
export FPGA_BDF="0002:21:00.0"
sudo -v
sudo "$HDC" list targets
sudo -v && bash /mnt/hgfs/pcie_host/deploy_three_video_rk3568.sh start
```

脚本先检查预览依赖、原程序SHA256、上传回读校验和9004转发，再切换板端服务；未加载驱动时使用已有 `/data/local/tmp/pango_pcie_multivideo.ko`，要求版本1.3。缺少该驱动时会中止，需要先按原三路部署文档安装驱动。成功标记为：

```text
THREE_VIDEO_BOARD_START_PASS
THREE_VIDEO_START_PASS
```

新程序安装在 `/data/local/tmp/three_video_display`，不覆盖单路模型、单路程序或FPGA固件。切换会停止本项目视频服务和已识别的同步控制器；不会按端口盲目结束其他进程。不再需要同步中转终端或9011网页来开始这次纯展示。

## Ubuntu终端二

```bash
bash /mnt/hgfs/pcie_host/preview_three_video.sh
```

可加 `--fullscreen`；S截图，F全屏，Q退出。只测试接收、不打开窗口：

```bash
bash /mnt/hgfs/pcie_host/preview_three_video.sh --headless --seconds 20
```

| 画面指标 | 含义 |
| --- | --- |
| RX FPS | 接收并校验通过的拼接帧速率 |
| DRAW FPS | 实际处理的新视频帧速率；不把重复绘制旧画面算作新帧 |
| SKIP | 接收后被更新帧替换、没有显示的帧数；缓慢界面优先追赶最新画面 |
| LOCAL AGE | 从Ubuntu收到最近完整帧至当前的等待时间；不是FPGA到屏幕的总延迟 |
| STALE | 连接断开或超过1秒没有新的完整拼接帧；不能据LIVE推断每一路源都在更新 |

以太网没有完整帧时是黑窗，停流后可能保留最后完整帧。当前协议缺少第三路独立时间戳，不虚构右侧实时性或三路同步偏差。

## Windows输入源

本轮已导出同一Waymo场景的三份干净视频，使用非受控播放器启动HDMI与以太网：

```powershell
Set-Location 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host'
.\run_sync_hdmi_ethernet.cmd --front '.\demo_cases_20261102\09_three_inputs\front.mp4' --right '.\demo_cases_20261102\09_three_inputs\ethernet_right.mp4' --monitor 0 --fullscreen --bind 192.168.1.102 --no-markers
```

确认 `--monitor` 对应接到FPGA的HDMI输出屏，`--bind` 是连接FPGA网卡的本机IPv4；默认目的地址 `192.168.1.10:5002`。播放器打开后按界面的播放操作，保留共同暂停/重播功能。更换素材时替换 `--front` 与 `--right`。不要同时启动另一个以太网发送器。

OV5640继续用手机播放 `09_three_inputs/left_reference.mp4` 拍屏，手动开始。这种展示说明三条物理链路并行工作，不宣称OV5640自动同步，也不宣称全景几何融合。Waymo原内容约10FPS，拼接窗口刷新更高也不能让源内容变成30FPS。

## 日志与恢复单路

预览运行时获取性能日志：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_three_video_rk3568.sh log
```

关闭三路预览，恢复当前单路：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_three_video_rk3568.sh restore-front
bash /mnt/hgfs/pcie_host/preview_front_fpga_keys.sh
```

恢复命令使用已安装的 `front_pie` 服务，不重新训练。硬件按键服务须已经按 `FPGA_JUNCTION_KEYS.md` 部署；RK刚重启而按键服务尚未恢复时，先执行该文档中的部署命令。单路方向键、冻结预演及已有模型保留。

## 本轮验证与限制

| 检查 | 结果 |
| --- | --- |
| 65536种RGB565颜色 | 新OpenCV转换与原Python转换逐字节一致 |
| TCP回放 | 损坏帧不发布；断线后重连；同连接重复序号拒收；新连接允许重启序号 |
| 最新帧显示统计 | 界面只计新帧，跳过数量正确，停流绘制帧率降至0 |
| 模拟HDC | 上传回读损坏时不切服务；缺少板端标记时不报成功；正常流程通过 |
| Bash | 三个新增入口语法检查通过 |
| 本机转换微基准 | Windows/OpenCV 4.11.0、1280×480、单线程、100次：原7.18ms，新1.67ms；不代表Ubuntu或RK帧率 |
| 画面布局 | 本地构造三路输入复核，示意图 `rk3568_three_video_deploy/preview_fixture.png` 明确标注非板端采集 |
| 实机 | 本轮未连接Ubuntu/RK，实际FPS、断流恢复及物理延迟待上板确认 |

运行相关回归：`python tests/test_three_video_preview.py -v`、`python tests/test_three_video_deploy.py -v`。低延迟验收建议用手机同框拍摄源画面与预览中的计时器，观察总延迟及抖动；不能仅凭FPS报告端到端延迟。
