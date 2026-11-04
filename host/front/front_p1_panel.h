/* P1 interactions live in the video window so lamp selection uses the image. */
static const char panel[] =
"<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>HDMI P1</title>"
"<body style='font:20px sans-serif;padding:36px;background:#101a2b;color:#eef'>"
"<h1>HDMI 单路辅助驾驶 P1</h1><p>请在同一个视频预览窗口操作：</p>"
"<pre>bash /mnt/hgfs/pcie_host/preview_front_p1.sh</pre>"
"<p>开始检测后自动显示灯色；M进入路口，J/I/L选择方向，G开始。右转检测到行人提示礼让，当前有效结果无人时反馈可右转，无需选择灯型。</p>"
"<p>车道边界短时漏检保持最多1.5秒，保持期间会标注。</p>"
"<p>左 / 右变道请求使用 A / D；屏幕 LED 和车速是模拟状态。</p>"
"<p><a href='/api/status' style='color:#91c8ff'>查看板端原始状态</a></p></body></html>";
