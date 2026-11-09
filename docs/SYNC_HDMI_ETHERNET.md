# HDMI前视与以太网右视：通用源时间轴播放器

后续板端发起的受控模式见[SYNC_CONTROL_RK3568.md](SYNC_CONTROL_RK3568.md)，入口`run_controlled_source.cmd`。下文描述保留的手动播放器；受控模式增加机器时间码、板端开始指令和接收配对校验，仍不声称传感器曝光同步。

用户确定：HDMI输入正前方，OV5640拍手机上的左侧视频并手动对齐，以太网输入右侧。本阶段只处理Windows素材输出和源时间轴，板端仍使用当前三路拼接与HDMI识别服务。左右侧识别及驾驶决策尚未接入。

## 支持范围

| 项目 | 行为 |
|---|---|
| 数据集 | 任意OpenCV可解码的本地视频文件，不依赖Waymo模型、标签或目录 |
| 前方 | 保持比例补边为1920×1080，通过选定Windows显示输出全屏显示 |
| 右侧 | 保持比例补边为640×480，转换RGB565LE，通过既有EVF1协议发送600个UDP分包 |
| 时间轴 | 优先用外部CSV；否则逐帧扫描媒体PTS，允许两路不同帧率、不同尺寸和VFR |
| 操作 | 共同播放/暂停、重播、拖动进度、前后单步、速度、右侧时间偏移 |
| 背压 | 仅一对处理中、一对等待；新请求替换等待请求，避免逐渐积压 |
| 显示顺序 | 完成右侧帧UDP提交后，将其前方配对帧交给界面显示 |
| 标记 | 两路显示源帧F、源时间T和传输序号TX；HDMI标记放在上部约1/4高度避开板端顶部状态栏，右侧标记放顶部补边区；TX与源帧号不同，重播/跳转也继续递增 |
| OV5640 | 显示/记录为manual_unverified，不声称自动同步 |
| 尚未支持 | RTSP等实时流接入、源时钟自动校准、FPGA接收确认、实际曝光/扫描同步 |

同一时间轴只适用于同一行驶过程的对应视角。两个无关视频即使同时播放，也不是同一车辆的多视角证据。不同数据集需要重新检查视角、画面映射和识别效果；这不自动要求重训模型。

本程序标记的是**素材时间**，不把发送时刻或界面刷新请求当作FPGA实际接收时刻。现有FPGA以太网协议没有源PTS字段，也没有接收ACK；板端三路拼接仍使用各路最新可用画面。实际HDMI和以太网延迟需要用F/T/TX在板端画面核对。未来联合决策需扩展源时间状态输出，不能仅凭此播放器宣称接收端严格同步。

## Windows启动

依赖当前已有Python 3.11、OpenCV、NumPy、Pillow、Tkinter，不安装或转换模型。先停止旧的以太网发送程序，保留RK3568现有三路服务和Ubuntu预览。专用FPGA网卡继续使用192.168.1.102/24，FPGA默认192.168.1.10:5002。

```powershell
Set-Location 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host'
```

先本地预演，手动选择任意数据集的前方和右侧文件，不发送网络：

```powershell
.\run_sync_hdmi_ethernet.cmd --no-network
```

已知一组素材的快捷启动；`--scene`只是目录便捷入口，可省略：

```powershell
.\run_sync_hdmi_ethernet.cmd --scene 'F:\waymo_data\decoded_perception\perception_10980133015080705026_780_000_800_000' --bind 192.168.1.102
```

任意数据集直接指定文件：

```powershell
.\run_sync_hdmi_ethernet.cmd --front 'F:\其他数据集\前方.mp4' --right 'F:\其他数据集\右侧.mp4' --bind 192.168.1.102
```

程序初始暂停，准备并输出首对画面；按空格播放。它会打开控制面板和一个独立前方显示窗口。选择实际连接FPGA HDMI输入的显示器编号，点击“在所选屏幕全屏”。单显示器/复制显示模式可能只枚举一个显示器；全屏后用Esc返回控制面板。Windows输出分辨率保持1920×1080，程序不修改系统显示模式。

源画面时间码用于链路校准，会遮住一小条图像；校准完成后加`--no-markers`关闭，再评估正式识别效果。它不会修改原视频文件。

列出显示器并指定全屏输出：

```powershell
.\run_sync_hdmi_ethernet.cmd --list-monitors
.\run_sync_hdmi_ethernet.cmd --front 'F:\素材\front.mp4' --right 'F:\素材\right.mp4' --bind 192.168.1.102 --monitor 0 --fullscreen
```

Ubuntu沿用当前预览，不需要重新部署服务：

```bash
bash /mnt/hgfs/pcie_host/preview_hdmi_lane_fast.sh
```

OV5640继续手动拍手机左侧视频。软件暂停两路时，手机需要手动暂停/对齐，不能自动跟随。

## 通用时间戳

无CSV时程序读取两个文件各自的媒体PTS，并以各自第一帧为0。若两个录像的开始时刻不同，需要显式配置偏移。长视频首次扫描PTS可能需要等待。

通用CSV可以使用以下列之一，每行对应视频中的一帧：

```csv
pts_seconds
0.000
0.100
0.250
```

也支持`relative_time_seconds`、绝对`timestamp_ns`或`source_timestamp_micros`；可附加`video_frame_index`和`display_duration_seconds`。两路都提供绝对时间戳时保留两者起点的差值；这些绝对时间戳应来自同一时钟或事先校准。CSV帧数必须与视频一致、时间必须严格递增。

```powershell
.\run_sync_hdmi_ethernet.cmd --front 'F:\素材\front.mp4' --right 'F:\素材\right.mp4' --front-timestamps 'F:\素材\front_times.csv' --right-timestamps 'F:\素材\right_times.csv'
```

`--timestamps`指定两路共用CSV；`--scene`会查找目录中的front.mp4、side_right.mp4和可选frame_timestamps.csv。时间戳不可用时默认停止，用户确认素材为固定帧率后才可用`--assume-cfr`估算；估算会写入记录，不能当作同步已验证。

右侧时间偏移的定义为：`右侧目标时间 = 前方场景时间 + 偏移`。正值使右侧内容提前，负值使其推后。该项可补偿已知素材起点差异，也可在实际链路测量后尝试补偿固定延迟；它不保证补偿变化的网络/显示延迟。默认0，控制面板中可调整，范围±10000ms。

## 验证与记录

`sync_playback_runs/日期时间/session.json`记录文件身份（路径、大小、修改时间）、时间戳来源、缩放、网络参数、OV手动状态和同步范围；`frames.jsonl`记录源帧/源时间、UDP提交时间和HDMI绘制请求时间，不伪造实际接收时间。暂停/跳转使用generation隔离旧结果，网络慢时允许跳过素材帧保持时间轴。

本地回归命令：

```powershell
& 'F:\Program Files\python3.11\python.exe' tests/test_sync_video.py
& 'F:\Program Files\python3.11\python.exe' tests/verify_sync_player_gui.py 'F:\waymo_data\decoded_perception\perception_10980133015080705026_780_000_800_000'
```

`test_sync_video.py`覆盖普通AVI不同帧率、VFR CSV和绝对时间起点、暂停/速度/跳转、补边、等待队列替换，以及localhost600包CRC32重组与完整RGB565字节比较。GUI回归使用隐藏窗口、不发网络。Windows微秒包节流使用QPC高精度计时器；此机Python3.11的monotonic采用约15.6ms精度的GetTickCount64，不用于包节流。

2026-10-28：本地已验证真实Waymo前方/右侧源时间匹配，以及普通合成视频的通用路径；真实HDMI全屏输出、FPGA以太网接收完整性及两路实际延迟仍需实机确认。未重训模型、未更改FPGA位流、未更改板端识别和三路拼接协议。
