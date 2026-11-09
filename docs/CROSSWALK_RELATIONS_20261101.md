# 过街区域、行人和本车路径：关系原型完善记录

2026-11-01。已完成独立 Windows 离线原型、10 项关系回归及 7 个视频／10 个片段的回放，合计 173 秒、865 个采样时刻。正式产物为 [对比视频图册](runs/crosswalk/mapillary_lraspp_v1_20261101/relation_validation_20261101/index.html)。

**结论：已补上区域级关联、脚点误差范围和短时横向运动证据，Waymo 中原先没有脚点重合候选的横穿行人现在能得到关注提示。路口的本车路径仍经常未知，本轮尚不能可靠判断何时应由本车礼让。** 当前输出用于识别展示和关系调试，没有接入停车／通行控制。本轮复用已有模型，没有重新训练或改动当前在线预览、RK3568 服务及 FPGA。

| 层次 | 本轮实现 | 使用范围与限制 |
| --- | --- | --- |
| 过街区域 | 以当前分割组织局部连续区域，区域 ID 关联、两次当前观察确认；分别记录可见与小间隙推断部分 | 不是完整道路拓扑；不以大凸包连接所有碎片，不跨车辆遮挡填补区域 |
| 行人定位 | 用检测框底部的有界椭圆范围表达足点误差，关联附近区域 | 减少单像素错位漏掉关系；边缘也可能误关联，不能视为人工真值 |
| 短时运动 | 背景角点／光流／RANSAC 近似补偿画面运动，再判断连续横向位移 | 是图像运动线索，不是行人意图、实际速度或未来轨迹；只能升级为关注 |
| 本车通道 | 使用原车道模型与既有 C 解码器给出的本车左右边界，仅取共同可见的纵向范围 | 不外推到路口；缺少边界、几何异常、结果过期时路径未知 |
| 提示确认 | 同一目标至少两个独立采样确认；未知立即撤销明确关系，重复帧不续期 | 当前仍有确认延迟和提示间断，未通过延长保持掩盖证据缺失 |
| 展示 | 原始预测与关系结果并排，下方复用现有跟踪和相对 2.5D，保留深蓝中文样式 | 离线对比产物；2.5D 不是标定后的米制俯视图 |

**提示的含义**

| 状态 | 需要的证据 | 含义 |
| --- | --- | --- |
| `CROSSING_ATTENTION` | 行人与当前确认过街区有关，或在其附近出现可靠短时横向运动 | 关注过街目标；本车路径关系可能仍未知 |
| `POTENTIAL_CONFLICT` | 当前区域接触证据、有效本车通道、足点误差范围与通道相交，并连续确认 | 可能占用当前可见通道，只是辅助提示，不发停车指令 |
| `SIDE_OBSERVATION` | 行人、过街区都处于当前通道可覆盖的纵向范围，且在通道外、不与通道重合 | 当前观测支持侧方关系；不代表后续不会横穿 |
| `CONFIRMING` | 新关系尚未满足连续确认 | 证据确认中 |
| `NO_CONFIRMED_RELATION`／`UNKNOWN` | 缺少足够关系或路径证据 | 不是道路安全，也不授予通行许可 |

所有逐目标记录均有 `driving_permission=false`。只有横向运动、没有占用证据的目标不能进入 `POTENTIAL_CONFLICT`。

**真实回放结果**

| 片段 | 源时间（秒） | 采样帧 | 关注帧 | 当前通道有效帧 | 旧脚点重合目标帧 | 新确认关联目标帧 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 本地 112507 | 0–44 | 220 | 3 | 163 | 7 | 3 |
| 本地 113421 | 0–13 | 65 | 0 | 0 | 0 | 0 |
| 本地 113539 等待路口 | 8–32 | 120 | 3 | 6 | 0 | 3 |
| 本地 113539 转弯 | 60–74 | 70 | 3 | 7 | 2 | 3 |
| Waymo 横穿 | 0–12 | 60 | 23 | 0 | 0 | 33 |
| Waymo 城市路口 | 0–8 | 40 | 14 | 20 | 12 | 14 |
| PIE video_0001 背景 | 330–340 | 50 | 1 | 26 | 1 | 1 |
| PIE video_0002 过街 | 156–182 | 130 | 17 | 13 | 6 | 21 |
| PIE video_0002 车头误检 | 225–237 | 60 | 0 | 0 | 0 | 0 |
| PIE video_0002 路口 | 355–365 | 50 | 5 | 0 | 0 | 5 |
| 合计 | 173 秒 | **865** | **69** | **235** | **28** | **83** |

“关注帧”按采样时刻计；“目标帧”按目标×采样时刻计，同一人可以计多次。这些数字不是人数、交通事件数、准确率或召回率。旧逻辑与新逻辑的确认规则也不同，不能把计数相除作为性能提升比例。83 条已确认关联中，5 条为脚点直接接触、48 条为误差范围内可能接触、30 条为邻近区域的短时横向运动。

**本批真实回放没有出现已确认的 `POTENTIAL_CONFLICT` 或 `SIDE_OBSERVATION`。** 两个分支通过的是合成单元测试；实际路口里车道边界不足，或行人超出曲线共同可见范围，不能把这些测试当成实片冲突判断验收。

| 目视复核例子 | 实际观察 | 剩余问题 |
| --- | --- | --- |
| [Waymo 横穿约 1.3 秒](runs/crosswalk/mapillary_lraspp_v1_20261101/relation_validation_20261101/waymo_crossing/frame_001.30.jpg) | 原脚点重合为 0，新逻辑能对横穿行人给黄色关注框；本片 60 个采样时刻中有 23 个关注时刻 | 0.4 秒仍未关联，首次关注约为源时间 1.300 秒，之后仍有间断；这是开始提示的源时间，不是有真值标注的响应延迟 |
| [PIE 165.6 秒侧方支路](runs/crosswalk/mapillary_lraspp_v1_20261101/relation_validation_20261101/pie_crossing/frame_165.60.jpg) | 有区域关联，但行人位置超出本车曲线可覆盖范围，保留关注而未升级为冲突 | 尚不能自动确认“与本车无关”；仍可能存在多余关注 |
| [本地 112507 39 秒近处横道](runs/crosswalk/mapillary_lraspp_v1_20261101/relation_validation_20261101/local112507/review_039.000.jpg) | 近处横道保留，当前有效区域进入相对 2.5D | 真实区域仍可能缺边，路口路径未知 |
| [本地 112507 12.6 秒直路](runs/crosswalk/mapillary_lraspp_v1_20261101/relation_validation_20261101/local112507/review_012.600.jpg) | 显示当前两侧边界，无确认过街关系 | 对骑行人保留身份不确定处理；不能据此声称前方无风险 |
| [本地 113539 70 秒转弯](runs/crosswalk/mapillary_lraspp_v1_20261101/relation_validation_20261101/local113539_turn/review_070.000.jpg) | 可见横道和路边目标分别展示，没有强行连接到本车路径 | 路口曲线不足，无法判断未来转弯是否与其相交 |
| [PIE 230 秒车头](runs/crosswalk/mapillary_lraspp_v1_20261101/relation_validation_20261101/pie_hood/review_230.000.jpg) | 原始车头误检在已配置排除范围中被过滤，没有确认过街关联 | 这是按相机配置的过滤，不能当作模型已学会排除车头；换视频需重新核实 |
| [PIE 356.8 秒路口](runs/crosswalk/mapillary_lraspp_v1_20261101/relation_validation_20261101/pie_junction/frame_356.80.jpg) | 侧方目标得到关注，近处其他行人仍按普通目标展示 | 没有过街区域／路径证据的行人仍可能漏掉关系；当前规则不能承担所有前方行人风险提示 |

已对全部 10 个片段抽查关键画面，没有对 865 帧逐一人工标注，也没有把逐帧解码检查描述为人工连续观看验收。本批视频已用于调参，属于开发回归集；尚无独立夜间、雨天或新相机的关系验收集。

**实现、检查和来源**

| 文件／产物 | 用途 |
| --- | --- |
| [crosswalk_relations.py](crosswalk_relations.py) | 区域组织、本车通道、足点误差、背景运动补偿和提示确认 |
| [test_crosswalk_relations.py](tests/test_crosswalk_relations.py) | 10 项测试，覆盖遮挡不填补、边界时效、重复帧、时钟重置、弱运动证据不升级冲突，以及整幅画面平移不能当作行人横穿 |
| [validate_crosswalk_relations.py](training/validate_crosswalk_relations.py) | 对齐原视频帧号／PTS，执行模型或复用校验过的感知缓存，生成关系记录和对比视频 |
| [finalize_crosswalk_relations.py](training/finalize_crosswalk_relations.py) | 证据规则核验、H.264 编码和全部帧解码检查 |
| [run.json](runs/crosswalk/mapillary_lraspp_v1_20261101/relation_validation_20261101/run.json) | 源视频、模型、代码、C 桥、配置及前轮 YOLO 记录 SHA256；缓存来源及复核范围 |
| [artifact_verification.json](runs/crosswalk/mapillary_lraspp_v1_20261101/relation_validation_20261101/artifact_verification.json) | `RELATION_ARTIFACT_VERIFY_PASS`，10 段、865 帧 H.264/yuv420p 实际解码通过，产物哈希、无驾驶权限核验 |

首个关系版本 `relations_v1_20261101` 执行了 865 组真实斑马线和原车道 ONNX 推理；YOLO 复用前轮 `continuous_validation_final_20261101` 保存的 865 组真实检测。最终版本复用同一批斑马线／车道结果重跑关系规则，使前后差异来自规则。缓存使用前核对视频、模型、C 桥、配置及 YOLO 记录哈希；逐帧核对源帧号和 PTS。中间 `relations_v2_20261101` 因缓存字段缺失中断，已修正后完成正式目录，不能将中间结果用于交付。

本车通道来自原车道模型 `bdd_lraspp_v1_20261013/best.onnx` 和现有 `live_road_bridge.dll`；不是凭屏幕中线生成的默认梯形。原视频等比适配 HDMI 平面，再把解码曲线映回源坐标；Waymo 原始 1920×1280 保持长宽比。在线部署时仍需单独验证实际异步结果年龄；这里输入是按同一源帧配对的离线结果。

关键参数为：区域 closing 核 19×5、新填补像素距真实支持不超过 7 像素；足点椭圆半径 x=4–24／y=4–13 像素，均在 640×360 处理平面计算；本车两侧曲线质量至少 0.35，结果年龄最多 250ms。背景运动估计需至少 16 个匹配、12 个内点、内点比例至少 0.65，限制缩放／旋转／平移；短时横向证据需至少两段有效运动及 0.35 秒观察，累计横向残差至少 9 像素且方向一致。参数属于本批开发设置，未标定成实际距离。

**打开结果与复现**

在 Windows PowerShell 直接看对比，不需要启动虚拟机或 RK3568：

```powershell
Set-Location 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host'
Invoke-Item '.\runs\crosswalk\mapillary_lraspp_v1_20261101\relation_validation_20261101\index.html'
```

网页优先看 Waymo 横穿与 PIE 侧方例子，再检查其余片段。视频是按源时间 5Hz 采样的离线回放；这个数值不代表 Windows 三模型实时帧率或 RK3568 性能。

需要复现时使用新的输出目录；以下会重新执行斑马线／车道推理，YOLO 仍读取已验证的前轮记录：

```powershell
& '.\.venv-lane\Scripts\python.exe' -m unittest discover -s tests -p test_crosswalk_relations.py -v

& '.\.venv-lane\Scripts\python.exe' -u training/validate_crosswalk_relations.py `
  --output runs/crosswalk/mapillary_lraspp_v1_20261101/relation_reproduce

& '.\.venv-lane\Scripts\python.exe' -u training/finalize_crosswalk_relations.py `
  --run runs/crosswalk/mapillary_lraspp_v1_20261101/relation_reproduce
```

只重跑关系规则时，在验证命令末尾增加 `--cache runs/crosswalk/mapillary_lraspp_v1_20261101/relations_v1_20261101`。每次使用新的输出目录，编码工具不会覆盖已有视频。新的源视频需先配置并生成配套的前轮感知记录，不能直接复用当前帧号或排除多边形。

**下一阶段的优先级**

| 顺序 | 工作 | 要解决的问题 |
| --- | --- | --- |
| 1 | 从已有素材补少量连续关系真值：行人脚部／遮挡、过街区、当前可见通道、所选转向路径及未知状态；留出未参与调参的片段 | 量化漏关联、侧方多余关注、确认和解除延迟；当前不缺继续开发所需的大型数据集 |
| 2 | 在现有 2.5D 交互里明确区分“感知到的车道”和“用户选择的路口路线”，验证路线与过街区的关系 | 路口没有车道线时，单靠原车道模型不能给出未来转弯路径；选定路线是演示约束，不能冒充自动识别 |
| 3 | 把经过复核的斑马线／关注提示先作为独立显示层接入，再做 RKNN 转换、实际延迟和 NPU 调度验证 | 保留当前已验收的车道稳定性与预览流畅度；依据实测决定分辨率与更新频率 |

自动停车／起步需要在上述关系和延迟验收后单独接入。当前最重要的缺口是路口路径依据和独立关系真值，继续扩大训练或增加保持时间不能直接替代它们。
