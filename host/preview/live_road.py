"""Display-only, ego-fixed road mapping. Coordinates are relative, never metres.

Freshness follows capture timestamps, not repeated HTTP polls. Image-space
association and bounded easing add no inference load and grant no permissions.
"""
import json
import math
from pathlib import Path
import cv2
import numpy as np

MAX_AGE = 1.2
OBJECT_HOLD = .65
LANE_HOLD = .85
CLASS_NAMES = {0: '行人', 1: '骑行', 2: '车辆', 3: '骑行', 5: '客车', 7: '货车'}


def _number(v):
    return type(v) in (int, float) and math.isfinite(v)


class GroundProjection:
    """Four-point flat-ground homography with explicit ROI and no metric claim."""
    def __init__(self, roi=None, points=None, calibrated=False):
        self.roi = np.array(roi if roi is not None else [0, 0, 1, 1], dtype=float)
        if self.roi.shape != (4,) or not np.isfinite(self.roi).all() or np.any(self.roi < 0) or np.any(self.roi > 1):
            raise ValueError('视频区域坐标无效')
        a,b,c,d = self.roi
        if c-a < .2 or d-b < .2:
            raise ValueError('视频区域过小，请框选完整道路视频')
        default = np.array([[.43,.55],[.57,.55],[.85,.95],[.15,.95]])
        self.points = np.array(points if points is not None else default*[c-a,d-b]+[a,b], dtype=np.float32)
        p = self.points
        if p.shape != (4,2) or not np.isfinite(p).all() or np.any(p < [a,b]) or np.any(p > [c,d]):
            raise ValueError('四个地面点须位于视频有效区域内')
        if not cv2.isContourConvex(p) or cv2.contourArea(p) < .015*(c-a)*(d-b):
            raise ValueError('四点应依次为远左、远右、近右、近左')
        if p[0,0]>=p[1,0] or p[3,0]>=p[2,0] or min(p[2:,1])-max(p[:2,1]) < .12*(d-b):
            raise ValueError('请在一段直路的同一车道上选远端和近端')
        if p[2,0]-p[3,0] <= (p[1,0]-p[0,0])*1.05:
            raise ValueError('近端车道宽度应大于远端宽度')
        self.matrix = cv2.getPerspectiveTransform(p, np.float32([[-.5,6],[.5,6],[.5,0],[-.5,0]]))
        # Homographies are equivalent up to scale; orient the valid ground half-plane.
        if float(self.matrix[2] @ np.r_[p.mean(axis=0),1]) < 0:
            self.matrix *= -1
        self.calibrated = bool(calibrated)

    def project(self, points):
        p = np.asarray(points, dtype=float).reshape(-1,2)
        a,b,c,d = self.roi
        in_roi = (p[:,0]>=a)&(p[:,0]<=c)&(p[:,1]>=b)&(p[:,1]<=d)
        h = np.column_stack((p,np.ones(len(p)))) @ self.matrix.T
        valid = in_roi & np.isfinite(h).all(axis=1) & (h[:,2]>1e-6)
        out = np.full((len(p),2),np.nan)
        out[valid] = h[valid,:2]/h[valid,2:]
        # Older NumPy warns when comparing the intentional NaN placeholders.
        # Compare only projected ground points; leave all rejected points invalid.
        ground = out[valid]
        valid[valid] = (ground[:,1]>=-.25)&(ground[:,1]<=7)&(np.abs(ground[:,0])<=3.3)
        return out, valid

    def save(self, path):
        path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
        data=dict(version=1,roi=self.roi.tolist(),points=self.points.tolist(),calibrated=self.calibrated)
        temp=path.with_suffix('.tmp'); temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(path)

    @classmethod
    def load(cls,path):
        data=json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(data,dict) or data.get('version')!=1: raise ValueError('标定文件版本不支持')
        return cls(data['roi'],data['points'],data.get('calibrated',False))


def validate_world(raw):
    if not isinstance(raw,dict) or raw.get('version')!=1: return None
    integer_fields=('producer','width','height','frame_seq','frame_ms','lane_seq','lane_ms','object_seq','object_ms')
    if any(type(raw.get(k)) is not int or not 0<=raw[k]<=2**64-1 for k in integer_fields): return None
    if (raw['width'],raw['height'])!=(1920,1080): return None
    if any(type(raw.get(k)) is not bool for k in ('live','lane_valid','object_valid')): return None
    if any(not _number(raw.get(k)) or not -1<=raw[k]<=100000 for k in ('report_age_ms','lane_age_ms','object_age_ms')): return None
    lanes,objects=raw.get('lanes'),raw.get('objects')
    if not isinstance(lanes,list) or len(lanes)>12 or not isinstance(objects,list) or len(objects)>40: return None
    for l in lanes:
        if not isinstance(l,list) or len(l)!=4 or any(type(x) is not int for x in l[:3]): return None
        if l[0] not in (0,1,2) or not 0<=l[1]<=10000 or l[2] not in (0,1,2): return None
        if not isinstance(l[3],list) or len(l[3])!=12: return None
        if any(not isinstance(p,list) or len(p)!=2 or any(type(x) is not int or not 0<=x<=10000 for x in p) for p in l[3]): return None
    for o in objects:
        if not isinstance(o,list) or len(o)!=6 or any(type(x) is not int or not 0<=x<=10000 for x in o): return None
        if o[0] not in CLASS_NAMES or o[2]>=o[4] or o[3]>=o[5]: return None
    return raw


def _family(cls):
    return 0 if cls==0 else 1 if cls in (1,3) else 2


def observed_lane_bands(lanes):
    """Count only adjacent observed strips, never the total lanes of a road."""
    boundaries=[]
    for lane in lanes:
        if lane.get('held') or lane.get('alpha',0)<.7 or lane.get('quality',1)<.35:continue
        points=np.asarray(lane.get('points'),dtype=float);mask=np.asarray(lane.get('mask'),dtype=bool)
        if points.ndim!=2 or points.shape[1]!=2 or mask.shape!=(len(points),):continue
        valid=mask&np.isfinite(points).all(axis=1)
        segments=[];start=None
        for index,present in enumerate(np.r_[valid,False]):
            if present and start is None:start=index
            if not present and start is not None:
                segment=points[start:index];start=None
                if len(segment)<3:continue
                difference=np.diff(segment[:,1])
                if not (np.all(difference>1e-5) or np.all(difference<-1e-5)):continue
                if difference[0]<0:segment=segment[::-1]
                if segment[-1,1]-segment[0,1]>=1.2:segments.append(segment)
        if not segments:continue
        segment=max(segments,key=lambda segment:segment[-1,1]-segment[0,1])
        boundaries.append(dict(id=lane.get('id'),points=segment,role=lane.get('role',0)))

    def shared(first,second):
        left,right=first['points'],second['points']
        low=max(left[0,1],right[0,1]);high=min(left[-1,1],right[-1,1])
        if high-low<1.2:return None
        forward=np.linspace(low,high,24)
        return forward,np.interp(forward,left[:,1],left[:,0]),np.interp(forward,right[:,1],right[:,0])

    unique=[]
    for boundary in sorted(boundaries,key=lambda boundary:(not bool(boundary['role']),boundary['id'] or 0)):
        duplicate=False
        for previous in unique:
            overlap=shared(previous,boundary)
            if overlap is not None and np.max(np.abs(overlap[2]-overlap[1]))<=.16:
                duplicate=True;break
        if not duplicate:unique.append(boundary)
    blocked=set()
    for first_index,first in enumerate(unique):
        for second_index in range(first_index+1,len(unique)):
            overlap=shared(first,unique[second_index])
            if overlap is None:continue
            width=overlap[2]-overlap[1]
            if np.min(width)<=0<=np.max(width) and np.max(np.abs(width))>.16:
                blocked.update((first_index,second_index))
    ordered=sorted(enumerate(unique),key=lambda item:float(np.median(item[1]['points'][:,0])))
    bands=[]
    for first,second in zip(ordered,ordered[1:]):
        if first[0] in blocked or second[0] in blocked:continue
        overlap=shared(first[1],second[1])
        if overlap is None:continue
        forward,left,right=overlap;width=right-left
        if np.min(width)<.55 or np.max(width)>1.6 or np.max(width)/np.min(width)>1.5:continue
        obstructed=False
        for other in unique:
            if other is first[1] or other is second[1]:continue
            points=other['points'];present=(forward>=points[0,1])&(forward<=points[-1,1])
            if not present.any():continue
            lateral=np.interp(forward[present],points[:,1],points[:,0])
            if np.any((lateral>left[present]+.16)&(lateral<right[present]-.16)):
                obstructed=True;break
        if obstructed:continue
        polygon=np.column_stack((np.r_[left,right[::-1]],np.r_[forward,forward[::-1]]))
        bands.append(dict(number=len(bands)+1,boundary_ids=(first[1]['id'],second[1]['id']),
                          polygon=polygon,center=(float((left[12]+right[12])/2),float(forward[12])),
                          y_range=(float(forward[0]),float(forward[-1]))))
    if len(bands)>1:
        low=max(band['y_range'][0] for band in bands);high=min(band['y_range'][1] for band in bands)
        adjacent=all(first['boundary_ids'][1]==second['boundary_ids'][0] for first,second in zip(bands,bands[1:]))
        if high-low<1.2 or not adjacent:
            bands=[]
        else:
            forward=np.linspace(low,high,24)
            lookup={boundary['id']:boundary['points'] for boundary in unique}
            for band in bands:
                left_boundary,right_boundary=(lookup[identifier] for identifier in band['boundary_ids'])
                left=np.interp(forward,left_boundary[:,1],left_boundary[:,0])
                right=np.interp(forward,right_boundary[:,1],right_boundary[:,0])
                band.update(polygon=np.column_stack((np.r_[left,right[::-1]],np.r_[forward,forward[::-1]])),
                            center=(float((left[12]+right[12])/2),float(forward[12])),y_range=(float(low),float(high)))
    return dict(status='OBSERVED' if bands else 'UNKNOWN',bands=bands,visible_lane_count=len(bands) if bands else None,
                boundary_count=len(lanes),distinct_boundary_count=len(unique),total_lane_count=None)


def _object_cost(track, detection):
    if _family(track['cls'])!=_family(detection['cls']): return None
    a,b=track['raw'],detection['raw']
    aw,ah=a[2:]-a[:2]; bw,bh=b[2:]-b[:2]
    if max(aw/bw,bw/aw,ah/bh,bh/ah)>2.5: return None
    delta=np.linalg.norm((a[:2]+a[2:]-b[:2]-b[2:])/2)
    gate=min(.13,max(.035,(aw+bw+ah+bh)*.22))
    if delta>gate: return None
    overlap=np.maximum(0,np.minimum(a[2:],b[2:])-np.maximum(a[:2],b[:2])).prod()
    iou=overlap/(aw*ah+bw*bh-overlap)
    return delta/gate+.5*(1-iou)


def _lane_cost(track, detection):
    a,b=track['raw'],detection['raw']
    lo=max(a[0,1],b[0,1]); hi=min(a[-1,1],b[-1,1])
    if hi-lo<.06: return None
    ys=np.linspace(lo,hi,7)
    delta=np.median(np.abs(np.interp(ys,a[:,1],a[:,0])-np.interp(ys,b[:,1],b[:,0])))
    if delta>.045: return None
    return delta/.045


class RoadTracker:
    def __init__(self,projection=None,object_history=False):
        self.projection=projection or GroundProjection()
        self.object_history=object_history
        self.next_id=1; self.reset()

    def _track_limits(self,kind):
        if kind=='object' and self.object_history:return 2.6,2.6
        return (LANE_HOLD if kind=='lane' else OBJECT_HOLD),MAX_AGE

    def reset(self):
        self.key=None; self.last_frame=None; self.last_now=None
        self.sequences={'lane':None,'object':None}; self.lanes=[]; self.objects=[]
        self.status='WAITING'; self.ages=(-1,-1); self.skew=None

    @staticmethod
    def display_shape(track,now):
        factor=max(0.,min(1.,(now-track['updated'])/.12))
        return track['previous']*(1-factor)+track['filtered']*factor

    def _associate(self,tracks,detections,kind,seq,evidence_at,now):
        hold,maximum=self._track_limits(kind)
        tracks[:]=[t for t in tracks if 0<=now-t['updated']<=hold and 0<=now-t['evidence_at']<=maximum]
        cost=_lane_cost if kind=='lane' else _object_cost
        pairs=[]
        for i,t in enumerate(tracks):
            for j,d in enumerate(detections):
                c=cost(t,d)
                if c is not None: pairs.append((c,i,j))
        matched=set(); used=set()
        for _,i,j in sorted(pairs):
            if i in matched or j in used: continue
            matched.add(i); used.add(j); t=tracks[i]; d=detections[j]
            previous=self.display_shape(t,now)
            if kind=='lane':
                # Smooth only within the newly observed extent; never extend a missing road.
                previous=np.column_stack((np.interp(d['raw'][:,1],previous[:,1],previous[:,0]),d['raw'][:,1]))
                lo=max(t['raw'][0,1],d['raw'][0,1]); hi=min(t['raw'][-1,1],d['raw'][-1,1])
                outside=(d['raw'][:,1]<lo)|(d['raw'][:,1]>hi); previous[outside]=d['raw'][outside]
                if d['style']!=t['pending']: t['pending'],t['votes']=d['style'],1
                else: t['votes']+=1
                if d['style']==0 or t['votes']>=2: t['style']=d['style']
                t['role']=d['role']; t['quality']=d['quality']
            else: t['cls'],t['score']=d['cls'],d['score']
            t.update(previous=previous,filtered=previous*.3+d['raw']*.7,raw=d['raw'],updated=now,evidence_at=evidence_at,seq=seq,held=False,
                     samples=min(1000,t.get('samples',1)+1))
        for i,t in enumerate(tracks):
            if i not in matched: t['held']=True
        for j,d in enumerate(detections):
            if j in used: continue
            t=dict(d,id=self.next_id,previous=d['raw'].copy(),filtered=d['raw'].copy(),updated=now,evidence_at=evidence_at,seq=seq,held=False,samples=1)
            if kind=='lane': t.update(pending=d['style'],votes=1)
            self.next_id+=1; tracks.append(t)
        # Strict bounds also cover rapid cuts with many unmatched detections.
        tracks.sort(key=lambda t:(t['held'],-t['updated']))
        del tracks[24 if kind=='lane' else 64:]

    def update(self,snapshot,video_fresh,now,video_ms=None):
        if self.last_now is not None and now<self.last_now: self.reset()
        self.last_now=now
        state=snapshot.get('state') or {}; elapsed=now-snapshot.get('received_at',0)
        if not video_fresh or not snapshot.get('connected') or not 0<=elapsed<=.5 or state.get('state')!='RUNNING':
            self.reset(); self.status='DISCONNECTED'; return self.view(now)
        raw=validate_world(state.get('world'))
        if raw is None:
            self.reset(); self.status='WAITING'; return self.view(now)
        transport=raw.get('_transport_ms',0)
        if not _number(transport) or not 0<=transport<=5000: transport=5000
        extra=elapsed*1000+transport
        if not raw['live'] or not 0<=raw['report_age_ms']+extra<=750:
            self.reset(); self.status='STALE'; return self.view(now)
        if video_ms is not None and not -250<=video_ms-raw['frame_ms']<=1000:
            self.reset(); self.status='VIDEO_GAP'; return self.view(now)
        key=(state.get('session'),raw['producer'],raw['width'],raw['height'])
        if key!=self.key: self.reset(); self.key=key
        frame=(raw['frame_seq'],raw['frame_ms'])
        if self.last_frame and (frame[0]<self.last_frame[0] or frame[1]<self.last_frame[1]):
            self.reset(); self.status='TIME_RESET'; return self.view(now)
        self.last_frame=frame; self.status='LIVE'; self.last_now=now
        if self.object_history and state.get('display_only') is True:
            history=validate_world(state.get('display_world'))
            if (history is not None and history['producer']==raw['producer'] and history['live'] and
                    0<=history['report_age_ms']+extra<=750 and history['object_valid'] and
                    0<=history['object_age_ms']+extra<=2500 and
                    (video_ms is None or -250<=video_ms-history['frame_ms']<=1000)):
                raw=dict(raw)
                for field in ('object_seq','object_ms','object_age_ms','object_valid','objects'):raw[field]=history[field]
        self.ages=tuple(raw[k]+extra if raw[k]>=0 else -1 for k in ('lane_age_ms','object_age_ms'))
        self.skew=abs(raw['lane_ms']-raw['object_ms']) if raw['lane_valid'] and raw['object_valid'] else None
        for index,kind in enumerate(('lane','object')):
            tracks=self.lanes if kind=='lane' else self.objects
            seq=(raw[kind+'_seq'],raw[kind+'_ms'])
            age=self.ages[index]
            maximum=2500 if kind=='object' and self.object_history else 1000
            if not raw[kind+'_valid'] or not 0<=age<=maximum:
                tracks.clear(); self.sequences[kind]=None; continue
            old=self.sequences[kind]
            if old and (seq[0]<old[0] or seq[1]<old[1]):
                tracks.clear(); self.status='TIME_RESET'; continue
            if seq==old: continue
            self.sequences[kind]=seq
            detections=[]
            if kind=='lane':
                for style,quality,role,points in raw['lanes']:
                    p=np.array(points,dtype=float)/10000
                    p=p[np.argsort(p[:,1])]
                    p=p[np.r_[True,np.diff(p[:,1])>1e-5]]
                    if len(p)<3 or p[-1,1]-p[0,1]<.06: continue
                    # Fixed point count simplifies interpolation without extrapolating endpoints.
                    ys=np.linspace(p[0,1],p[-1,1],12)
                    p=np.column_stack((np.interp(ys,p[:,1],p[:,0]),ys))
                    detections.append(dict(raw=p,style=style,quality=quality,role=role))
            else:
                for cls,score,*box in raw['objects']:
                    p=np.array(box,dtype=float)/10000
                    if score<2500: continue
                    detections.append(dict(raw=p,cls=cls,score=score))
            self._associate(tracks,detections,kind,seq,now-age/1000,now)
        return self.view(now)

    def view(self,now):
        result=dict(status=self.status,lanes=[],objects=[],omitted=0,ages=self.ages,skew=self.skew)
        for kind,tracks,hold in (('lanes',self.lanes,LANE_HOLD),('objects',self.objects,OBJECT_HOLD)):
            hold,maximum=self._track_limits('lane' if kind=='lanes' else 'object')
            tracks[:]=[t for t in tracks if 0<=now-t['updated']<=hold and 0<=now-t['evidence_at']<=maximum]
            for t in tracks:
                shape=self.display_shape(t,now); held=t['held'] or now-t['evidence_at']>.75
                alpha=.45 if held else 1.
                if held: alpha*=max(.15,1-(now-t['updated'])/hold)
                if kind=='lanes':
                    points,mask=self.projection.project(shape)
                    if np.count_nonzero(mask)<3: continue
                    # Invalid gaps are rendered as separate observed segments, never bridged.
                    result[kind].append(dict(id=t['id'],points=points,mask=mask,style=t['style'],role=t['role'],
                                             quality=t['quality']/10000.,held=held,alpha=alpha))
                else:
                    foot=[(shape[0]+shape[2])/2,shape[3]]
                    points,mask=self.projection.project([foot])
                    if not mask[0] or shape[3]>=self.projection.roi[3]-.005:
                        result['omitted']+=1; continue
                    result[kind].append(dict(id=t['id'],position=points[0],cls=t['cls'],held=held,alpha=alpha,
                                             score=t['score']/10000.,samples=t.get('samples',1),
                                             age_ms=(now-t['evidence_at'])*1000))
        result['lane_bands']=observed_lane_bands(result['lanes'])
        return result


class RoadRenderer:
    WIDTH,HEIGHT=656,648
    BG=(40,26,16)
    ORIGIN_Y=518
    def __init__(self):
        self.base=np.full((self.HEIGHT,self.WIDTH,3),self.BG,np.uint8)
        poly=np.int32([self.point(x,y) for x,y in ((-3.3,0),(3.3,0),(3.3,7),(-3.3,7))])
        cv2.fillPoly(self.base,[poly],(49,38,26),cv2.LINE_AA)
        for y in range(8): cv2.line(self.base,self.point(-3.3,y),self.point(3.3,y),(61,48,33),1,cv2.LINE_AA)
        for x in np.arange(-3,3.1,.5): cv2.line(self.base,self.point(x,0),self.point(x,7),(56,43,30),1,cv2.LINE_AA)

    @staticmethod
    def point(x,y,z=0):
        # Relative display scale spreads the near field without claiming metric depth.
        depth=math.log1p(3*y)/math.log(22)*7 if y>0 else y
        # NumPy 1.17 round(np.float64) can return a floating scalar; OpenCV 4.2
        # requires Python int coordinates. Normalize at the drawing boundary.
        return int(round(float(328+x*(87-4*depth)))),int(round(float(RoadRenderer.ORIGIN_Y-depth*57-z*28)))

    def _car(self,image,x,y,color,ego=False):
        w,h,z=(.24,.42,.45) if ego else (.17,.26,.35)
        floor=[(x-w,y-h/2),(x+w,y-h/2),(x+w,y+h/2),(x-w,y+h/2)]
        bottom=np.int32([self.point(a,b) for a,b in floor]); roof=np.int32([self.point(a,b,z) for a,b in floor])
        for i,j in ((0,1),(1,2)):
            cv2.fillConvexPoly(image,np.array([bottom[i],bottom[j],roof[j],roof[i]]),tuple(int(c*.6) for c in color),cv2.LINE_AA)
        cv2.fillConvexPoly(image,roof,color,cv2.LINE_AA)
        cv2.polylines(image,[roof],True,(221,226,219),1,cv2.LINE_AA)
        a=self.point(x-w*.65,y+.02,z+.02); b=self.point(x+w*.65,y+.14,z+.02)
        cv2.rectangle(image,(a[0],b[1]),(b[0],a[1]),(75,57,34),-1)

    def frame(self,view,direction=None):
        image=self.base.copy()
        lane_bands=view.get('lane_bands') or observed_lane_bands(view['lanes'])
        for band in lane_bands['bands']:
            polygon=np.array([self.point(*point) for point in band['polygon']],np.int32)
            layer=image.copy();cv2.fillPoly(layer,[polygon],(88,71,43),cv2.LINE_AA)
            image=cv2.addWeighted(image,.7,layer,.3,0)
            label_point=self.point(*band['center'])
            cv2.putText(image,'L%d'%band['number'],label_point,cv2.FONT_HERSHEY_SIMPLEX,.48,(172,157,121),1,cv2.LINE_AA)
        crosswalk=view.get('crosswalk',{})
        for region in crosswalk.get('regions',[]):
            color=(226,150,233) if region['persons'] else (180,204,194) if region['confirmed'] else (124,138,140)
            alpha=region['alpha']*(.8 if region['confirmed'] else .45)
            for field,strength in (('observed_map',alpha),('inferred_map',alpha*.32)):
                mask=region[field]
                image[mask]=np.rint(image[mask]*(1-strength)+np.array(color)*strength).astype(np.uint8)
            contours,_=cv2.findContours(region['observed_map'].astype(np.uint8),cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(image,contours,-1,color,1,cv2.LINE_AA)
            rows,columns=np.nonzero(region['observed_map'])
            if len(rows):
                label='Z%d'%region['id']+(' ?' if not region['confirmed'] else '')
                if region['persons']:label+=': '+','.join('P%d'%identifier for identifier in region['persons'])
                cv2.putText(image,label,(int(columns.min()),max(83,int(rows.min())-5)),
                            cv2.FONT_HERSHEY_SIMPLEX,.43,color,1,cv2.LINE_AA)
        for lane in view['lanes']:
            color={0:(134,134,134),1:(83,177,251),2:(190,210,58)}[lane['style']]
            color=tuple(int(round(float(c*lane['alpha']+b*(1-lane['alpha'])))) for c,b in zip(color,self.BG))
            pts=lane['points']; mask=lane['mask']
            for i in range(1,len(pts)):
                if not mask[i-1] or not mask[i]: continue
                if lane['style']==2 and i%3==0: continue
                cv2.line(image,self.point(*pts[i-1]),self.point(*pts[i]),color,3 if lane['role'] else 2,cv2.LINE_AA)
        labels=[]
        for obj in sorted(view['objects'],key=lambda x:-x['position'][1]):
            x,y=obj['position']; p=self.point(x,y)
            color=(226,150,233) if obj['cls']==0 else (200,201,83)
            color=tuple(int(round(float(c*obj['alpha']+b*(1-obj['alpha'])))) for c,b in zip(color,self.BG))
            if obj['cls']==0:
                cv2.ellipse(image,p,(7,3),0,0,360,(65,48,37),-1,cv2.LINE_AA)
                cv2.line(image,(p[0]-4,p[1]),(p[0],p[1]-12),color,3,cv2.LINE_AA)
                cv2.line(image,(p[0]+4,p[1]),(p[0],p[1]-12),color,3,cv2.LINE_AA)
                cv2.line(image,(p[0],p[1]-10),(p[0],p[1]-22),color,4,cv2.LINE_AA)
                cv2.circle(image,(p[0],p[1]-28),5,color,-1,cv2.LINE_AA)
            elif obj['cls'] in (1,3):
                cv2.circle(image,(p[0]-6,p[1]),5,color,2,cv2.LINE_AA)
                cv2.circle(image,(p[0]+6,p[1]),5,color,2,cv2.LINE_AA)
                cv2.line(image,(p[0]-6,p[1]),(p[0],p[1]-12),color,2,cv2.LINE_AA)
                cv2.line(image,(p[0]+6,p[1]),(p[0],p[1]-12),color,2,cv2.LINE_AA)
                cv2.circle(image,(p[0],p[1]-20),4,color,-1,cv2.LINE_AA)
            else: self._car(image,x,y,color)
            label=('P' if obj['cls']==0 else 'B' if obj['cls'] in (1,3) else 'V')+str(obj['id'])+(' ~' if obj['held'] else '')
            box=(p[0]+9,p[1]-36,p[0]+12+len(label)*8,p[1]-22)
            if not any(box[0]<b[2] and b[0]<box[2] and box[1]<b[3] and b[1]<box[3] for b in labels):
                cv2.putText(image,label,(p[0]+9,p[1]-23),cv2.FONT_HERSHEY_SIMPLEX,.4,color,1,cv2.LINE_AA)
                labels.append(box)
        self._car(image,0,-.08,(241,155,79),True)
        if direction in (1,2,3):
            start=self.point(0,.1);end=(start[0]+{1:-44,2:0,3:44}[direction],start[1]-44)
            cv2.arrowedLine(image,start,end,(240,170,52),3,cv2.LINE_AA,tipLength=.3)
        return image
