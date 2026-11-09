# qiansai_FPGA

RK3568 × 紫光同创 FPGA 辅助驾驶赛题验证工程（PCIe 高速传输链路）

## 项目简介

本工程完成 FPGA 与 RK3568 之间的 PCIe 高速数据通路搭建，并在此基础上实现多路视频采集、车道线检测、红绿灯识别、行人礼让与辅助驾驶决策等赛题功能。FPGA 侧负责多路输入采集、图像增强和 PCIe 上传，RK3568 侧运行 RKNN 模型做目标识别与驾驶规则判断，上位机负责预览与控制。

## 系统架构

```
FPGA（紫光同创）
  HDMI / OV5640 / 以太网  三路采集
  → 图像处理 / 暗光增强 / 按键与灯光反馈
  → PCIe 上传
        ↓ PCIe（BAR0/BAR1 + DMA）
RK3568（PCIe 主机侧）
  PCIe 驱动 → RKNN 识别（YOLOv5s / 车道 / 行人）
  → 驾驶决策（红绿灯 / 变道 / 礼让）→ TCP 服务
        ↓ 网络
上位机（Windows / Ubuntu 预览与控制）
```

## 目录结构

```
qiansai_FPGA/
├── fpga/                     # Pango PDS FPGA 工程
│   ├── src/                  # Verilog 源码（camera / eth / pcie / vip / ddr）
│   ├── ipcore/               # IP 核（PCIe、DDR3、FIFO、PLL 等）
│   ├── generate_bitstream/   # 当前位流输出
│   └── *.sbit                # 各版本固化位流（见下表）
├── host/                     # RK3568 板端 + 上位机代码
│   ├── pcie/                 # PCIe 驱动测试、多视频服务器、RKNN 探针
│   ├── lane/                 # 车道线检测核心
│   ├── junction/             # 路口、行人、目标检测
│   ├── front/                # 前视与驾驶辅助
│   ├── sync/                 # 多源同步与录像控制
│   ├── fpga/                 # FPGA 按键 / 灯光反馈
│   ├── common/               # 帧拼接、校验、调度
│   ├── preview/              # 上位机预览与决策演示（Python + 启动脚本）
│   ├── config/               # 模型清单、标签、决策场景配置
│   └── scripts/              # 构建、部署、运行脚本
└── docs/                     # 设计文档与分阶段验证记录（57 篇）
    └── demo/                 # 演示素材索引（index.html + manifest）
```

## FPGA 位流版本

| 位流 | 说明 |
| --- | --- |
| `hdmi_loop.ethernet_video_v1.sbit` | 三路采集基础版（HDMI + OV5640 + 以太网） |
| `hdmi_loop.junction_keys_v2.sbit` | 路口方向键 KEY5/6/7（左转/直行/右转），保留三路采集 |
| `hdmi_loop.drive_feedback_v1.sbit` | 辅助驾驶实体灯反馈（模拟通行 / 停车 / 礼让 / 违规报警） |
| `hdmi_loop.lowlight_v2.sbit` | 暗光增强 v2（保边降噪、增益、高光保护，三档强度） |

其余位流为历史版本，保留用于追溯与回退。

## 板端快速开始

```bash
# 1. 下载位流：PDS 下载器中选择对应 .sbit
# 2. 部署板端程序（脚本在 Ubuntu 侧执行，经 hdc 部署到 RK3568）
sudo -v && bash host/scripts/deploy_three_video_rk3568.sh start    # 三路纯传输演示
sudo -v && bash host/scripts/deploy_sync_control_rk3568.sh start   # 单路识别 + 同步
# 3. 板端运行 / 停止
bash host/scripts/run_front_pie_board.sh
bash host/scripts/stop_rknn_server.sh
```

## 上位机预览

预览与决策演示程序在 `host/preview/`：

```bash
# 单路识别 + 冻结路口预演（Windows / Ubuntu）
bash host/preview/preview_pie_anticipation.sh

# 三路视频预览
bash host/preview/preview_three_video.sh

# 辅助驾驶统一预览
bash host/preview/preview_drive_assist.sh
```

Python 程序直接调用：`python3 host/preview/preview_front_p1.py`、`preview_multivideo.py`、`driving_decision_demo.py` 等，参数见对应脚本头部说明。

## 文档索引

`docs/` 下按阶段记录了设计、部署与验证过程，主要包括：

- `README.md`（原工程完整说明，122KB，含全部阶段变更记录）
- `DECISION.md`、`LANE.md`、`MULTIVIDEO.md`、`RKNN.md`、`TRAFFIC.md`、`TIMING.md` —— 决策、车道、多视频、模型、交通、时序设计
- `*_RK3568.md` —— 各功能板端部署与验收说明
- `COMPETITION_DEMO_PLAN.md`、`THREE_VIDEO_DISPLAY.md` —— 赛题演示规划与操作
- `PIE_*`、`CROSSWALK_*`、`JUNCTION_*`、`FROZEN_JUNCTION_PREVIEW.md` —— 行人、横道、路口与冻结预演
- `FIRMWARE_CATALOG.md` —— 固件清单与 SHA256 校验

## 功能清单

- 红绿灯识别：红灯停、绿灯行，支持箭头灯
- 车道线检测：虚线变道请求、实线违规报警
- 行人礼让：横穿检测、路口方向选择、三秒预演
- 多路输入：HDMI / OV5640 / 以太网三路并行采集与低延迟传输
- FPGA 图像增强：暗光保边降噪，三档增强
- 辅助驾驶反馈：实体 LED 灯指示通行 / 停车 / 礼让 / 报警

## 演示素材

`docs/demo/` 提供演示素材索引（9 组 11 个视频的清单、来源与 SHA256）。视频本体约 296MB，不随仓库托管，可从工程所在地的 `demo_cases_20261102/` 目录获取。

## 验证状态

- 本地回归与交叉编译已通过；板端实机帧率、总延迟与实体灯反馈待最终验收。
- 识别模型为 YOLOv5s（COCO 80 类），板端运行于 RKNN-Toolkit2 运行时。
