#ifndef FRONT_PANEL_H
#define FRONT_PANEL_H

static const char panel[] =
"<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
"<meta name='viewport' content='width=device-width'>"
"<title>RK3568 前视辅助驾驶</title>"
"<style>body{font:18px system-ui;background:#101a2b;color:#eef;padding:24px;max-width:900px;margin:auto}"
"button{font:inherit;padding:12px 20px;margin:8px 8px 8px 0}"
"pre{white-space:pre-wrap;line-height:1.65;background:#17243a;padding:16px;border-radius:8px}"
".hint{color:#b8c7dd}</style>"
"<h1>前视 HDMI · 辅助驾驶</h1>"
"<p class='hint'>只使用 RK3568 HDMI 正前方视频流；不需要 Windows 双路同步、OV5640 或以太网视频。</p>"
"<button onclick=\"act('start')\">开始前视检测</button>"
"<button onclick=\"act('stop')\">停止</button><br>"
"<button onclick=\"drive('left')\">请求向左</button>"
"<button onclick=\"drive('right')\">请求向右</button>"
"<button onclick=\"drive('cancel')\">取消请求</button>"
"<button onclick=\"drive('brake')\">模拟制动</button>"
"<pre id='status'>连接中…</pre>"
"<p class='hint'>规则：实线只报警阻止；虚线只给人工复核提示。前方行人、车辆和信号灯仅作辅助证据，不输出自动驾驶放行结论。</p>"
"<script>const names={IDLE:'等待开始',RUNNING:'检测中：HDMI 前视',HDMI_STALE:'HDMI 数据过期：暂缓判断',STOPPED:'已停止',ERROR:'故障'},"
"lineNames=['未知','实线','虚线'],signalNames=['未知','红灯','黄灯','绿灯'];"
"async function call(url){try{return await (await fetch(url,{method:'POST',headers:{'X-Sync-Control':'1'}})).json()}catch(e){return {error:String(e)}}}"
"async function act(a){await call('/api/'+a);tick()}"
"async function drive(a){let r=await call('/api/drive?action='+a);if(r.error)alert(r.error);tick()}"
"async function tick(){try{let s=await(await fetch('/api/status')).json(),d=s.drive||{};"
"document.getElementById('status').textContent=(names[s.state]||s.state)+'\\n'+"
"'状态原因：'+s.reason+'\\n来源：'+(s.source||'HDMI')+'\\n'+"
"'变道请求：'+(d.request?'向'+(d.request===1?'左':'右'):'无')+'\\n'+"
"'反馈：'+d.feedback+'\\n边界类型：[左 '+lineNames[(d.line||[])[0]||0]+'，右 '+lineNames[(d.line||[])[1]||0]+']\\n'+"
"'信号：'+signalNames[d.signal||0]+'\\n前方车辆：'+(d.vehicles||[])[0]+'，行人：'+d.people+'\\n'+"
"'车速(模拟)：'+d.speed_kmh+' km/h，挡位：'+d.gear+'\\n'+"
"'车道结果年龄：'+d.lane_age_ms+' ms，目标结果年龄：'+(d.object_age_ms||[])[0]+' ms';"
"}catch(e){document.getElementById('status').textContent='控制服务连接中断：'+e}setTimeout(tick,300)}tick()</script></html>";

#endif
