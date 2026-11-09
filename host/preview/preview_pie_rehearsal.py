"""Create a frozen real-perception scene with interactive 3-second route rehearsals."""
import argparse
import base64
from collections import deque
import json
from pathlib import Path
import sys
import time
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT/'training'))
from pie_behavior import BehaviorPredictor
from live_road import GroundProjection,RoadTracker
from crosswalk_relations import RelativeFootMotion
from preview_live_road import replay_snapshot
from windows_object_detector import WindowsObjectDetector,sha256_file
from validate_crosswalk_video import CrosswalkONNX,world_for_objects
from validate_crosswalk_relations import LaneSource,person_records
from junction_rehearsal import analyze,motion_from_compensation
from crosswalk_validation import exclusion_mask


def build_scene(video,seconds,detector,behavior,lane,crosswalk,projection,allowed):
    cap=cv2.VideoCapture(str(video));fps=cap.get(cv2.CAP_PROP_FPS)
    if not cap.isOpened() or fps<=0:raise ValueError('Cannot open input video')
    if not np.isfinite(seconds) or seconds<2 or seconds>=cap.get(cv2.CAP_PROP_FRAME_COUNT)/fps:raise ValueError('Snapshot must be between 2 seconds and video end')
    tracker=RoadTracker(projection);motion=RelativeFootMotion();history={};records=[];lane.reset()
    end=round(seconds*fps);step=round(fps/5);frames=list(range(end-10*step,end+1,step))
    try:
        for index,f in enumerate(frames):
            cap.set(cv2.CAP_PROP_POS_FRAMES,f);ok,image=cap.read()
            if not ok or round(cap.get(cv2.CAP_PROP_POS_FRAMES))-1!=f:raise OSError('Input frame alignment failed')
            now=f/fps;detections=detector.detect(image)
            world=world_for_objects(detections,image.shape,index+1,now,allowed)
            tracker.update(replay_snapshot(world,now),True,now,video_ms=round(now*1000))
            fresh=[t for t in tracker.objects if not t['held']]
            people={p['id']:p for p in person_records(tracker,now)}
            targets=[dict(id=t['id'],box=t['raw'].tolist(),held=False,age=0,rider_ambiguous=people.get(t['id'],{}).get('rider_ambiguous',False)) for t in fresh]
            gray=cv2.cvtColor(cv2.resize(image,(640,360)),cv2.COLOR_BGR2GRAY)
            movement=motion.update(gray,targets,[t['raw'].tolist() for t in fresh],now)
            peds=[t for t in fresh if t['cls']==0 and not people[t['id']]['rider_ambiguous']]
            embeddings=behavior.embed(image,[t['raw'].tolist() for t in peds]);current_ids={t['id'] for t in peds}
            history={k:v for k,v in history.items() if k in current_ids}
            for t,feature in zip(peds,embeddings):
                seq=history.setdefault(t['id'],deque(maxlen=6))
                if seq and abs(now-seq[-1]['time']-.2)>.04:seq.clear()
                seq.append(dict(time=now,box=t['raw'].copy(),feature=feature))
            # Warm the original lane temporal decoder on this same observation sequence.
            lanes=lane.infer(image,index+1,now)
            records.append(dict(frame=f,seconds=now,detections=detections,tracks=[dict(id=t['id'],class_id=t['cls'],box=t['raw'].tolist(),held=t['held']) for t in tracker.objects]))
        actors=[];omitted=[];boxes=[];scores=[]
        for t in fresh:
            b=t['raw'].tolist();identity=t['id'];m=movement[identity]
            m=dict(m,window_seconds=len(motion.history.get(identity,{}).get('steps',[]))/5)
            pose=motion_from_compensation(b,m,projection)
            forecast=None
            if identity in history and len(history[identity])==6:
                seq=list(history[identity])
                forecast=behavior.predict([s['feature'] for s in seq],[s['box'] for s in seq],[s['time'] for s in seq])
            box=dict(id=identity,class_id=t['cls'],box=b,score=t['score']/10000,behavior=forecast)
            boxes.append(box)
            if t['cls']==0:scores.append(dict(id=identity,forecast=forecast))
            if pose is None or b[3]>=.995:
                omitted.append(identity);continue
            if people.get(identity,{}).get('rider_ambiguous'):
                pose.update(velocity=[0,0],motion_known=False)
            actors.append(dict(pose,id=identity,class_id=t['cls'],behavior=forecast,source='actual_yolo_track',motion_source='short_background_compensated_assumption'))
        projected_lanes=[]
        for l in lanes:
            p,valid=projection.project(l['points'])
            # Preserve segments rather than joining across rejected points.
            segment=[]
            for pt,good in zip(p,valid):
                if good:segment.append(pt.tolist())
                else:
                    if len(segment)>=2:projected_lanes.append(dict(style=l['style'],points=segment))
                    segment=[]
            if len(segment)>=2:projected_lanes.append(dict(style=l['style'],points=segment))
        probability=crosswalk.infer(image,(640,360));mask=(probability>=.65)&allowed
        contours,_=cv2.findContours(mask.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        zebra=[];image_zebra=[]
        for c in contours:
            if cv2.contourArea(c)<25:continue
            c=cv2.approxPolyDP(c,2,True).reshape(-1,2)/[639,359]
            image_zebra.append(c.tolist());p,valid=projection.project(c)
            if valid.all() and len(p)>=3:zebra.append(p.tolist())
        small=cv2.resize(image,(960,round(image.shape[0]*960/image.shape[1])))
        ok,jpg=cv2.imencode('.jpg',small,[cv2.IMWRITE_JPEG_QUALITY,88])
        if not ok:raise OSError('Snapshot encoding failed')
        result=dict(name=video.parent.name+'/'+video.name,time=now,source_frame=end,image='data:image/jpeg;base64,'+base64.b64encode(jpg).decode(),
                    image_width=image.shape[1],image_height=image.shape[0],boxes=boxes,actors=actors,omitted_ids=omitted,
                    image_lanes=lanes,lanes=projected_lanes,zebra=zebra,image_zebra=image_zebra,behavior_scores=scores,
                    routes={d:analyze(d,actors) for d in ('left','straight','right')},
                    projection='manual_relative' if projection.calibrated else 'default_relative',
                    scope='frozen_observation_plus_user_route_assumptions_not_counterfactual_ground_truth',driving_permission=False)
        return result,records
    finally:cap.release()


def main():
    p=argparse.ArgumentParser(description='冻结真实识别画面，生成可交互的2.5D路线预演；不连接板端')
    p.add_argument('--video',type=Path);p.add_argument('--at',type=float,default=193.6)
    p.add_argument('--model',type=Path,default=ROOT/'runs/pie_behavior/temporal_v1_20261101')
    p.add_argument('--output',type=Path,default=ROOT/'ui_preview_pie_rehearsal_v2');p.add_argument('--projection',type=Path)
    p.add_argument('--profile',choices=['none','pie'],default='none');p.add_argument('--open',action='store_true');a=p.parse_args()
    if a.output.exists():raise ValueError('Use a new output directory to preserve previous evidence')
    a.output.mkdir(parents=True);cv2.setNumThreads(4)
    scenes=[(a.video,a.at)] if a.video else [(Path('F:/waymo_data/pie/set02')/v,t) for v,t in [('video_0001.mp4',407.8),('video_0001.mp4',523.4),('video_0002.mp4',193.6),('video_0002.mp4',285.4),('video_0003.mp4',39.6),('video_0003.mp4',47.8)]]
    config=json.loads((ROOT/'training/crosswalk_video_cases.json').read_text(encoding='utf-8'))
    profile='pie' if not a.video else a.profile
    allowed=exclusion_mask((360,640),config['profiles']['pie']['exclusions']) if profile=='pie' else np.ones((360,640),bool)
    detector=WindowsObjectDetector();behavior=BehaviorPredictor(a.model);lane=LaneSource()
    crosswalk=CrosswalkONNX(ROOT/'runs/crosswalk/mapillary_lraspp_v1_20261101/crosswalk.onnx')
    projection_path=a.projection or (ROOT/'pie_rehearsal_projection.json' if profile=='pie' else None)
    projection=GroundProjection.load(projection_path) if projection_path else GroundProjection()
    results=[];began=time.perf_counter()
    try:
        for i,(video,t) in enumerate(scenes):
            scene,records=build_scene(video,t,detector,behavior,lane,crosswalk,projection,allowed);results.append(scene)
            (a.output/('observations_%d.json'%i)).write_text(json.dumps(records,ensure_ascii=False),encoding='utf-8')
            print('REHEARSAL_SCENE',i,video.name,t,'actors',len(scene['actors']),'behavior_ready',sum(x['forecast'] is not None for x in scene['behavior_scores']),flush=True)
    finally:lane.close()
    manifest=dict(version=1,scenes=results,model_id=behavior.manifest['model_id'],model_release='experimental_only_excessive_anticipation_false_positives',
                  behavior_horizon_seconds=1,rehearsal_horizon_seconds=3,geometry='relative_not_metric_routes_user_selected_not_reconstructed',
                  projection=dict(path=str(projection_path) if projection_path else None,points=projection.points.tolist(),calibrated=False,
                                  sha256=sha256_file(projection_path) if projection_path else None),
                  source_sha256={str(v):sha256_file(v) for v,_ in scenes},
                  model_sha256=dict(behavior=behavior.manifest['onnx_sha256'],lane=lane.model.model_sha256,crosswalk=sha256_file(crosswalk.path),yolo=sha256_file(detector.model_path)),
                  code_sha256={f:sha256_file(ROOT/f) for f in ['preview_pie_rehearsal.py','pie_behavior.py','junction_rehearsal.py','rehearsal_template.html']},
                  seconds=time.perf_counter()-began,driving_permission=False)
    data=json.dumps(manifest,ensure_ascii=False).replace('</','<\/')
    (a.output/'scene_data.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    template=(ROOT/'rehearsal_template.html').read_text(encoding='utf-8')
    (a.output/'index.html').write_text(template.replace('__SCENE_DATA__',data),encoding='utf-8')
    print('REHEARSAL_PREVIEW_PASS',a.output/'index.html',flush=True)
    if a.open:
        import webbrowser
        webbrowser.open((a.output/'index.html').resolve().as_uri())


if __name__=='__main__':main()
