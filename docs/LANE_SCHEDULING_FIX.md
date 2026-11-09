# 三路辅助识别接入后的车道跳变修正

2026-10-28。针对同一段前方视频在v6中标线和中文状态一起跳变的反馈，修正模型之间的计算调度。模型仍为第24轮LR-ASPP和原YOLOv5 RKNN；此次没有训练或转换模型。

## 证据与修改

| 已核实的现象或代码 | 含义与处理 |
|---|---|
| 用户录屏《屏幕录制 2026-10-28 204749.mp4》中，车道年龄出现828ms、902ms，上方出现STALE；部分时刻上方仍有线，下方待确认 | 超过750ms的结果失效参与了跳变；视频叠加和HTTP面板更新有时间差。录屏本身不能确定每次UNKNOWN的原因 |
| 原v6的“前方优先”仅在YOLO前/左/右内部生效，车道模型与YOLO之间是普通互斥锁 | 新增显式仲裁：排队车道优先，连续最多两个车道任务后必须服务等待的目标检测，避免目标任务饿死 |
| 原worker先取出图像，再预处理、等待NPU；等待期间processing图像已固定 | v6在获得计算机会后才选择来源并交换最新帧；等待期间采集仍可覆盖pending，不积压旧图像 |
| 旧车道输出有500ms输入间隔重置、350ms短暂保持、750ms结果时效 | 全部保留；不靠延长旧证据的寿命消除跳动。超过500ms是否反复重置须看实机lane_diag，不能只凭录屏认定 |

上表记录的是 v6 调度修正时的基线。A1 后续单独把板端已确认显示保持调整为 550ms，并增加一次同几何 `UNKNOWN` 容忍；500ms 输入间隔重置和750ms总结果时效不变。
| 中文面板原每300ms拉取一次板端状态 | 改为后台每100ms拉取，减少板端已更新而面板尚未拉取的窗口；视频接收和HTTP仍独立 |
| 过期和未确认以前均显示待确认 | 新面板在已知车道年龄超过750ms时显示“已过期”；有效但线型未知时仍为“待确认” |

计算机会覆盖取最新帧、CPU预处理和NPU输入/运行/取输出，随后立即释放，CPU解码继续独立运行。此实现减少排队旧帧的年龄，也会减少部分预处理重叠；实际吞吐量及稳定性须由RK3568运行结果确认。同步时间码、session隔离、开始屏障、侧方来源隔离和原驾驶规则保持有效。

## Ubuntu应用更新

共享目录、HDC和驱动已正常的情况下，在同窗面板先点停止检测，再关闭预览窗口。保留正确运行的Windows受控播放器及Ubuntu relay终端。在Ubuntu执行：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/integrate_drive_assist_rk3568.sh
```

此命令重新测试、编译并安装v6程序，复用板上模型，不安装Python依赖、不转换模型、不改FPGA位流。仅执行`deploy_drive_assist_rk3568.sh start`不会重编译本次C修改。成功结尾为`DRIVE_ASSIST_PASS`，服务启动日志应包含：

```text
Lane scheduling: lane-priority-v1; reserve before newest frame; bounded object fairness; original expiry limits
```

随后打开统一预览：

```bash
bash /mnt/hgfs/pcie_host/preview_drive_assist.sh
```

在窗口点开始/重播或按空格，仍使用之前同一段视频观察清晰直线路段。无需再次重启或烧录板卡；若relay已关闭，另开终端执行：

```bash
python3 /mnt/hgfs/pcie_host/sync_control_relay.py
```

## 验证与实机判断

| 本地验证 | 覆盖范围 |
|---|---|
| NPU仲裁线程测试 | 串行执行、排队车道优先、目标检测在两个车道任务后得到机会、唤醒和资源复用 |
| v6车道worker测试 | 等待资源时仍可提交/替换最新帧；等待中reset或stop不执行旧帧、归还执行机会；原过期与旧session丢弃 |
| 三路目标worker测试 | 获得计算机会后取最新侧方帧；重播取消旧pending；保留分路轮询、来源隔离、冻结过期和失败清理 |
| 真实引擎函数＋模拟RKNN回调 | 无效输入、预处理、inputs_set/run/outputs_get/outputs_release错误及成功，均恰好释放一次；原同步API继续有效 |
| 8项预览测试 | 后台HTTP、过期显示、断流清理、操作路径和失效立即重绘 |
| ARM64严格交叉编译 | v6生产程序通过-Wall -Wextra -Werror；本地测试不等于板端性能验收 |

新增日志`lane_async`和`objects_multi`带`queue_ms`（等待执行机会）、`input_age_ms`（获得机会后选中图像的年龄）、`result_age_ms`（完成处理时的结果年龄）；`lane_diag input_gap_ms/reset`继续记录车道时序。排队时间较长时，input_age仍应反映刚选取的新帧，而不是把全部排队时间加在旧输入上。显示期间的结果年龄还会继续增长，不能只看完成瞬间的年龄。

若更新后清晰直路仍频繁跳变，在保持预览运行时读取：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_drive_assist_rk3568.sh log
```

根据以上分项耗时和reset原因判断是否仍受实际算力限制，再调整侧方任务频率。当前不能承诺某个帧率，或保证路口、遮挡场景一定有可靠线型。

已安装的同步v5仍可回退：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/deploy_drive_assist_rk3568.sh rollback
```

该回退恢复原前方识别和同步控制，不包含v6侧方检测与中文驾驶状态；统一面板需v6接口。
