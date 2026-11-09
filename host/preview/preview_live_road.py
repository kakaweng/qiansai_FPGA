"""Headless/interactive real-video replay for the live map, using existing ONNX models.

Lane curves run through the same C decoder as RK3568. This is desktop validation,
not an RKNN speed/accuracy benchmark. No images or detections are synthesized.
"""
import argparse
import ctypes
import json
from pathlib import Path
import struct
import time
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parent


def unpack_wire(wire):
    head=struct.unpack_from('!4sI7QHH4B',wire)
    _,producer,sent,frame_seq,frame_ms,lane_seq,lane_ms,object_seq,object_ms,w,h,nl,no,flags,_=head
    world=dict(version=1,producer=producer,live=True,frame_seq=frame_seq,frame_ms=frame_ms,width=w,height=h,
               lane_seq=lane_seq,lane_ms=lane_ms,object_seq=object_seq,object_ms=object_ms,
               report_age_ms=0,lane_age_ms=0,object_age_ms=0,lane_valid=bool(flags&1),object_valid=bool(flags&2),
               lanes=[],objects=[])
    at=72
    for _ in range(nl):
        style,role,quality,*points=struct.unpack_from('!BBH24H',wire,at); at+=52
        world['lanes'].append([style,quality,role,[points[i:i+2] for i in range(0,24,2)]])
    for _ in range(no):
        cls,_,score,*box=struct.unpack_from('!BBH4H',wire,at); at+=12
        world['objects'].append([cls,score,*box])
    return world


def replay_snapshot(world,now):
    drive=dict(front_only=True,line=[0,0],stable_line=[0,0],line_held=[False,False],line_age_ms=[-1,-1],
               vehicles=[sum(o[0]!=0 for o in world['objects']),0,0],people=sum(o[0]==0 for o in world['objects']),
               object_age_ms=[0,-1,-1],object_valid=[True,False,False],lane_age_ms=0,signal=0,request=0,
               feedback='IDLE',solid_alarm=False,speed_kmh=0,gear='P')
    return dict(connected=True,received_at=now,notice='',revision=1,
                state=dict(state='RUNNING',session=1,source='HDMI',drive=drive,world=world))


class ReplayClient:
    def submit(self,action): return False


def main():
    from windows_lane_style import WindowsLaneStyleSegmenter,preprocess_frame
    from windows_object_detector import WindowsObjectDetector
    from front_live_preview import LiveDashboard
    parser=argparse.ArgumentParser(description='真实视频2.5D离线验证，复用ONNX模型与板端C曲线解码器')
    parser.add_argument('--video',required=True,type=Path)
    parser.add_argument('--start',type=float,default=0)
    parser.add_argument('--seconds',type=float,default=12)
    parser.add_argument('--sample-fps',type=float,default=4)
    parser.add_argument('--output',type=Path,default=ROOT/'ui_preview_live_road'/'replay')
    parser.add_argument('--bridge',type=Path,default=ROOT/'rk3568_front_p1_deploy'/'host'/'live_road_bridge.dll')
    parser.add_argument('--show',action='store_true')
    args=parser.parse_args()
    if not 1<=args.sample_fps<=10 or args.seconds<=0 or args.start<0: parser.error('invalid replay duration/rate')
    args.output.mkdir(parents=True,exist_ok=True)
    cap=cv2.VideoCapture(str(args.video))
    if not cap.isOpened(): raise OSError('无法打开视频：'+str(args.video))
    lane=WindowsLaneStyleSegmenter(backend='cpu'); detector=WindowsObjectDetector(backend='cpu')
    lib=ctypes.CDLL(str(args.bridge.resolve())); lib.world_create.restype=ctypes.c_void_p
    lib.world_destroy.argtypes=[ctypes.c_void_p]
    fp=ctypes.POINTER(ctypes.c_float)
    lib.world_process.argtypes=[ctypes.c_void_p,fp,fp,ctypes.c_uint,ctypes.c_uint64,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_size_t]
    lib.world_process.restype=ctypes.c_size_t
    core=lib.world_create(); wire=ctypes.create_string_buffer(1200)
    if not core: raise MemoryError('decoder allocation failed')
    panel=LiveDashboard(ReplayClient(),projection_path=args.output/'projection.json')
    writer=cv2.VideoWriter(str(args.output/'replay.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),12,(1600,900))
    if not writer.isOpened(): raise OSError('无法创建录制视频')
    rows=[]; observations=[]; current=None; board=None; last_sample=-1; max_render=0; render_times=[]
    count=round(args.seconds*12)
    try:
        for index in range(count):
            t=index/12; cap.set(cv2.CAP_PROP_POS_MSEC,(args.start+t)*1000)
            ok,frame=cap.read()
            if not ok: break
            h,w=frame.shape[:2]; scale=min(1920/w,1080/h)
            sw,sh=round(w*scale),round(h*scale); x,y=(1920-sw)//2,(1080-sh)//2
            hdmi=np.zeros((1080,1920,3),np.uint8); hdmi[y:y+sh,x:x+sw]=cv2.resize(frame,(sw,sh))
            now=100+t
            sample=int(t*args.sample_fps+1e-6)
            if sample!=last_sample:
                started=time.perf_counter(); tensor,_=preprocess_frame(hdmi,np)
                logits=np.ascontiguousarray(lane.session.run(['lane_style_logits'],{lane.input_name:tensor})[0])
                detected=detector.detect(hdmi)
                boxes=np.array([[d['class_id'],d['score'],*d['box']] for d in detected],dtype=np.float32).reshape(-1,6)
                size=lib.world_process(core,logits.ctypes.data_as(fp),boxes.ctypes.data_as(fp),len(boxes),sample+1,round(now*1000),wire,len(wire))
                if not size: raise RuntimeError('C decoder rejected inference')
                world=unpack_wire(wire.raw[:size]); current=replay_snapshot(world,now)
                observations.append(dict(time_seconds=round(args.start+t,3),world=world))
                rows.append(dict(time_seconds=round(args.start+t,3),lanes=len(world['lanes']),objects=len(world['objects']),
                                 inference_decode_ms=round((time.perf_counter()-started)*1000,2)))
                last_sample=sample
            annotated=hdmi.copy()
            for style,quality,role,points in current['state']['world']['lanes']:
                p=np.int32(np.array(points)*[1920/10000,1080/10000]); color={0:(140,140,140),1:(70,170,250),2:(190,220,60)}[style]
                cv2.polylines(annotated,[p],False,color,3,cv2.LINE_AA)
            for cls,score,l,top,r,b in current['state']['world']['objects']:
                a=(round(l*1920/10000),round(top*1080/10000)); z=(round(r*1920/10000),round(b*1080/10000))
                cv2.rectangle(annotated,a,z,(200,140,230) if cls==0 else (200,200,80),2)
            board=np.zeros((480,1280,3),np.uint8); board[:,213:1066]=cv2.resize(annotated,(853,480))
            started=time.perf_counter(); canvas=panel.compose(board,True,current,now,video_ms=round(now*1000))
            ms=(time.perf_counter()-started)*1000; max_render=max(max_render,ms); render_times.append(ms)
            patch=panel._patch('离线实视频 · ONNX 推理验证',16,'#fff7ed','#9a3412'); panel.canvas_patch(canvas,patch,640,8)
            writer.write(canvas)
            if index in (0,count//3,count*2//3,count-1): cv2.imwrite(str(args.output/('frame_%04d.png'%index)),canvas)
            if index%24==0: print('REPLAY %.1fs lanes=%d objects=%d'%(t,len(panel.world_view['lanes']),len(panel.world_view['objects'])),flush=True)
            if args.show:
                cv2.imshow('Live road - OFFLINE actual ONNX inference',canvas)
                if cv2.waitKey(1)&0xff in (27,ord('q')): break
    finally:
        lib.world_destroy(core); cap.release(); writer.release()
        if args.show: cv2.destroyAllWindows()
    summary=dict(video=str(args.video),desktop_only=True,model=lane.manifest['model_version'],
                 frames=len(render_times),samples=len(rows),render_mean_ms=float(np.mean(render_times)) if render_times else 0,
                 render_max_ms=max_render,observations=rows)
    (args.output/'report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (args.output/'observations.json').write_text(json.dumps(observations,ensure_ascii=False),encoding='utf-8')
    print('LIVE_ROAD_REPLAY_PASS',args.output,flush=True)


if __name__=='__main__': main()
