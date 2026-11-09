# PIE v2 行人条件分数：RK3568 HDMI 单路测试

2026-11-02：路口 M 视图也已加入 PIE 接口状态/有效分数，与“当前行人行为”分开显示。当前横穿来自带背景补偿的连续运动规则，PIE 仍是下述提前预测模型；采样和动作阶段限制保持。可仅更新预览，说明及实测对照见 [PEDESTRIAN_BEHAVIOR_PREVIEW.md](PEDESTRIAN_BEHAVIOR_PREVIEW.md)。

2026-11-01。已把 Windows 训练选中的 `pie_anticipation_v2_20261101` 几何分类器导出为纯 C 参数与计算模块，并接入独立的 RK3568 控制器。部署入口、原模型一致性自测、实时 HTTP 状态及现有深色预览中的实验分数卡均已完成。

同日最新修正：已移除E右转规则选择，改为自动行人反馈；M路口视图使用实际边界、分割斑马线及行人对应关系。接入新斑马线模型时使用 `integrate_observed_junction_rk3568.sh`，继续沿用本文的预览入口和PIE分数。操作及验证边界见 [OBSERVED_JUNCTION_RK3568.md](OBSERVED_JUNCTION_RK3568.md)。

**本地 C 数值、通信及界面回归通过，ARM64 严格交叉编译通过；尚未在用户的 RK3568 上完成运行验收。** 当前 Ubuntu 的 SSH 不接受免密登录，Windows HDC 未列出板卡，故部署需要从已连接 RK3568 的 Ubuntu 终端执行。文末的通过标记必须来自实际运行，不能由本地测试代替。

## 本轮接入内容

| 部分 | 执行位置与行为 |
| --- | --- |
| 原车道、YOLO 与灯色 | 继续使用 HDMI 单路 `front_p1` 服务及原 RKNN 权重 |
| 行人框关联与历史 | RK3568 C 控制器，使用 FWM1 里原始目标框、对象自身序号与源采样时间 |
| v2 分数计算 | RK3568 CPU；直接执行导出模型的归一化、36 维统计、线性头及累计激活 |
| Ubuntu | 接收状态与画面，绘制已有实时 2.5D 和左下实验卡片，不执行该模型推理 |
| 新模型版本 | `pie_anticipation_v2_20261101`，geometry，第 96 轮；FP32，无新增量化 |
| 预测输出 | 未来 0.6 秒／1 秒首次官方横穿的未校准分数，顺序与 ONNX 一致 |
| 原版回退 | 旧 `rk_front_p1_control` 保留，新控制器为 `rk_front_pie_control` |

该模型只有小型几何分类头，直接 C 执行可以避开新装 ONNX Runtime 或重新转换 RKNN；这与原 FP32 模型做同一计算。本轮没有接入新的斑马线分割模型，也没有把 Windows 冻结预演的整个离线分析管线搬到 RK3568。

**线上目前还没有可靠的“历史尚未横穿”动作门控。** 所以接口始终为 `eligibility:unverified`、`no_control:true`，界面明确显示动作阶段未确认。分数用于检查板端模型链路和历史采样；不能解释成已经确认的提前横穿预警，不触发变道、起步或停车。

## 首次部署

Windows 中继续按之前方式播放正前方 HDMI 视频，全屏并隐藏播放器控制栏。本轮不需要三路同步播放器，也不处理左右辅助源。

先关闭原预览窗口，在 Ubuntu 终端恢复共享目录：

```bash
sudo -v
sudo mkdir -p /mnt/hgfs
if ! mountpoint -q /mnt/hgfs; then
    sudo vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other
fi
ls -l /mnt/hgfs/pcie_host/integrate_pie_anticipation_rk3568.sh
```

如果最后一条提示不存在，在 VMware“虚拟机设置 → 选项 → 共享文件夹”中启用名称为 `pcie_host` 的共享，主机路径为 `F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host`。恢复文件后再继续。

将 RK3568 USB 设备连接给 Ubuntu 虚拟机，FPGA 保持原先已经验证的位流，然后执行：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/integrate_pie_anticipation_rk3568.sh
```

该入口自动寻找既有 Linux HDC，检查指定 RK3568、按需加载原 `pango_pcie_multivideo.ko`、核对 FPGA 设备，验证导出模型哈希，再执行测试、编译、安装和服务切换。默认 TARGET 为 `7001005458323933328a26cf4db23900`，默认 BDF 为 `0002:21:00.0`；换设备时沿用 `HDC`、`TARGET`、`FPGA_BDF` 环境变量。不会下载依赖或重新训练／转换原模型。

| 过程中应出现的标记 | 实际证明的内容 |
| --- | --- |
| `PIE_DEVICE_READY` | HDC 能访问板端，原驱动与 FPGA 字符设备就绪 |
| `PIE_C_EXPORT_VERIFY_PASS` | 导出参数、ONNX、checkpoint 及合同哈希一致 |
| `PIE_ANTICIPATION_CORE_PASS` | Ubuntu 原生 C 输入与时序回归通过 |
| `PIE_C_PARITY_PASS ... cases=64` | 64 个 ONNX 参考案例数值一致；Ubuntu 原生运行一次，板端 inspect 再运行一次 |
| `LANE_INSPECT_PASS` | 原车道／目标 RKNN 初始化检查通过 |
| `FRONT_BOARD_START_PASS` | 板端视频服务启动 |
| `PIE_BOARD_API_PASS` | 经 HDC 转发读到新版模型合同与实验字段；可在尚未开始检测时出现，不表示已有有效历史 |
| **`PIE_INTEGRATE_PASS`** | 本次整合脚本全部执行完成 |

## 打开预览并观察

另开一个 Ubuntu 终端：

```bash
bash /mnt/hgfs/pcie_host/preview_pie_anticipation.sh
```

在预览点击“开始检测”或按空格。默认实时道路 2.5D 视图左下显示“行人预测 · RK3568 CPU”，保留之前的界面风格、原车道效果与变道请求。路口 M 视图左下的“行人行为 · 实时分析”同时显示当前行为和 PIE 接口摘要；需要查看完整预测卡片时按 `V` 返回实时映射。

| 显示状态 | 含义 |
| --- | --- |
| 等待历史，n／6 | 已关联行人，但真实历史不足 |
| 采样间隔不符 | 本次不满足模型训练使用的采样合同，重新收集或等待合适样本 |
| 关联不确定 | 多个框可能对应同一行人，清除该预测历史 |
| 0.6s／1s 两个数值 | 六个符合条件的真实样本完成模型计算；仍受动作阶段未知的限制 |
| 感知过期／等待连接 | 当前分数无效，清空数值 |

板端编号 `P` 独立于右侧 Ubuntu 跟踪器的本地编号，不把两套 ID 直接混配。卡片最多显示三名行人，板端关联最多十六名；不会用分数给本地 2.5D 行人染色。

## 采样约束与实机记录

模型需要六个独立检测时刻，相邻间隔 160～240ms，约覆盖过去 1 秒。只有对象自身的新 `object_seq` 与 `object_ms` 增加历史；车道更新、HTTP 轮询和重复 UDP 都不增加。更快的检测帧跳过，更慢的帧重建历史，不重复帧或插值凑数。源变化、序号／时钟倒退、目标缺失、停止／重启和超过 750ms 均清理预测。

如果实际 YOLO 新结果常慢于 240ms，会经常停留在等待历史。这说明实时检测采样率未满足训练合同，不能通过虚构历史解决。该步骤的重点是记录实际就绪率，并据此决定优化检测调度还是单独训练适合更低采样率的模型。

保持视频和检测运行，第三个 Ubuntu 终端记录约 20 秒：

```bash
python3 /mnt/hgfs/pcie_host/check_pie_anticipation.py \
  --seconds 20 \
  --output /mnt/hgfs/pcie_host/rk3568_pie_deploy/board_observation.json
```

脚本记录去重后的源对象序号、采样间隔、历史状态及分数就绪次数。它只能观察到 HTTP 轮询捕获的报告子集，不能当成全部推理吞吐率，更不是带真值的准确率评测。运行结束标记为 `PIE_BOARD_OBSERVATION_COMPLETE`。

如启动失败或需查看模型日志：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_pie_anticipation_rk3568.sh log
```

## 重启后与回退

首次整合成功后，RK3568 重启无需再次训练或重新编译。恢复共享目录、USB 连接和原驱动后，执行：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_pie_anticipation_rk3568.sh start
bash /mnt/hgfs/pcie_host/preview_pie_anticipation.sh
```

若驱动尚未加载，可重新执行首次整合入口，让它检查并加载；会复用原模型并重跑必要检查。希望回到此前 HDMI 单路 P1 版本时，关闭预览后运行：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_pie_anticipation_rk3568.sh rollback
bash /mnt/hgfs/pcie_host/preview_front_p1.sh
```

回退切换到旧控制器，继续使用原检测服务与权重；没有删除新版文件。

## 当前验证记录

| 已执行检查 | 结果与边界 |
| --- | --- |
| 模型导出哈希 | `PIE_C_EXPORT_VERIFY_PASS` |
| 64 个原生 C／ONNX 对照 | 最大绝对分数误差 1.79e-7，阈值 3e-6，全部通过 |
| C 核心 | 输入、几何、真实时间间隔、歧义、重复、停止／失效／重置与 JSON 边界通过 |
| 新版 C 进程 UDP→HTTP | 1 项完整会话测试通过；6 次采样、重复包、停止、重启与失效均覆盖；输入为合成框，未连接板卡 |
| 新预览卡片 | 7 项回归通过，含模型合同、数值、时效、断流、缓存和源时钟重置 |
| 原实时道路 | 11 项通过，含真实 C 进程通信；原画面与右侧 2.5D 回归通过 |
| ARM64 | 控制器与64案例自测程序严格交叉编译通过；不等于实机运行通过 |
| 字体／布局 | 既有子集字体补齐14个新汉字、更新哈希，合成界面目视通过 |
| Ubuntu Bash／实机整合 | 脚本入口包含部署前语法检查；当前尚待用户在 Ubuntu 实际执行 |

训练方法、开发集指标与误报／漏报限制见 [PIE_ANTICIPATION_20261101.md](PIE_ANTICIPATION_20261101.md)。原始模型产物保持不变，C 导出合同位于 [c_export.manifest.json](rk3568_pie_deploy/c_export.manifest.json)。
