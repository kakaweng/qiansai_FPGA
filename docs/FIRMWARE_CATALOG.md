# 单路辅助驾驶与三路输入的版本入口

以下路径以 `F:\B_file\jichuangsai\Pango_FPGA` 为工程根目录。2026-11-02已重新核对原版文件长度和SHA256。

| 用途 | 文件 | 核对结果 |
| --- | --- | --- |
| 原三路FPGA固件 | `06_hdmi_loop_1/hdmi_loop.ethernet_video_v1.sbit` | 2,101,696字节；SHA256 `58e2c6b17e13a282ffb67b8908568088ce78aa04bf16b49f1bb070f670774626` |
| 原三路固件的第二份留存 | `dual_camera_baseline_20261025/hdmi_loop.ethernet_video_v1.sbit` | 与上方逐文件SHA256一致；虽然目录名含dual，文件实际是以太网第三路版 |
| 原三路板端程序 | `09_pcie_test/pcie_host/pcie_multivideo_server.ethernet_v6` | SHA256 `d28439094128866ac04273379fddc9ac1c817c1c9d3426f07d25b01787dd99a8`；版本 `multivideo-ethernet-v6`，AArch64静态程序 |
| 当前路口方向键固件 | `06_hdmi_loop_1/hdmi_loop.junction_keys_v2.sbit` | 原KEY1～4视频/暗光功能保留，新增KEY5/6/7方向选择，三路采集仍保留；校验值见 `JUNCTION_KEYS_BUILD_V2.md` |
| 新增辅助驾驶灯反馈固件（联调版） | `06_hdmi_loop_1/hdmi_loop.drive_feedback_v1.sbit` | 保留v2按键与三路采集，新增A2/B3/C5三业务灯及LF01回写通道；2,101,696字节；SHA256 `837014ba23d44673ab957ce5e6fa2ddc2ba57e7971bea23eedc12ae3983752fa`。时序未收敛，未实机验收；见 `DRIVE_FEEDBACK_BUILD.md` |
| 新增暗光增强v2固件（联调版） | `06_hdmi_loop_1/hdmi_loop.lowlight_v2.sbit` | 保留方向键、LF01反馈及三路采集；新增保边降噪、平滑提亮与LOW 1/2/3档位；2,101,696字节；SHA256 `2fb62110b6aaf5229c02ded9215aed87fddf8b79fd90d5cd65398aef0ed8bda5`。HDMI像素域同域setup/hold通过，整体仍有其他域/跨域违例，实机待确认；见 [LOWLIGHT_V2.md](../../06_hdmi_loop_1/LOWLIGHT_V2.md)及[构建记录](../../06_hdmi_loop_1/LOWLIGHT_V2_BUILD.md) |
| 已作废方向键v1 | `06_hdmi_loop_1/hdmi_loop.junction_keys_v1.sbit` | 占用了原KEY1～3，禁止用于本轮验收；为追溯保留文件，改用v2 |
| 当前单路检测/2.5D预览 | `09_pcie_test/pcie_host/preview_pie_anticipation.sh` | 保留现有冻结预演版本；RK使用已安装前视服务 |
| 新增方向键预览入口 | `09_pcie_test/pcie_host/preview_front_fpga_keys.sh` | 在当前单路界面上启用独立硬件按键通道 |
| 新增按键与灯光反馈预览入口 | `09_pcie_test/pcie_host/preview_front_feedback.sh` | 在同一单路界面后台回传状态，需部署 `deploy_fpga_feedback.sh`；见 [FPGA_DRIVE_FEEDBACK.md](FPGA_DRIVE_FEEDBACK.md) |

**原三路固件可直接在PDS下载器中选择这个文件：**

```text
F:\B_file\jichuangsai\Pango_FPGA\06_hdmi_loop_1\hdmi_loop.ethernet_video_v1.sbit
```

它包含HDMI、OV5640、以太网三路输入，配合RK程序 `--source all` 输出三路拼接。之前“前视单路识别”和“三路展示”的区别主要在RK采集与软件服务模式；原三路位流也能供当前服务只读HDMI。新增方向键位流继续保留三路采集，切换展示模式并不必然需要反复下载FPGA。

## 找回原三路预览

2026-11-02新增更适合演示的独立入口：`deploy_three_video_rk3568.sh start` + `preview_three_video.sh`。仍使用下方保留的ethernet_v6，不加载模型，保留完整校验，降低预览转换与重复绘制开销；完整操作与恢复当前单路见 [THREE_VIDEO_DISPLAY.md](THREE_VIDEO_DISPLAY.md)。下方命令为历史原始入口。

完整恢复驱动、发送视频、上传原服务及启动命令在 [ETHERNET_VIDEO.md](ETHERNET_VIDEO.md)；实际位流构建记录在 [ETHERNET_VIDEO_BUILD.md](../../06_hdmi_loop_1/ETHERNET_VIDEO_BUILD.md)。如果需要之前带同步面板、三路识别的后续版本，入口为 `deploy_sync_control_rk3568.sh` / `deploy_drive_assist_rk3568.sh`，与“纯三路并行流展示”的ethernet_v6不同。

已有1.3驱动、原三路程序已上传且三路输入已建立时，原展示服务的核心启动命令如下。先关闭前视预览，避免两个客户端同时连接9002：

```bash
export HDC="$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc"
export TARGET="7001005458323933328a26cf4db23900"
export FPGA_BDF="0002:21:00.0"
sudo -v
sudo "$HDC" -t "$TARGET" shell sh /data/local/tmp/stop_multivideo_server.sh
sudo "$HDC" -t "$TARGET" shell \
  /data/local/tmp/pcie_multivideo_server.ethernet_v6 \
  --device "/dev/pango_pcie_multivideo_$FPGA_BDF" \
  --source all --overlap-verify --serve --port 9002 \
  --send-timeout-ms 30000 --profile --daemon \
  --log /data/local/tmp/three_video_baseline.log
```

已有 `tcp:9004 tcp:9002` 转发时复用；否则按ETHERNET_VIDEO.md检查端口后建立转发。第二个Ubuntu终端：

```bash
python3 /mnt/hgfs/pcie_host/preview_multivideo.py --host 127.0.0.1 --port 9004 --timeout 60
```

三路窗口为左侧HDMI、右上OV5640、右下以太网。未收到以太网完整帧时右下黑窗属于输入未就绪，不需要更换模型。回到当前单路时启动 `SYNC_PROFILE=front_pie bash /mnt/hgfs/pcie_host/deploy_sync_control_rk3568.sh start`，再打开单路预览入口。

原版位流历史构建报告存在未收敛的时序约束，详见原构建记录。此次“找回”是文件及版本核对，没有重新验收原版硬件性能。
