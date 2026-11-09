# HDMI 识别结果消失与车道确认重置修复

2026-11-01。用户日志确认模型仍在工作：YOLO返回6辆车，但约991ms的排队加推理耗时超过750ms新鲜期限；车道输出稳定的SOLID/DASHED，617～651ms的分析间隔却触发原500ms重置，确认计数一直在1和2之间循环。13FPS只是视频刷新速度。

| 环节 | 本次修改 |
| --- | --- |
| 前视YOLO | 和车道一样先取得NPU名额，再从pending队列选最新帧；排队时持续替换输入。reset/stop取消待处理工作并归还计算名额。新日志分开记录queue_ms、input_age_ms、result_age_ms。 |
| 车道确认 | 仅HAVE_FRONT_P1运行时将分析样本间隔容忍设为1200ms，仍要求3次几何一致的观测。实际HDMI提交帧断档超过500ms、回退或换源会清空历史；原550ms确认保持与750ms结果期限不延长。默认其他配置仍用500ms。 |
| YOLO框 | 前视画框单独允许1500ms显示期限，超过750ms画灰色并标HOLD；原决策快照及灯色证据的期限不变。 |
| 普通实时2.5D | 使用独立显示跟踪器读取2500ms以内的display_world，严格感知目标为空时以淡色及~标记短时显示；不修改用于横道关联和驾驶判断的tracker/world。停止、断流、错误producer、时间错位和换会话都会清除。 |
| 路口M视图 | 保留感知增强、横道归属修正和场景交互。 |

先关闭旧预览，在Ubuntu执行：

```bash
sudo -v && bash /mnt/hgfs/pcie_host/repair_front_perception_rk3568.sh
```

复用已部署的模型，不转换、不训练。脚本运行回归、编译、上传和重启，读取启动日志确认实际版本及取帧策略；最后应显示`FRONT_PERCEPTION_REPAIR_PASS`。再执行：

```bash
bash /mnt/hgfs/pcie_host/preview_pie_anticipation.sh
```

空格开始检测；M切换路口。左侧HDMI原片建议全屏并隐藏播放器控件。普通视图的灰色HOLD框及2.5D淡色目标表示历史结果，更新后恢复正常颜色。若持续超过显示期限仍会清空，不能将它解释成无人/无车。

启动日志必须包含：

```text
multivideo-hdmi-front-observed-junction-v5-fresh
Objects scheduling: reserve-before-latest-v2
Lane cadence: analysis_gap_ms=1200
```

运行约20秒后，可通过`sudo -v && bash /mnt/hgfs/pcie_host/deploy_pie_anticipation_rk3568.sh log`检查：YOLO的queue_ms允许较大，但input_age_ms应明显小于旧排队耗时；result_age_ms以实际推理性能为准。车道相近几何的确认应达到3，不应在约617ms间隔反复reset=GAP。实际源断流仍应触发复位。

本地验证包含日志617/333ms节奏重放、旧版500ms复位、原保持过期、真实线程排队/最新帧替换/取消/停止、预处理和RKNN失败释放，以及展示通道不影响决策的回归。Windows线程验证使用项目已有测试适配；Ubuntu更新入口再执行Linux pthread测试。ARM64服务严格交叉编译通过，实机耗时和显示改善须以部署后结果验收。
