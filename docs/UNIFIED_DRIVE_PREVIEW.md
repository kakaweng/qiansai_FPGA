# 同窗驾驶辅助预览与更换视频源

2026-10-28：在现有OpenCV预览中加入中文驾驶状态、按钮和请求反馈。上方保留1280×480三路识别拼接，下方为中文辅助驾驶区，整体1280×820；窗口可缩放。无需同时观看浏览器控制面板，不改模型和RK3568状态机。

三路识别接入后的车道跳变另有板端调度修正，需重跑`integrate_drive_assist_rk3568.sh`才能应用，详见[LANE_SCHEDULING_FIX.md](LANE_SCHEDULING_FIX.md)。本页预览入口只更新Ubuntu显示代码。最新显示代码每100ms在后台拉取状态，车道证据超过750ms显示“已过期”，有效证据尚不能定型时显示“待确认”。

## 启动同窗预览

第六版板端服务和Ubuntu控制转发9011已运行时，关闭旧预览，执行：

```bash
bash /mnt/hgfs/pcie_host/preview_drive_assist.sh
```

该入口自动将最新显示代码和配套中文字体同步到Ubuntu本地工作目录，沿用已经验证的C/FNV-1a校验库。只更新Ubuntu显示端，无需重新构建模型或重新切换板端服务。中继终端`sync_control_relay.py`仍需保持运行，用于将RK3568的准备/播放/停止指令转给Windows；浏览器可以关闭。

| 同窗内容 | 说明 |
|---|---|
| 三路识别画面 | 原有前方HDMI、左侧OV5640、右侧以太网的板端识别框和标线 |
| 中文驾驶状态 | 左右实/虚线、灯色、模拟车速/挡位、当前转向意图 |
| 请求反馈 | 板端给出的具体原因，实线请求显示红色报警区域 |
| 数据年龄 | 三路检测年龄和车道年龄；控制或视频失效时不继续显示有效判断 |
| 按钮/快捷键 | 都调用RK3568的原有HTTP接口，Ubuntu不重新执行驾驶规则 |

| 操作 | 快捷键 |
|---|---|
| 开始检测/从头重播 | 空格 |
| 停止检测 | X |
| 左/右变道请求 | A / D |
| 取消请求 | K |
| 模拟制动 | B |
| 模拟减速/加速 | − / +（也可用等号键） |
| 模拟P/N/D/R挡 | P / N / 1 / 2 |
| 保存包含驾驶状态的整窗截图 | S |
| 退出预览 | Q / Esc |

快捷键使用前先点击预览窗口取得焦点。各按钮均可用鼠标点击，D字母仍表示向右变道，数字1表示模拟D挡，避免冲突。

视频、板端状态分别在后台接收；HTTP超时不阻塞显示线程。状态展示仍是独立更新，不承诺判断和当前视频逐帧严格对应，各项保留结果年龄。视频超过1秒无新帧会变暗并标记STALE；控制状态超过1秒无刷新，或感知结果超过750ms，不再展示旧的有效线型、绿灯或变道反馈。底层决策仍由板端处理，原先未标定、左侧手动未同步等限制继续有效。

如仅需原来的纯视频窗口，继续使用`preview_hdmi_lane_fast.sh`。底层预览也支持显式参数`--drive-panel --control-url http://127.0.0.1:9011`；中文字体可用`--panel-font /路径/字体.ttf`覆盖。

## Windows更换视频源

1. 在统一预览中点“停止检测”或按X。
2. 关闭旧的Windows受控播放器及其全屏窗口，避免重复占用控制端口、重复发送以太网视频。
3. 运行以下任意一种命令，等Windows显示`SOURCE_CONTROL_READY`。
4. 在统一预览里点“开始/重播”，新一轮播放使用新的session，清理旧识别和旧请求。Ubuntu中继会读取更新后的连接配置，不需要修改令牌。

**弹窗选择视频**（先正前方，再右侧）：

```powershell
Set-Location 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host'
.\run_controlled_source.cmd --monitor 1 --bind 192.168.1.102
```

**直接指定文件**（将两处示例路径替换为实际文件）：

```powershell
Set-Location 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host'
.\run_controlled_source.cmd --monitor 1 --bind 192.168.1.102 --front 'D:\视频\正前方.mp4' --right 'D:\视频\右侧.mp4'
```

**指定已经整理好的场景目录**：

```powershell
.\run_controlled_source.cmd --monitor 1 --bind 192.168.1.102 --scene 'F:\waymo_data\decoded_perception\perception_10980133015080705026_780_000_800_000'
```

场景目录须含`front.mp4`和`side_right.mp4`，可选`frame_timestamps.csv`；普通文件无需按这两个名字重命名，使用`--front/--right`即可。

以上显示输出编号1是示例，应沿用当前连接FPGA且已经正确工作的编号；不确定时运行：

```powershell
.\run_sync_hdmi_ethernet.cmd --list-monitors
```

`--front`是HDMI正前方，`--right`是以太网右侧；OV5640左侧仍由手机给摄像头播放并手动对齐，不在这两个参数中指定。更换数据集不会要求重训或重新部署模型，但两路素材应来自同一行驶场景；任意不相关视频仅能测试链路，不能证明侧方联合判断有效。

## 验证范围

8项本地测试覆盖真实异步HTTP收发、操作路径与控制头、板端拒绝原因展示、断线恢复、结果年龄、旧状态失效、所有按钮坐标和快捷键、图像拼接保留原视频像素、同一文字刷新周期内的失效重绘及中文渲染；统一客户端到真实C控制器的本地联调也已通过。渲染验证使用合成视频区域，不能作为RK3568实机帧率或检测准确率证明。布局示意图位于`rk3568_drive_deploy/unified_preview_layout.png`，实际Ubuntu窗口显示与帧率仍需验收。
