# HDMI 前视辅助驾驶（front_v1）

该 profile 与三路 `scene_sync_v5`/`drive_v6` 独立保留。它只从 PCIe 设备读取 HDMI 正前方帧，车道和目标模型仍在 RK3568 上异步运行；OV5640、以太网、Windows 双路同步播放器和 `sync_control_relay.py` 不参与单路判断。

## 构建与部署

在 Ubuntu 共享目录中执行：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_front_assist_rk3568.sh prepare
sudo -v && bash /mnt/hgfs/pcie_host/deploy_front_assist_rk3568.sh install
sudo -v && bash /mnt/hgfs/pcie_host/deploy_front_assist_rk3568.sh start
```

成功标志包含 `FRONT_BOARD_START_PASS` 和 `SYNC_START_PASS`。预览使用：

```bash
bash /mnt/hgfs/pcie_host/preview_front_assist.sh
```

控制页面为 `http://127.0.0.1:9011`。点击开始后直接进入 HDMI 检测，不等待 `PAIR`，也不下发 Windows 播放命令。

## 规则

- 实线边界：请求对应侧变道时显示 `SOLID_BLOCK` 并报警。
- 虚线边界：显示 `DASHED_REVIEW`，仅提示人工复核，不输出“安全放行”。
- 车道或目标结果过期：显示 `LANE_STALE`/`HDMI_STALE`，清除旧判断。
- 前方车辆、行人和信号灯作为辅助证据显示；单路模式不使用侧方车辆或跨路时间差。

三路演示仍使用原有 `deploy_sync_control_rk3568.sh`/`deploy_drive_assist_rk3568.sh` 入口，不会被 front_v1 覆盖。
