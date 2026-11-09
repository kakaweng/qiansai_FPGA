# 辅助驾驶实体指示灯

完整灯光定义、寄存器、构建、上板步骤和验证范围见 [DRIVE_FEEDBACK_LEDS.md](../../06_hdmi_loop_1/DRIVE_FEEDBACK_LEDS.md)。

| 入口 | 用途 |
| --- | --- |
| `build_fpga_feedback.ps1` | Windows编译并测试协议，生成RK3568静态反馈适配程序及SHA256清单 |
| `deploy_fpga_feedback.sh` | Ubuntu安装独立适配服务，核对FPGA LF01能力，建立9017→9015转发 |
| `test_fpga_feedback_lights.py` | 上板逐灯测试，不读取视频或模拟识别结果 |
| `preview_front_feedback.sh` | 在当前HDMI单路界面上同时启用原方向键与灯光反馈 |
| `fpga_drive_feedback.py` | 读取当前界面已归一化判断，5Hz后台发送，日志标注实时/模拟 |

本次没有更改模型、行人/车道阈值或原界面布局。需要先下载新 `hdmi_loop.drive_feedback_v1.sbit` 并恢复原板端服务；单独更新Python不能让旧位流具备回写点灯能力。
