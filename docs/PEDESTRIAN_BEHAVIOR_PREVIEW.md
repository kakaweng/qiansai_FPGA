# 路口行人显示与当前横穿行为（2026-11-02）

后续新增：M入口现在使用“播放时积累运动 → 暂停源视频 → N分析当前场景 → 三秒冻结推演”。本文的当前行为分析继续作为其运动依据，新的操作与限制见 [FROZEN_JUNCTION_PREVIEW.md](FROZEN_JUNCTION_PREVIEW.md)。

PIE set01 的 `video_0004.mp4` 约53秒，红衣行人正在从右向左横穿。旧预览将不能与斑马线分割关联的人排除在主场景之外，导致真实行人消失；纯脚点接触也不能覆盖横道漏分割、近处脚部裁切。本次补齐当前运动行为和显示，不重训、不改 FPGA、RKNN 权重或 RK3568 控制协议。

## 最终行为

| 情况 | 主场景与反馈 |
| --- | --- |
| 检出人，但横道关联暂不明确 | 仍画真实检测人形，以原图左右位置和人体框高给出粗略示意位置，虚环表示估计 |
| 连续观测到近处行人有一致横向运动 | 扣除背景相机运动后确认“横穿中”，显示右→左/左→右；缺少脚点支持时允许推测为驶入横道 |
| 与所选路线的横道相关 | 本地预演停车礼让，依据为运动时明确写“运动推测”；不因瞬时漏检立即放行 |
| 远处站立、相机平移、背景证据不足 | 不凭检测到人或原始屏幕位移升级为横穿；显示静止、移动或行为待确认 |
| 短时丢失 | 保持人形并淡化，行为标记短时保持；超过2.6秒清除，不永久残留 |

继续沿用深色布局。路口左下替换操作说明区，加入“行人行为 · 实时分析”，最多显示三名行人的状态、方向和位置来源；六个人形显示名额优先保留横穿者。没有真实检测框的计数补全不画成人形。

## 当前行为与 PIE 提前预测的区别

| 内容 | 当前实现与边界 |
| --- | --- |
| 当前行为 | 新 `pedestrian_behavior.py` 的连续运动规则；320×180真实源图像、背景特征光流与RANSAC仿射补偿，再分析真实对象观测历史 |
| 确认条件 | 至少3次独立观测、跨度至少450ms；近处人体、横向运动幅度/速度与方向一致性共同满足，不能重复轮询凑历史 |
| 图像配对 | 只取不晚于对象自身源时间且相差不超过220ms的画面；对象间隔80～1500ms，证据不足不升级 |
| PIE v2 | 仍在RK3568 CPU执行既有训练分类头，预测未来0.6秒/1秒首次横穿的未校准分数；不等同于当前是否正在横穿 |
| PIE 可见性 | 普通视图保留原预测卡；M路口视图现在也显示接口状态或有效分数，不再需要退回普通视图才能看到 |
| PIE 未就绪 | 原6次观测、160～240ms间隔合同保持；慢于合同会显示采样间隔不符/等待历史，不伪造预测分数 |
| 预测可靠性 | `eligibility:unverified`、`no_control:true`保持；板端编号与本地编号独立，不直接混配或用分数染色当前行为 |

本次不是新一轮训练，也没有宣称训练模型已经可靠理解行人意图。PIE的行为标注用于离线核对当前错误和原模型训练；生产预览不读取XML、视频文件名或“53秒”来安排人物。当前行为由实时图像与检测结果计算。

## 应用到既有实机环境

保持当前板端检测服务运行，关闭旧预览。在Ubuntu执行：

```bash
bash /mnt/hgfs/pcie_host/update_junction_alignment_preview.sh
```

看到 `JUNCTION_ALIGNMENT_PREVIEW_PASS` 后：

```bash
bash /mnt/hgfs/pcie_host/preview_pie_anticipation.sh
```

启动器会同步本轮Python文件和字体到Ubuntu本地工作目录。按 `M` 进入路口；`J/I/L` 选择左转/直行/右转，`G`开始、`B`暂停、`R`重置。将原片从49秒附近连续播放到56秒，避免直接跳到53秒时没有此前运动历史。

## 验证结果

Windows使用既有YOLO与Mapillary ONNX对原片采样，给预览模拟900ms结果交付延迟；这些是开发回放，不是板端运行验收或独立数据集准确率。

| 验证 | 本轮结果 |
| --- | --- |
| 自动回归 | 163项通过；覆盖前视状态、车道/目标展示、横道关联、运动补偿、路线冲突、失效保持、协议及PIE预测接口 |
| 0004 49.5～56秒 | 连续98帧，红衣行人官方ID `1_4_106` 在12个已交付且有标注的采样均保留显示，本地编号始终P3 |
| 官方crossing对照 | 上述9个官方crossing采样中8个显示当前横穿；首个约51.03秒仍为移动中，约51.5秒源观测开始确认横穿 |
| 用户关注的53秒 | 源观测约53.03秒：P3、CROSSING、驶入横道位置估计、YIELD；接收显示另有模拟900ms延迟 |
| 路边负例 `1_4_107` | 13个官方not-crossing采样，8个进入当前6人显示名额，均未被提升为横穿；其余未显示不能算作负例识别成功 |
| 其他片段 | 0003 329～335秒90帧、0004 158～162秒60帧，均无文字截断、无越过程序所判定冲突的帧 |
| 时间核对 | 离线报告中未发现使用未来的源图像/目标的情况；标注工具只作事后比较 |

以上针对同一演示片段开发，不能换算成泛化准确率。模板横道角色和估计位置仍可能有误；0004接近横穿结束时可出现横道角色切换，精确地图和相机标定问题仍未解决。当前运动规则也不能代替起步前意图预测。

输出位于 `rk3568_crosswalk_deploy/alignment_review/behavior_motion_v4/`，包含 `preview_after.mp4`、界面PNG、`review.json`、`red_person_labels.json`、`roadside_person_labels.json`。其他片段位于 `behavior_0003_v4/`、`behavior_0004_right_v4/`。

复现关键片段：

```powershell
.\.venv-lane\Scripts\python.exe tools\review_junction_presentation.py --video F:\waymo_data\pie\set01\video_0004.mp4 --start 49.5 --seconds 6.5 --delay-ms 900 --cached-samples rk3568_crosswalk_deploy\alignment_review\behavior_0004\samples.json --output rk3568_crosswalk_deploy\alignment_review\behavior_motion_v4
.\.venv-lane\Scripts\python.exe tools\evaluate_pedestrian_behavior.py --review rk3568_crosswalk_deploy\alignment_review\behavior_motion_v4\review.json --annotations F:\waymo_data\pie\annotations\set01\video_0004_annt.xml --person 1_4_106 --output rk3568_crosswalk_deploy\alignment_review\behavior_motion_v4\red_person_labels.json
```

`evaluate_pedestrian_behavior.py` 是离线核对工具，仅使用对象源时间匹配官方框；不会被预览启动器复制或导入。
