# 首个交通场景：只验证红灯、绿灯（前 30 秒）

## 范围和视频参考

用户指定 `D:\buduinetdiskdownload\video_20260929_113539.mp4`，本轮只分析播放器的 0～30 秒。原视频为 1920×1080、30 fps，总长约 77.17 秒；30 秒以后的内容不作为本次验收输入。

| 视频阶段 | 期望表现 |
| --- | --- |
| 开头尚未找到有效信号灯 | `UNKNOWN / WAIT`，不预先填红灯 |
| 找到信号灯后至颜色切换前 | `RED / STOP` |
| 25 秒 | 抽帧确认仍为红灯 |
| 26 秒 | 抽帧确认已经变绿；需要连续帧确认 |
| 26～30 秒 | `GREEN / GO_SIGNAL_ONLY`；前车在视频中启动 |

本轮判断依据是帧内颜色、亮度、紧凑灯点及周围暗色灯壳，并非播放到第 26 秒就强制切换。`traffic_redgreen_scene.json` 的 `evaluation_only` 时间只作人工验收参考，运行时不读取它决定灯色。第一版不判断车道、黄灯、违规、前车是否真正启动，也不输出物理车辆控制。`GO_SIGNAL_ONLY` 只表示绿灯提示，尚未检查行人、前车、车道和其他通行条件。

## 实现位置和判断来源

| 文件 | 用途 |
| --- | --- |
| `traffic_light.py` | 灯色分析、三帧确认、证据超时及红绿冲突处理 |
| `traffic_redgreen_scene.json` | 当前视频的上方信号灯搜索区域和确认参数 |
| `check_traffic_video.py` | 离线分析前 30 秒，模拟 RGB565 颜色量化，保存 CSV 和状态切换截图 |
| `preview_stream.py` | 在现有彩色同帧窗口中显示灯色、停车/通行提示和来源；`S` 增加 `_traffic.png` 截图 |

实时模式优先在当前帧的 YOLO `traffic light` 框中分析颜色，日志 `source=yolo`。该视频远处灯点很小，在 640×640 模型输入中可能漏检；显式加 `--traffic-roi-fallback` 后，允许在场景标定区域内分析灯点，日志 `source=scene_roi`。备用模式额外要求两个大小近似、横向对齐的灯点，排除开头树叶反光；这是当前双信号灯场景的配置，不能作为任意路口通用算法。

同一区域同时有红、绿证据时输出 `UNKNOWN / WAIT`。颜色变化需连续 3 个新帧确认；无颜色证据最多保留旧状态 500 ms，帧中断或重连也清空状态。窗口只支持 matched RGB565LE 图像，避免灰度图猜灯色或异步旧框与新图配对。

## 本地验证结果与限制

使用本视频 0～30 秒，每秒采样 10 帧，共 300 帧；每帧先模拟 RGB565 量化，再运行区域备用颜色分析：

| 结果 | 视频时间 |
| --- | --- |
| 首次稳定红灯 | 10.1 秒 |
| 红转绿确认中，暂为 UNKNOWN | 26.0 秒 |
| 首次稳定绿灯 | 26.2 秒 |
| 稳定状态数量 | UNKNOWN 103 帧，RED 159 帧，GREEN 38 帧 |
| 开头误报绿灯 | 0 帧 |
| 26.3～29.9 秒 | 均为 GREEN |

这是本机的视频颜色分析验证，没有运行 RK3568 NPU，不代表 YOLO 已在这些帧检出信号灯，也不是通用准确率。实际 HDMI 回放需再次验收灯框检出、颜色、窗口延迟和状态来源。不同检测/显示帧率会改变连续三帧确认的耗时。

## Ubuntu：同步文件

先在 Ubuntu 恢复共享目录，并确认 VMware 共享名为 `pcie_host`：

```bash
sudo mkdir -p /mnt/hgfs
if ! mountpoint -q /mnt/hgfs; then
    sudo vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other
fi
ls -ld /mnt/hgfs/pcie_host
ls -l /mnt/hgfs/pcie_host/{preview_stream.py,traffic_light.py,traffic_redgreen_scene.json,check_traffic_video.py,TRAFFIC.md,README.md}
```

如果上面的六个源文件全部不存在，先执行 `vmware-hgfsclient` 和 `ls -la /mnt/hgfs`，检查共享是否启用、名称是否改变。Windows VMware 的“虚拟机设置 → 选项 → 共享文件夹”中，应启用共享，名称设为 `pcie_host`，主机路径指向 `F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host`。若使用其他共享名，需相应修改 `/mnt/hgfs/pcie_host`。仅目录存在或 `mountpoint` 成功不代表共享内的源文件可用。

六个源文件都能列出后再复制。用 `&&` 保证复制失败时不会继续版本检查：

```bash
cd "$HOME/pcie_host"
cp -v /mnt/hgfs/pcie_host/{preview_stream.py,traffic_light.py,traffic_redgreen_scene.json,check_traffic_video.py,TRAFFIC.md,README.md} "$HOME/pcie_host/" && \
    python3 preview_stream.py --help | grep -E 'traffic-scene|traffic-roi-fallback'
```

必须能看到 `--traffic-scene` 和 `--traffic-roi-fallback` 才执行下面的启动命令。若出现 `unrecognized arguments`，说明仍运行旧的 `preview_stream.py`，先处理同步问题。

## HDMI 实时测试

终端 A 按 `COLOR.md` 第 3～4 节运行已经验证的 `pcie_rknn_probe_rgb565 --serve --preview-mode matched`，复用端口 9001。本轮仅增加 Ubuntu 端的分析和显示，无需重新编译 RKNN 程序、内核驱动或 FPGA 位流。

终端 B：

```bash
cd "$HOME/pcie_host"
python3 -u preview_stream.py \
    --host 127.0.0.1 --port 9001 \
    --require-detections --profile \
    --traffic-scene traffic_redgreen_scene.json \
    --traffic-roi-fallback \
    2>&1 | tee traffic_redgreen_preview.log
```

在 HDMI 源电脑上全屏播放本视频，从 0 秒开始，到 30 秒暂停或停止。搜索区域按视频充满 1920×1080 画面标定，播放器边框、窗口缩放或黑边会改变区域对应关系；必须用实际预览图核对。窗口不能从 PCIe 时间戳推算播放器从何时开始，因此该实时命令不会在 30 秒自动退出。

窗口应先显示 `UNKNOWN / WAIT`，找到有效灯色后显示 `RED / STOP`，约 26 秒变绿并确认后显示 `GREEN / GO_SIGNAL_ONLY`。`S` 保存当前原图、检测框图和 `_traffic.png` 状态图。下面命令在退出预览后筛选灯色日志：

```bash
grep 'Traffic signal:' traffic_redgreen_preview.log
```

只想确认 YOLO 框内的灯色时，去掉 `--traffic-roi-fallback`。若一直 UNKNOWN，请同时查看原始 `traffic light` 检测框和 `source`，不能把备用区域检测的结果当作 YOLO 检测成功。

## 可选：离线回放

把原视频复制到 Ubuntu 后执行，将路径换成实际视频路径：

```bash
python3 check_traffic_video.py /实际路径/video_20260929_113539.mp4 \
    --scene traffic_redgreen_scene.json \
    --seconds 30 --sample-fps 10 \
    --output-dir "$HOME/pcie_host/traffic_check_$(date +%Y%m%d_%H%M%S)"
```

加 `--headless` 可以只生成 CSV 和状态切换截图。离线回放按场景区域寻找颜色，不调用 RKNN，也不需要连接板子。CSV 中 `video_seconds` 是原视频时间；HDMI 日志的 `seq` 是板端采集序号，两者不能直接互换。

下一步实机颜色判断通过后，再把这个模块的状态接口移到 RK3568 侧，并增加 FPGA LED 或 HDMI 动作叠加。
