"""Export clean, traceable driving demo clips; never changes source datasets."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import subprocess
import time

import cv2
import numpy as np

ROOT=Path(__file__).resolve().parent
WAYMO=Path('F:/waymo_data/decoded_perception')
LOCAL=Path('D:/buduinetdiskdownload')
PIE=Path('F:/waymo_data/pie')
CASES=[
    dict(id='01_signal_red_green',title='红灯等待 → 绿灯通行',requirement='测试方式(1)',
         source=str(LOCAL/'video_20260929_113539.mp4'),start=10.,end=35.,priority='主演示',
         observation='前车在红灯前停车，约原片27秒附近变绿后起步；灯位可见。',
         operation='选择直行，红灯期间演示停止/禁止强行通行，绿灯后展示通行提示。',
         cue='原片约26～28秒灯色变化；先确认对应车道的信号灯。'),
    dict(id='02_dashed_lane_change',title='清晰虚线 · 变道请求',requirement='测试方式(2)',
         source=str(LOCAL/'video_20260929_112507.mp4'),start=3.,end=12.,priority='主演示',
         observation='白天直线路段，本车两侧可见白色虚线，画面较稳定。',
         operation='等待边界确认后按A或D提交变道请求，展示虚线请求反馈。',
         cue='虚线只满足边界条件；单路前视不能证明侧后方安全。'),
    dict(id='03_solid_line_warning',title='双黄实线 · 越线请求报警',requirement='测试方式(2)',
         source=str(LOCAL/'video_20260929_113539.mp4'),start=0.,end=10.,priority='主演示',
         observation='道路中心双黄实线清晰，含施工与会车环境。',
         operation='先确认左边界为实线，再按A模拟向左越线请求，展示禁止/报警。',
         cue='这是实线越线报警素材，不是允许驶入对向车道的演示。'),
    dict(id='04_pedestrian_crossing',title='标准行人横穿 · 冻结路口预演',requirement='测试方式(4)',
         source=str(PIE/'set01/video_0004.mp4'),start=42.,end=63.,priority='主演示',
         observation='用户已确认原片约49～53秒的横穿关系；用PIE官方行为标注辅助定位。',
         operation='先连续播放积累运动历史；原片约53秒暂停，KEY5/6/7选路线，N分析，G推演礼让。',
         cue='导出片约11秒对应原片53秒；路线需与横穿者实际位置对应。'),
    dict(id='05_green_arrow',title='绿色箭头灯 · 路口方向选择',requirement='测试方式(1)',
         source=str(LOCAL/'video_20260929_112507.mp4'),start=7.,end=15.,priority='补充演示',
         observation='用户已给出的原片9～13秒箭头绿灯区间，含车道箭头。',
         operation='展示箭头灯目标及绿灯颜色识别，使用KEY5/6/7切换模拟路线。',
         cue='现模型识别灯色，不把灯色识别宣称为已可靠识别箭头朝向及车道归属。'),
    dict(id='06_night_enhancement',title='真实夜间道路 · FPGA暗光增强',requirement='图像采集处理(2) / 场景(1)',
         source=str(WAYMO/'perception_15272375112495403395_620_000_640_000/front.mp4'),
         start=0.,end=19.5,priority='主演示',
         observation='原始夜间场景，可见标线、车灯和路口；没有人工压暗。',
         operation='用KEY1切换原彩色/暗光增强，用KEY2/3调阈值，展示处理效果。',
         cue='用于图像处理展示；夜间自动驾驶识别效果另行实机验证。'),
    dict(id='07_roadside_person',title='路边行人 · 不一见人就报警',requirement='测试方式(4)',
         source=str(PIE/'set01/video_0001.mp4'),start=31.,end=43.,priority='补充演示',
         observation='官方标注目标含尚未横穿及与任务无关的轨迹；不是全片无人负例。',
         operation='连续播放，展示行人可见而不直接等同于当前路径冲突；保持未知提示的真实性。',
         cue='此段不能据轨迹级not-crossing标签直接宣称任何时刻都可通行。'),
    dict(id='08_complex_junction',title='复杂路口 · 行人与骑行者混行',requirement='测试方式(4)',
         source=str(LOCAL/'video_20260929_113539.mp4'),start=60.,end=74.,priority='挑战备选',
         observation='转向时含多辆骑行者，原片70～71秒有近处步行者。',
         operation='作为泛化和未知场景展示；必要时暂停进行空间分析，说明遮挡/类型混淆。',
         cue='不是主稳定性案例，也不能用此开发素材报告独立准确率。'),
    dict(id='09_three_inputs',title='同场景三视角 · 并行传输展示',requirement='图像采集处理(1)(3)',
         source=str(WAYMO/'perception_10980133015080705026_780_000_800_000/front.mp4'),
         start=0.,end=18.,priority='主演示',
         extra_sources={'left_reference':str(WAYMO/'perception_10980133015080705026_780_000_800_000/side_left.mp4'),
                        'ethernet_right':str(WAYMO/'perception_10980133015080705026_780_000_800_000/side_right.mp4')},
         observation='同一Waymo片段的前/左侧/右侧画面，保留三路独立输入链路。',
         operation='front走HDMI，ethernet_right走以太网；手机播放left_reference给OV5640拍摄。',
         cue='OV5640仍为拍屏和手动对齐；本案例只证明三路并行传输，不宣称硬件同步或全景几何融合。'),
]


def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def contact_sheet(case,folder):
    cap=cv2.VideoCapture(case['source'])
    if not cap.isOpened():raise ValueError('Cannot open '+case['source'])
    start,end=case['start'],case['end']
    canvas=np.full((2*300,3*480,3),(29,20,13),np.uint8)
    observations=[]
    try:
        for i,at in enumerate(np.linspace(start+.1,end-.2,6)):
            cap.set(cv2.CAP_PROP_POS_MSEC,float(at)*1000)
            ok,frame=cap.read()
            if not ok:raise ValueError('Cannot decode frame at '+str(at))
            actual=cap.get(cv2.CAP_PROP_POS_MSEC)/1000
            scale=min(480/frame.shape[1],270/frame.shape[0])
            small=cv2.resize(frame,(round(frame.shape[1]*scale),round(frame.shape[0]*scale)))
            x=(i%3)*480+(480-small.shape[1])//2;y=(i//3)*300
            canvas[y:y+small.shape[0],x:x+small.shape[1]]=small
            cv2.putText(canvas,'source %.2fs / clip %.2fs'%(actual,actual-start),
                        ((i%3)*480+9,y+291),cv2.FONT_HERSHEY_SIMPLEX,.52,(220,220,220),1,cv2.LINE_AA)
            observations.append(dict(requested_source_seconds=float(at),decoded_source_seconds=actual))
    finally:cap.release()
    path=folder/'review.jpg'
    if not cv2.imwrite(str(path),canvas):raise OSError(str(path))
    return observations


def verify_video(path):
    cap=cv2.VideoCapture(str(path));frames=0;first=last=None
    if not cap.isOpened():raise ValueError('Export cannot open: '+str(path))
    width,height,fps=(cap.get(prop) for prop in (3,4,5))
    try:
        while True:
            ok,_=cap.read()
            if not ok:break
            stamp=cap.get(cv2.CAP_PROP_POS_MSEC)/1000
            if first is None:first=stamp
            last=stamp;frames+=1
    finally:cap.release()
    if frames<2:raise ValueError('Empty clip '+str(path))
    return dict(width=int(width),height=int(height),nominal_fps=fps,decoded_frames=frames,
                first_pts_seconds=first,last_pts_seconds=last,bytes=path.stat().st_size,sha256=sha256(path))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'demo_cases_20261102')
    parser.add_argument('--review-only',action='store_true')
    parser.add_argument('--ffmpeg',type=Path)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    if not args.review_only and args.ffmpeg is None:
        import imageio_ffmpeg
        args.ffmpeg=Path(imageio_ffmpeg.get_ffmpeg_exe())
    result=[];hashes={}
    for case in CASES:
        print('CASE_START',case['id'],flush=True)
        entry=dict(case);folder=args.output/case['id'];folder.mkdir(exist_ok=True)
        entry['review_samples']=contact_sheet(case,folder)
        entry['review_scope']='six source frames; scene selection, not model/board acceptance'
        entry['exports']={}
        if not args.review_only:
            sources={'front':case['source'],**case.get('extra_sources',{})}
            for role,source in sources.items():
                source_path=Path(source)
                if source not in hashes:hashes[source]=sha256(source_path)
                output=folder/(role+'.mp4')
                command=[str(args.ffmpeg),'-hide_banner','-loglevel','error','-nostdin','-y',
                         '-ss',str(case['start']),'-i',source,'-t',str(case['end']-case['start']),
                         '-map','0:v:0','-an','-fps_mode','passthrough','-c:v','libx264',
                         '-preset','veryfast','-crf','19','-pix_fmt','yuv420p','-threads','4',
                         '-movflags','+faststart',str(output)]
                subprocess.run(command,check=True)
                record=verify_video(output)
                if abs(record['last_pts_seconds']-(case['end']-case['start']))>.5:
                    raise ValueError('Export duration mismatch '+str(output))
                record.update(file=str(output.relative_to(args.output)).replace('\\','/'),
                              source=source,source_sha256=hashes[source],command=command)
                entry['exports'][role]=record
                print('EXPORTED',case['id'],role,record['decoded_frames'],flush=True)
        result.append(entry)
    payload=dict(version=1,created_date='2026-11-02',purpose='curated development/demo material; not independent evaluation',
                 video_processing='accurate time cut and H.264 encode; original resolution and media timing; no detection or simulated labels burned in',
                 board_verified=False,cases=result)
    (args.output/'manifest.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    sections=[]
    for case in result:
        videos=''.join('<p>%s</p><video controls preload="none" src="%s"></video>'%(
            html.escape(role),html.escape(data['file'])) for role,data in case['exports'].items())
        sections.append('<article><h2>%s · %s</h2><p>%s / %s / 原片 %.2f–%.2f 秒</p><p>%s</p><p>操作：%s</p><p>说明：%s</p><img src="%s/review.jpg">%s</article>'%(
            case['id'],html.escape(case['title']),case['priority'],case['requirement'],case['start'],case['end'],
            html.escape(case['observation']),html.escape(case['operation']),html.escape(case['cue']),case['id'],videos))
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>赛题演示素材</title><style>body{background:#0b1220;color:#dce5f5;font:16px system-ui;max-width:1280px;margin:30px auto;padding:20px}article{background:#131f32;border:1px solid #2b3b54;border-radius:12px;padding:24px;margin:24px 0}img{width:100%}video{width:70%;max-height:480px}p{line-height:1.7;color:#b7c6da}h1,h2{color:#f0f5ff}</style><h1>赛题演示素材 · 2026-11-02</h1><p>原片裁切，不含预设检测框或强制放行逻辑。为开发与演示素材，未作板端通过率认证。</p>'+''.join(sections)+'</html>'
    (args.output/'index.html').write_text(page,encoding='utf-8')
    print('CASE_REVIEW_PASS' if args.review_only else 'CASE_EXPORT_PASS',args.output,flush=True)


if __name__=='__main__':main()
