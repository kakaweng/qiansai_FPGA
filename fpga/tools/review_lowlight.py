"""Export same-frame integer-reference comparisons, explicitly separate from board capture."""
from pathlib import Path
import hashlib
import html
import json
import subprocess
import cv2
import imageio_ffmpeg
import numpy as np
from PIL import Image,ImageDraw,ImageFont
from lowlight_reference import enhance,legacy

ROOT=Path(__file__).resolve().parents[1]
HOST=ROOT.parent/'09_pcie_test/pcie_host'
OUTPUT=ROOT/'lowlight_review_20261102'
SOURCE=HOST/'demo_cases_20261102/06_night_enhancement/front.mp4'
REVIEW_FONT=Path('C:/Windows/Fonts/msyh.ttc')
if not REVIEW_FONT.is_file():REVIEW_FONT=HOST/'preview_fonts/PangoPreviewSans.ttf'
FONT=ImageFont.truetype(str(REVIEW_FONT),22)


def label(rgb,text):
    image=Image.fromarray(rgb);draw=ImageDraw.Draw(image)
    draw.rectangle((0,0,image.width,38),fill=(10,18,28));draw.text((12,4),text,font=FONT,fill=(225,235,242))
    return np.asarray(image)


def main():
    OUTPUT.mkdir(exist_ok=True)
    cap=cv2.VideoCapture(str(SOURCE))
    if not cap.isOpened():raise SystemExit('source unavailable')
    fps=cap.get(cv2.CAP_PROP_FPS);size=(int(cap.get(3)),int(cap.get(4)))
    tile_size=(640,round(size[1]*640/size[0]/2)*2)
    samples=[]
    for second in (1.,7.,14.):
        cap.set(cv2.CAP_PROP_POS_MSEC,second*1000);ok,bgr=cap.read()
        if not ok:raise SystemExit('sample unavailable')
        raw=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
        variants={'raw':raw,'legacy':legacy(raw),'weak':enhance(raw,0),'medium':enhance(raw,1),'strong':enhance(raw,2)}
        for name,rgb in variants.items():Image.fromarray(rgb).save(OUTPUT/('frame_%02d_%s.png'%(second,name)))
        tiles=[label(cv2.resize(variants[name],tile_size),title) for name,title in
               (('raw','原始输入'),('legacy','旧算法：4× / 2× / 1.25×'),('medium','新算法：LOW 2 中档'),('strong','新算法：LOW 3 强档'))]
        sheet=np.concatenate((np.concatenate(tiles[:2],axis=1),np.concatenate(tiles[2:],axis=1)),axis=0)
        Image.fromarray(sheet).save(OUTPUT/('comparison_%02d.png'%second))
        samples.append(dict(seconds=second,clipped_channels={name:float((rgb==255).mean()) for name,rgb in variants.items()}))
    cap.set(cv2.CAP_PROP_POS_FRAMES,0)
    output=OUTPUT/'night_raw_legacy_v2.mp4'
    ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
    command=[ffmpeg,'-hide_banner','-loglevel','error','-y','-f','rawvideo','-pixel_format','rgb24',
             '-video_size','1920x%d'%tile_size[1],'-framerate',str(fps),'-i','pipe:0','-an','-c:v','libx264',
             '-preset','veryfast','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',str(output)]
    process=subprocess.Popen(command,stdin=subprocess.PIPE)
    frames=0
    try:
        while True:
            ok,bgr=cap.read()
            if not ok:break
            raw=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
            # Algorithm at original resolution, then resize for a compact comparison.
            tiles=[label(cv2.resize(rgb,tile_size,interpolation=cv2.INTER_AREA),title) for rgb,title in
                   ((raw,'原始输入'),(legacy(raw),'旧算法'),(enhance(raw,1),'新算法：LOW 2'))]
            process.stdin.write(np.concatenate(tiles,axis=1).tobytes());frames+=1
    finally:
        cap.release();process.stdin.close()
    if process.wait()!=0:raise SystemExit('comparison encoder failed')
    metadata=dict(version='lowlight-v2',source=str(SOURCE),source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                  scope='Integer reference matched to RTL; not FPGA output capture; no model accuracy claim',
                  original_resolution=size,fps=fps,frames=frames,samples=samples,
                  video=str(output),video_sha256=hashlib.sha256(output.read_bytes()).hexdigest())
    (OUTPUT/'review.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    page='''<!doctype html><meta charset="utf-8"><title>暗光增强 v2 同帧对照</title>
<style>body{margin:32px auto;max-width:1400px;background:#101923;color:#e3edf6;font:16px system-ui}h1{font-size:28px}p{line-height:1.7;color:#adbecd}video,img{width:100%;border-radius:8px}section{margin:30px 0}a{color:#6cdfe8}button{background:#263c50;color:#e3edf6;border:1px solid #456;padding:10px;cursor:pointer}</style>
<h1>暗光增强 v2 · 同帧算法对照</h1>
<p>此页为与 RTL 逐像素核对的整数参考输出，不是 FPGA 上板采集。原图、旧算法和新算法取自同一帧；不增加检测框、不补画道路细节。新算法采用统一 RGB 增益、连续曲线、高光保护与水平保边降噪，黑场保持为黑色。</p>
<video controls src="night_raw_legacy_v2.mp4"></video><p>视频：左为原图，中为旧算法，右为新算法中档。源素材约10 FPS，保持原始内容帧率。</p>'''
    for second in (1,7,14):
        page+='<section><h2>片内 %d 秒</h2><img src="comparison_%02d.png"><p>左上：原图；右上：旧算法；左下：新中档；右下：新强档。</p>'%(second,second)
        page+='<p>'+ ' · '.join('<a href="frame_%02d_%s.png">%s 全尺寸</a>'%(second,name,title) for name,title in
                                (('raw','原图'),('legacy','旧算法'),('weak','新弱档'),('medium','新中档'),('strong','新强档')))+'</p></section>'
    (OUTPUT/'index.html').write_text(page,encoding='utf-8')
    print('LOWLIGHT_REVIEW_PASS',json.dumps(metadata,ensure_ascii=False))


if __name__=='__main__':main()
