# 车道稳定化 A1

## 目标

A1 用于验证车道结果跳变是否主要来自短暂的异步延迟、单帧样式未知和过短保持时间。模型、RKNN 输入输出契约以及 `scene_v4` 的视频协议保持不变。

## 行为

- 已确认的边界保持时间从 350ms 调整为 550ms。
- 同一几何边界出现一次 `UNKNOWN` 时，预览继续显示上一条已确认类型，并标记 `HOLD`；当前几何线仍以本帧可见边界为准。
- `HOLD` 结果的 `decision_type` 为 `UNKNOWN`，因此不会被变道判断当作可用的虚线证据。
- 连续第二次未知、边界几何移动、时钟回退、输入间隔超过500ms或总结果超过750ms时，旧确认清除。
- 左右边界独立处理；一侧未知不会清除另一侧。

## Ubuntu 构建

在共享目录可用且 RKNN 头文件存在时执行。这个脚本会把 A1 源码复制到 Ubuntu 构建目录，运行全部测试，并将场景程序部署到板端：

```bash
cd /mnt/hgfs/pcie_host
sudo -v && bash integrate_hdmi_scene_rk3568.sh
```

末尾应看到 `SCENE_INTEGRATE_PASS`。如果只想在 Ubuntu 编译而不部署，可使用：

```bash
LANE_BUILD_DIR=/mnt/hgfs/pcie_host/rk3568_scene_deploy \
RKNN_HEADER_DIR=/mnt/hgfs/pcie_host/rk3568_lane_deploy \
LANE_SERVER_NAME=pcie_multivideo_server.scene_v4 bash build_hdmi_lane.sh
```

最后启动预览：

```bash
bash preview_hdmi_lane_fast.sh
```

## 验收

使用同一段之前效果较好的 HDMI 视频，连续播放约30秒，比较以下项目：

| 项目 | A1 预期 |
|---|---|
| 单个未知结果 | 不立即清除已确认类型，状态显示 `HOLD`；若本帧无几何边界，线图层仍可暂时消失 |
| 连续未知或边界移动 | 清除旧线型并显示待确认/未知 |
| 变道请求 | `HOLD` 不得触发虚线许可 |
| 清晰直线路段 | `SOLID`/`DASHED` 不应连续来回跳变 |
| 路口、左转、遮挡 | 允许进入 `UNKNOWN`，恢复直线后重新确认 |
| 过期结果 | 超过750ms仍显示过期，不继续沿用旧线型 |

运行期间如需查看原因和耗时：

```bash
sudo -v && bash deploy_hdmi_lane_rk3568.sh log
```
