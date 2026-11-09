# 斑马线连续回放验证：区域过滤、时序保持和行人关系

后续完善已完成区域级关联、足点误差范围、短时运动证据及本车通道约束；请优先查看 [最新关系验证报告](CROSSWALK_RELATIONS_20261101.md) 和 [最新对比图册](runs/crosswalk/mapillary_lraspp_v1_20261101/relation_validation_20261101/index.html)。以下保留前一轮方法及发现，作为前后对照。

2026-11-01。已完成 Windows 离线验证：7 个源视频、10 个连续片段、合计 173 秒，按源时间 5Hz 进行 865 次斑马线 ONNX 推理。输出原始预测／处理后预测／车辆行人跟踪／相对 2.5D 同窗对比。全部 865 帧 H.264 产物通过实际解码检查；每个片段抽查关键画面，未声称人工逐帧标注或连续观看验收所有画面。

**结论：区域显示原型可以继续接入试验，自动礼让逻辑尚未验收。** 本轮明确发现“行人脚点是否落在斑马线分割像素上”不足以判断是否过街或是否应由本车礼让。没有重新训练模型，也没有替换现有预览、车道模型、RK3568 服务或 FPGA 程序。

打开 [连续对比视频图册](runs/crosswalk/mapillary_lraspp_v1_20261101/continuous_validation_final_20261101/index.html)。正式结果目录为 `continuous_validation_final_20261101`；此前 `v1`、`v2`、`checked` 为中间调试输出，已分别标记中断／不通过／被最终结果替代，不能当作验收结果。

**完成范围与方法**

| 项目 | 本轮实现及验证 |
| --- | --- |
| 模型 | 使用上一轮 `mapillary_lraspp_v1_20261101` 的 FP32 ONNX，源图整帧 letterbox，归一化与 manifest 一致 |
| 处理坐标 | 去除模型补边后映射到 640×360 的归一化图像平面；最终视频按各源原始长宽比显示 |
| 非道路排除 | 按视频记录人工多边形；`112507` 和 `113421` 不裁底部；`113539` 只排除已确认的仪表台内部，PIE 使用保守的车头内部区域 |
| 首次确认 | 至少两次采样支持；5Hz 时通常增加约 200ms 等待，运动／遮挡可能更长 |
| 运动对齐 | 反向 Farneback 光流，将前一采样映射到当前画面，增加光度残差及位移范围检查 |
| 概率平滑 | 有效对应像素使用当前 0.7、上一采样 0.3；当前前景阈值 0.5，小于 18 像素的连通域过滤 |
| 短时保持 | 最多 400ms，仅显示暂时不确定的历史区域；当前前景概率低于 0.15 时立即清除对应历史 |
| 重置 | 重复帧不续期；时间倒退、采样间隔超过 600ms、明显切镜、无效推理会重置 |
| 长视频时间 | 内部传递相对证据年龄，不将几百秒的绝对时间写入 float32 图像，避免小数舍入引起间歇性清除 |
| 车辆／行人 | 复用现有 YOLO 和 `RoadTracker`；当前框与淡化保持框区分显示，骑行目标重叠时标记不确定 |
| 关系验证 | 仅记录当前行人脚点与当前确认斑马线的图像关系；保持像素不充当新证据；没有可见斑马线不等于道路安全 |
| 2.5D | 复用 `GroundProjection`、`RoadRenderer`；显示真实分割及目标的相对示意，未推理新车道曲线，网格不是车道／可行驶区域真值 |
| 驾驶规则 | 本车路径 `UNRESOLVED`、礼让决策 `NOT_EVALUATED`，全部记录 `driving_permission=false`；没有发停车或通行命令 |

本次 865 个时刻均重新执行斑马线模型。为复核后处理而避免重复 YOLO，前四个片段的 475 份目标检测复用了本轮早先完成的真实结果，逐一核对源视频哈希、目标模型哈希、帧号和时间；另外 390 份重新执行 YOLO。不是伪造目标框。图中 5Hz 是验证采样／播放频率，不是板端帧率或 Windows 性能承诺。

**片段与机器记录**

| 片段 | 源时间（秒） | 推理采样数 | 落点重合候选记录数 |
| --- | --- | ---: | ---: |
| 本地 `112507` | 0–44 | 220 | 7 |
| 本地 `113421` | 0–13 | 65 | 0 |
| 本地 `113539` 等灯／通行 | 8–32 | 120 | 0 |
| 本地 `113539` 转向 | 60–74 | 70 | 2 |
| Waymo `119873…` 行人过街 | 0–12 | 60 | **0** |
| Waymo `109801…` 城市路口 | 0–8 | 40 | 12 |
| PIE video_0001 背景／车头 | 330–340 | 50 | 1 |
| PIE video_0002 过街片段 | 156–182 | 130 | 6 |
| PIE video_0002 车头误检 | 225–237 | 60 | 0 |
| PIE video_0002 路口 | 355–365 | 50 | 0 |
| 合计 | **173 秒** | **865** | **28** |

“落点重合候选记录数”按目标×采样时刻统计，同一人可计多次；不是人数、过街事件数、正确报警数或召回率。候选不代表行人已经进入本车路径。没有在本轮用 PIE 的轨迹级 crossing 标签生成逐帧礼让真值。

**关键发现及修正证据**

| 问题 | 本轮实际发现与处理 | 仍需注意 |
| --- | --- | --- |
| 大范围裁掉底部会丢失真实路面 | `112507` 39 秒，试验配置误排除了 18,040 个原始预测像素；撤掉该视频底部裁剪后排除数为 0，最终当前确认区域 8,979 像素、保持 73 像素，画面恢复近处斑马线 | 当前确认仍只有部分原始区域；5Hz 下快速近场运动及两次确认会带来滞后／边缘缺失，不能声称完整区域恢复 |
| 单纯延长保持会拖影 | `112507` 12.6 秒，之前保持区域为 9,753 像素；加入明确背景清除后为 362，画面大块残影明显减少 | 是同一帧的后处理像素对比，不是整体准确率；仍有少量残留 |
| 仪表台误检 | `113539` 25.4 秒，配置仪表台内部有 3,397 个原始预测像素，处理后该区域为 0，当前道路斑马线仍可见 | 排除区域外可能残留；模型本身的误检能力未因此被训练纠正 |
| PIE 白色车头误检 | `pie_hood` 235.6 秒，配置车头内部有 4,578 个预测像素，处理后为 0；`pie_background` 333.8 秒对应为 2,299→0 | 多边形边缘仍有少量误检，换相机／裁剪必须复核；零输出由过滤保证，不是独立精度证明 |
| 本车之外的横道也能产生候选 | PIE 165.6 秒，左侧支路／路边目标出现落点重合候选 | 不能仅凭重合就给本车报警；本车路径仍未知 |
| 明显过街也可能没有像素重合 | Waymo `119873…` 0.4 秒等画面可见前方行人横穿，但整个所查片段的脚点重合候选为 0 | 人体／车辆遮挡、斑马线区域分割缺口、框底误差会漏掉关系；当前算法不具备可靠自动礼让条件 |

最终复核图片：

- [近处斑马线：112507 / 39 秒](runs/crosswalk/mapillary_lraspp_v1_20261101/continuous_validation_final_20261101/local112507/review_00039.000.jpg)
- [保持残影：112507 / 12.6 秒](runs/crosswalk/mapillary_lraspp_v1_20261101/continuous_validation_final_20261101/local112507/review_00012.600.jpg)
- [仪表台：113539 / 25.4 秒](runs/crosswalk/mapillary_lraspp_v1_20261101/continuous_validation_final_20261101/local113539_wait/review_00025.400.jpg)
- [PIE 车头案例](runs/crosswalk/mapillary_lraspp_v1_20261101/continuous_validation_final_20261101/pie_hood/largest_excluded.jpg)
- [Waymo 行人关系漏掉的案例](runs/crosswalk/mapillary_lraspp_v1_20261101/continuous_validation_final_20261101/waymo_crossing/review_00000.400.jpg)
- [PIE 侧方重合候选](runs/crosswalk/mapillary_lraspp_v1_20261101/continuous_validation_final_20261101/pie_crossing/contact_candidate.jpg)

**已执行检查与可用边界**

7 项回归通过：重复帧／两次确认／保持到期，间隔／倒退／切镜／无效数据清除，排除区域，长视频绝对时间稳定性，平移运动跟随，明确背景立即清除，关系证据与不授予通行许可。原有运行代码未修改，因此没有重复运行无关 FPGA 或板端集成测试。

`CROSSWALK_REPLAY_ARTIFACT_VERIFY_PASS`：10 段 H.264/yuv420p 视频共 865 帧逐帧解码通过，帧尺寸 1280×800、5FPS；源采样时间／帧号严格递增。模型文件、原车道权重哈希一致，逐帧关系记录没有授予驾驶许可。核验文件：[artifact_verification.json](runs/crosswalk/mapillary_lraspp_v1_20261101/continuous_validation_final_20261101/artifact_verification.json)。

没有新的像素或事件真值，也没有独立夜间、雨天验收。本批素材已经参与调参，属于开发回归集。由排除区域内像素清零、候选数量下降或画面更稳定，不能推导礼让准确率提高。2.5D 使用默认单应映射，不是标定好的米制俯视图。

**使用与复现**

直接看结果，不需要启动虚拟机或 RK3568：

```powershell
Set-Location 'F:\B_file\jichuangsai\Pango_FPGA\09_pcie_test\pcie_host'
Invoke-Item '.\runs\crosswalk\mapillary_lraspp_v1_20261101\continuous_validation_final_20261101\index.html'
```

浏览器中点播放。H.264 版本为每个片段目录的 `comparison_browser.mp4`，原始编码文件和逐帧 `frames.jsonl` 同时保留。每个片段的 `summary.json` 包含源范围、人工配置及统计；根目录 `run.json` 保存模型、源视频、代码和配置哈希。

重新生成一份独立回放（必须使用新的输出目录）：

```powershell
& '.\.venv-lane\Scripts\python.exe' -m unittest discover -s tests -p test_crosswalk_validation.py -v

& '.\.venv-lane\Scripts\python.exe' -u training/validate_crosswalk_video.py `
  --output runs/crosswalk/mapillary_lraspp_v1_20261101/continuous_validation_reproduce

& '.\.venv-lane\Scripts\python.exe' -u training/finalize_crosswalk_validation.py `
  --run runs/crosswalk/mapillary_lraspp_v1_20261101/continuous_validation_reproduce
```

H.264 编码使用 `.venv-lane` 中新增的 `imageio-ffmpeg==0.6.0`，未修改模型训练权重。新视频需在 [crosswalk_video_cases.json](training/crosswalk_video_cases.json) 中单独配置范围、真实长宽比和已核实的非道路区域，不能复制本批车头多边形后默认有效。

**下一步优先级**

| 优先级 | 工作 | 验证目标 |
| --- | --- | --- |
| 1 | 将同一过街带的可见分割片段组织成区域，明确遮挡／未知；改进近场区域级关联，减少逐像素确认导致的缺口 | 用 Waymo 0–5 秒及本地近场通过片段检查；不能把所有碎片直接求大凸包 |
| 2 | 将行人足点误差范围、轨迹和过街带关联，再结合本车两侧边界／选定方向；没有可靠本车路径时保持未知 | 同时覆盖“明显横穿却未重合”和“侧方支路重合但与本车无关” |
| 3 | 用小批连续片段补人工过街区域、遮挡和本车路径冲突标签，保留未参与调整的片段 | 才能量化漏报、误报及确认／解除延迟；当前不需要为此再盲目下载大型数据集 |
| 4 | 显示层通过后，先把斑马线作为独立感知层接入现有界面，再验证 RKNN 和 NPU 调度 | 保留现有车道效果和预览流畅度，不提前把实验关系判定接成停车规则 |
