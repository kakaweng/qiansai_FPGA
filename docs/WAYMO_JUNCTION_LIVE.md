# 固定 Waymo 路口多路演示

本轮仅修改 RK3568/Ubuntu/Windows 软件。继续使用已经亮灯成功的 FPGA 位流；不综合、不下载位流，不改变 KEY1～4、KEY5～7 或 LED 的定义。

## 输入与处理

固定目录：`F:\waymo_data\decoded_perception\perception_11987368976578218644_1340_000_1360_000`。

| 输入 | 素材 | 板端处理 |
| --- | --- | --- |
| HDMI | `front.mp4` | YOLO 车辆/行人，指定主灯区域的实时颜色判断 |
| 以太网 | `left.mp4`（原始 FRONT_LEFT 视角） | YOLO 车辆/行人，黄色横道附近行人确认 |
| OV5640 | 摄像头当前画面 | 仅采集、拼接、显示 |

RK3568 使用一个既有 YOLO RKNN 实例轮流处理前视和左视，每路只保留最新待处理帧。视频采集、拼接不等待推理。灯色、横道接触、确认/保持、通行/礼让/违规规则和模拟车速由 RK3568 计算；Ubuntu 接收原三路拼接和独立状态，绘制检测框、短时轨迹与 2.5D，并经原反馈服务驱动 FPGA LED。

本模式聚焦红灯与行人闭环，不加载车道分割、Mapillary 或 PIE 模型。原单路辅助驾驶及纯三路传输入口保留。2.5D 使用路口模板，目标位置和未来约 1.2 秒箭头是图像跟踪的示意估计；跨视角身份、米制位置和真实未来轨迹未标定。界面持续标注“模板 / 位置含估计”。

主灯 ROI 和黄色横道规则只针对该片段；没有按“第几秒”切换检测结论。前/左播放器共用源时间轴，但 HDMI/以太网的物理到达延迟仍各自存在；本模式不声称完成硬件同步标定。

## Windows：固定素材播放器

关闭原以太网发送程序，避免两个程序同时发送。PowerShell 执行：

```powershell
Set-Location 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host'
.\run_waymo_junction_source.cmd --monitor 0 --bind 192.168.1.102
```

`--monitor` 选择连接 FPGA HDMI 输入的 Windows 显示器，保持其输出为 1920×1080。如物理显示器编号不同，用 `run_sync_hdmi_ethernet.cmd --list-monitors` 查看并替换。`--bind` 是连接 FPGA 的 Windows 网卡地址，沿用现有 192.168.1.102；目标默认 192.168.1.10:5002。

脚本自动加载 front/left 和时间戳，不弹文件选择窗口。启动暂停在首帧；暂停时每 250ms 重发同一静止画面，媒体时间不前进，以便模型预热和操作演示。显示的是正在送入 FPGA 的暂停画面，不代表道路仍在运动。结束后可点“回到开始”重新演示。

## Ubuntu：启动与预览

若共享目录已经存在，直接从第二段开始。若虚拟机重启后共享目录消失：

```bash
sudo -v
sudo mkdir -p /mnt/hgfs
if ! mountpoint -q /mnt/hgfs; then
    sudo vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other
fi
ls /mnt/hgfs/pcie_host/deploy_three_video_rk3568.sh
```

终端一：

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
export FPGA_BDF="0002:21:00.0"
sudo -v && bash /mnt/hgfs/pcie_host/deploy_three_video_rk3568.sh start-junction
```

脚本检查预览依赖、运行核心及异步队列测试、编译新服务、上传并回读校验，先加载既有 PCIe 视频驱动并确认设备启用，再检查/重启原按键与 LED 软件服务，最后复用板上既有 `road_objects.rknn` 和 contract 切换视频服务。只复用已有位流能力，不包含任何 FPGA 下载操作。

成功标志：`JUNCTION_CORE_PASS`、`JUNCTION_ASYNC_PASS`、`JUNCTION_BUILD_PASS`、`JUNCTION_BOARD_PREPARE_PASS`、`JUNCTION_INSPECT_PASS`、`JUNCTION_BOARD_START_PASS`、`JUNCTION_START_PASS`。

重启后应先出现 `JUNCTION_PCIE_STATE ... enable=1 ... version=1.3`（enable 也可能大于 1），然后才出现按键/LED接口检查。旧脚本可能先检查按键，因设备未启用报 `FPGA_KEY_CAPABILITY_ABSENT`；该报错本身不能证明位流错误。更新脚本已修正此顺序，并取消回读临时文件的删除确认。如果仍无法读取按键，新的 `FPGA_KEY_CAPABILITY_READ` 会给出实际 magic、ABI 与 LED 标识，供区分 PCIe 读取问题和接口不匹配。

终端二：

```bash
bash /mnt/hgfs/pcie_host/preview_junction_live.sh
```

本模式无需 `sync_control_relay.py` 或网页控制面板。按键/控制按钮在统一预览中工作。视频捕获和推理在预览连接后开始，因此首帧红灯应保持到窗口出现检测结果。

## 建议演示顺序

| 步骤 | 操作与可见反馈 |
| --- | --- |
| 1. 首帧预热 | Windows 暂停在开头，Ubuntu 打开预览；等待前/左识别年龄正常、状态“红灯停车” |
| 2. 红灯违规 | 预览按 `F`/“强制启动”，模拟车辆短时移动，界面报警，原三业务灯快闪；真实视频不会被此按钮加速或移动 |
| 3. 恢复 | 按 `B` 停车；旧 LED 服务保留报警最短保持时间，约两秒后恢复停车灯效 |
| 4. 动态路口 | 按 `R` 重置模拟，选择左转 `KEY5`（或屏幕“左转”），Windows 播放从头开始 |
| 5. 行人礼让 | 片段中段左视横穿行人高亮，2.5D 左侧横道显示行人与短时轨迹；模拟车停下，LED 双闪；持续清空后恢复通行 |
| 6. 重播 | Windows 回到开始并暂停；预览按 `R`，重新等待识别稳定 |

`KEY5/6/7` 分别左转/直行/右转；屏幕也有相同按钮。`G` 恢复自动通行请求，`F` 强制模拟启动，`B` 模拟停车，`R` 仅重置模拟状态，`Esc/Q` 退出。这个专用红灯闭环模式三条模拟路线均接受同一主灯约束，不改原单路模式的右转规则。

| 业务状态 | 复用原 LED 效果 |
| --- | --- |
| 可模拟通行 | 所选方向灯常亮 |
| 红灯/模拟停车 | 三灯常亮 |
| 礼让行人 | 三灯双闪 |
| 红灯或行人冲突时强制启动 | 三灯快闪 |
| 双路结果过期/连接中断 | 三灯慢闪，撤销旧通行状态 |

横道行人需要连续证据确认；短时漏检保持最多 1.6 秒，持续清空约 0.9 秒且至少三次观测后解除。任一路感知帧年龄超过 1.8 秒转为未知。处于未知时强制启动被拒绝。

## 日志和回退

保持预览运行，在另一终端读取：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_three_video_rk3568.sh junction-log
```

日志应同时出现 `junction_live source=HDMI_FRONT` 与 `source=ETH_LEFT`，不会出现 OV5640 推理。`inference_ms` 是单次处理耗时；预览 FPS 才是收到的视频帧率。

关闭专用预览后恢复原纯三路低延迟传输：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_three_video_rk3568.sh start
bash /mnt/hgfs/pcie_host/preview_three_video.sh
```

## 验证范围

Windows 已完成 ARM64 编译检查、C 核心规则、Python 预览/跟踪、上传回读/错误标志的模拟部署、原三路/按键/LED回归及播放器隐藏窗口测试。完整 198 帧素材已生成 `rk3568_junction_live_deploy/review/software_review.mp4`、截图和 `report.json`；用 Windows ONNX 参考检测配合实际 C 规则确认红灯、绿灯、行人礼让与红灯强制启动报警。参考 ONNX 与板上既有 INT8 RKNN 并非已验证等价，不能据此声称板端准确率或帧率。

Linux 异步线程测试已交叉编译；真实运行由 Ubuntu 启动脚本在切换板端服务前完成。RK3568 双路实时速度、FPGA 灯效和 HDMI/网络物理链路仍需上述实机启动验证。
