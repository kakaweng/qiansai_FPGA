# 行车车道与交通灯预览

## 视频与标注范围

视频：`D:\buduinetdiskdownload\video_20260929_112507.mp4`。原片为 1920×1080、30 fps、1349 帧，约 44.97 秒。机器可读标注见 [traffic_lane_scene.json](traffic_lane_scene.json)。车道从左向右按 1 开始编号；时间段采用左闭右开区间。

| 视频时间 | 车道总数 | 本车所在车道 | 事件 |
| --- | ---: | ---: | --- |
| 0～12 秒 | 4 | 2 | 保持车道 |
| 12～15 秒 | 3 | 2 | 道路变为三车道 |
| 15～24 秒 | 5 | 3 | 道路变为五车道 |
| 24～28 秒 | 4 | 2 | 24 秒通过交通灯 |
| 28～38 秒 | 4 | 1 | 28 秒从第 2 车道变入第 1 车道 |
| 38 秒起 | 未标定 | 未标定 | 开始左转 |

9～13 秒另有箭头绿灯。用户未指定箭头方向，标注为 `direction=unknown`；其他时间的灯色不作推断。38 秒后的转弯结束时间、新道路的车道数及本车位置也未给出，不纳入车道区间。

## 使用边界

这些时间点来自人工描述，仅用于离线评分。运行时的车道和灯色判断使用当前图像，不读取 `evaluation_only` 时间轴。切换点附近按秒标注，不代表已完成逐帧边界核定。

## 预览中的识别结果

当前重点是标出本车所处车道，并提醒本车向左或向右跨越车道边界。总车道数为可选辅助信息，不作为本车区域确认或变道提醒的前置条件。

| 画面内容 | 含义 |
| --- | --- |
| 路面黄色线 | 从当前图像提取的标线候选，受透视约束过滤 |
| 本车前方半透明区域 | 估计的本车车道范围；遮挡、路口或转弯时可能无法确认 |
| `Current lane` | 当前车道区域；只有道路边界证据充分时才附加从左编号或道路相对位置 |
| `Lane change LEFT / RIGHT` | 连续边界轨迹或走廊中心跨越证据确认的本车变道提醒；保持一段时间方便观察 |
| `In-lane position LEFT / CENTER / RIGHT` | 本车在当前车道内的横向位置，与道路左/中/右车道含义不同 |
| `Signal RED / GREEN / UNKNOWN` | 红灯、绿灯或当前无法确定灯色 |
| 交通灯附近小框 | 当前用于判断颜色的亮灯点 |
| `source=yolo / scene_roi / none` | YOLO 检测框中的颜色证据、标定区域的备用颜色证据、无当前证据 |

`TRACKED` 表示经连续几何证据确认的本车区域；`CONFIRMING` 表示当前找到候选区域、尚在确认；`RECENT` 表示缺线后最多保留 350 ms 的近期区域，并降低几何证据分数。画面中的百分比是标线几何支持程度，不是实测准确率。

车道模块在缩小图像中提取白/黄标线、估计消失点并排除同帧 YOLO 车辆框。仅找到本车两侧标线即可独立确认当前车道区域；从左编号和道路相对位置仍需要道路边界证据。路口斑马线、车辆遮挡和转弯可能造成误检或 `UNKNOWN`。几何结果改变不等于发生变道，变道提醒使用连续边界轨迹或连续走廊中心跨越确认，并经过方向、持续时间和位移滞回。当前未识别箭头方向，也未增加黄色灯分类。`GO_SIGNAL_ONLY` 表示灯色提示。

后续的变道或左/右转决策需要结合邻道是否可行驶与被占用、目标路线、车道转向箭头、信号灯方向及其对应车道、行人和障碍物等证据。本轮提供当前车道与变道观测，最终转向或变道许可由后续决策层判断。

## Ubuntu 文件同步

确认 VMware 共享文件夹 `pcie_host` 已挂载到 `/mnt/hgfs/pcie_host`。若尚未挂载，在 Ubuntu 执行：

```bash
sudo mkdir -p /mnt/hgfs
if ! mountpoint -q /mnt/hgfs; then
    sudo vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other
fi
```

将本轮文件复制到 Ubuntu 实际运行目录：

```bash
cd "$HOME/pcie_host"
cp -v /mnt/hgfs/pcie_host/{preview_stream.py,traffic_light.py,lane_detection.py,traffic_lane_scene.json,check_road_video.py,LANE.md,README.md} . && \
    python3 preview_stream.py --help | grep -- '--road-scene'
```

必须看到 `--road-scene`。若参数未出现，检查共享目录是否指向 Windows 的 `F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host`，并确认正在运行更新后的预览脚本。

## HDMI 实时预览

RK3568 重启后，终端 A 仍按 [COLOR.md](COLOR.md) 第 3～4 节加载彩色驱动、启动 `pcie_rknn_probe_rgb565` 的 matched 服务并恢复 HDC 的 9001 端口转发。确认已运行后，在终端 B 执行：

```bash
cd "$HOME/pcie_host"
python3 -u preview_stream.py \
    --host 127.0.0.1 --port 9001 \
    --require-detections --profile \
    --road-scene traffic_lane_scene.json \
    --traffic-roi-fallback \
    2>&1 | tee lane_signal_preview.log
```

在 HDMI 源电脑上全屏播放本视频。配置对应原片铺满 1920×1080 的图像；播放器黑边、画面裁剪或窗口缩放会改变区域对应关系。`--road-scene` 同时启用车道和交通灯；保留原 `--traffic-scene traffic_redgreen_scene.json` 可运行第一段红绿灯视频，两参数互斥。

按 `S` 同时保存原图、检测图、灯色图和组合 `_road.png`；按 `Q` 或 `Esc` 退出。灯色和车道只分析 matched RGB565LE 同帧，超时、断线或重连时清空已识别状态。日志以 `Lane perception:`、`Traffic signal:` 记录对应采集序号与结果。

## 本地视频离线验证

将原片复制到 Ubuntu，或在已安装 OpenCV、NumPy 的 Windows Python 中执行同一脚本。替换视频路径：

```bash
python3 check_road_video.py /实际路径/video_20260929_112507.mp4 \
    --scene traffic_lane_scene.json --sample-fps 10 \
    --output-dir road_change_check_112507 --headless
```

移除 `--headless` 可显示离线预览。输出包含 `road.csv`、`road_summary.json` 和关键时刻的叠加截图。CSV 同时保存道路相对位置、车道内位置、变道方向、事件序号、事件时间戳及原因；摘要的 `lane_change_events` 对持续提醒去重，每次确认跨越只记一次。检测器使用 RGB565 量化后的真实图像；人工车道和信号真值仅用于结果评分。离线运行没有 RKNN 检测框，所以不会验证 NPU 灯框召回或车辆遮挡排除，HDMI 实机仍需核对。

### 本轮之前的历史基线

此前版本整段以 10 fps 采样、RGB565 仿真得到 450 个样本，结果保存在 `road_check_112507`。下表为历史基线：

| 验证项目 | 历史结果 |
| --- | --- |
| 9～13 秒稳定绿灯 | 37/40 个采样帧，92.5% |
| 0～38 秒本车区域可视 | 271/380 个采样帧，71.3%，包含短时保留区域 |
| 当前帧确认本车区域 | 146/380 个采样帧，38.4% |
| 从左编号 | 仅 7/380 帧输出，6 帧与人工标注一致，尚不能稳定验收 |
| 总车道数 | 无可靠输出，保留未知 |
| 38 秒转弯以后 | 本车区域保持未知 |

可视覆盖率不等于区域准确率。总车道数及本车从左编号仍需要进一步验证和改进；本轮以当前区域和真实跨界事件作为主要验证对象。第一段红绿灯场景的默认阈值保留。

### 本轮回归结果

使用当前代码对原片 RGB565 仿真、10 fps 采样得到 450 个样本，输出目录为 `road_change_check_112507_final_v2`：

| 验证项目 | 本轮结果 |
| --- | --- |
| 本车区域可视（0～38 秒标注区间） | 280/380，73.7%（含近期区域） |
| 当前帧确认区域（0～38 秒标注区间） | 114/380，30.0% |
| 从左编号 | 6/380 帧输出，6 帧正确；总数保持未知 |
| 9～13 秒绿灯 | 37/40，92.5% |
| 28 秒开始的左变道 | 约 29.5 秒确认一次 `LEFT`，事件序号 1，置信度 0.418 |

29.5 秒晚于人工记录的 28 秒，因为算法等待实际边界/走廊跨越证据；逐帧复核显示参考线在约 28.97～29.07 秒穿过画面中心。10 fps 结果用于回归，板端实时显示仍需用 matched RGB565 源实机复核。
