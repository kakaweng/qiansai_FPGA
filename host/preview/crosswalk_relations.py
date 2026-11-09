"""Image-space crossing regions and advisory relations. No vehicle control.

Observed pixels, small inferred gaps, and foot-location uncertainty are separate.
Lane support is never extrapolated into a junction. A missing relation is unknown,
not evidence that a road is clear. Times are source seconds from one session.
"""
import math
import cv2
import numpy as np


def overlap(a,b):
    n=np.count_nonzero(a&b)
    return n/max(1,np.count_nonzero(a|b))


class CrossingRegions:
    def __init__(self, shape=(360,640), max_gap=.6, match_age=.35):
        if not 0<match_age<=max_gap:raise ValueError('Invalid region tracking intervals')
        self.shape=shape;self.max_gap=float(max_gap);self.match_age=float(match_age)
        self.next_id=1;self.reset()

    def reset(self):
        self.tracks=[];self.last=None;self.session=None;self.last_result=[]

    def update(self, candidate, allowed, now, session=1, reset=False, vehicle_boxes=()):
        candidate=np.asarray(candidate,dtype=bool);allowed=np.asarray(allowed,dtype=bool)
        if candidate.shape!=self.shape or allowed.shape!=self.shape or not math.isfinite(now):
            self.reset();raise ValueError('Invalid region input')
        if reset or session!=self.session or (self.last is not None and (now<self.last or now-self.last>self.max_gap)):
            self.reset()
        if self.last==now:return self.last_result
        self.last=now;self.session=session
        observed=candidate&allowed
        occluders=np.zeros(self.shape,np.uint8)
        h,w=self.shape
        for box in vehicle_boxes:
            b=np.asarray(box,float)
            if b.shape!=(4,) or not np.isfinite(b).all() or np.any((b<0)|(b>1)):continue
            x1,y1,x2,y2=np.rint(b*[w-1,h-1,w-1,h-1]).astype(int)
            cv2.rectangle(occluders,(x1,y1),(x2,y2),1,-1)
        observed &= ~occluders.astype(bool)
        # Limited local closing; never a global hull across an intersection.
        closed=cv2.morphologyEx(observed.astype(np.uint8),cv2.MORPH_CLOSE,
                                 cv2.getStructuringElement(cv2.MORPH_RECT,(19,5)))>0
        distance=cv2.distanceTransform((~observed).astype(np.uint8),cv2.DIST_L2,3)
        region=(observed | (closed & (distance<=7)))&allowed&~occluders.astype(bool)
        count,labels,stats,centroids=cv2.connectedComponentsWithStats(region.astype(np.uint8),8)
        detections=[]
        for i in range(1,count):
            mask=labels==i;support=int(np.count_nonzero(mask&observed))
            x,y,bw,bh,area=stats[i]
            if support<45 or bw<6 or bh<2 or area>h*w*.45:continue
            detections.append(dict(mask=mask,observed=mask&observed,inferred=mask&~observed,
                bbox=[int(x),int(y),int(x+bw),int(y+bh)],center=centroids[i],pixels=support))
        old=[t for t in self.tracks if 0<now-t['at']<=self.match_age]
        pairs=[]
        for i,t in enumerate(old):
            for j,d in enumerate(detections):
                ratio=d['pixels']/max(1,t['pixels'])
                iou=overlap(t['mask'],d['mask'])
                if .25<ratio<4 and iou>=.12:pairs.append((-iou,i,j))
        used=set();matched=set()
        for _,i,j in sorted(pairs):
            if i in used or j in matched:continue
            used.add(i);matched.add(j)
            detections[j].update(id=old[i]['id'],hits=min(3,old[i]['hits']+1))
        for d in detections:
            if 'id' not in d:d.update(id=self.next_id,hits=1);self.next_id+=1
            d.update(at=float(now),confirmed=d['hits']>=2)
        self.tracks=detections
        self.last_result=detections
        return detections


def lane_corridor(lanes,shape=(360,640),age=0):
    """Only overlap of two observed ego curves; source plane normalized 0..1."""
    h,w=shape;mask=np.zeros(shape,bool)
    result=dict(status='UNKNOWN',reason='missing_two_boundaries',mask=mask,polygon=[],source='current_lane_model',age=age)
    if not math.isfinite(age) or not 0<=age<=.25:result['reason']='stale_lane';return result
    sides={}
    for lane in lanes:
        if lane['role'] not in (1,2) or lane['quality']<.35:continue
        p=np.asarray(lane['points'],float)
        if p.ndim!=2 or p.shape[1]!=2 or len(p)<3 or not np.isfinite(p).all() or np.any((p<0)|(p>1)):continue
        p=p[np.argsort(p[:,1])];p=p[np.r_[True,np.diff(p[:,1])>1e-6]]
        if len(p)<3:continue
        sides[lane['role']]=p
    if len(sides)!=2:return result
    left,right=sides[1],sides[2]
    lo=max(left[0,1],right[0,1]);hi=min(left[-1,1],right[-1,1])
    if hi-lo<.06:result['reason']='insufficient_shared_extent';return result
    yy=np.linspace(lo,hi,24);lx=np.interp(yy,left[:,1],left[:,0]);rx=np.interp(yy,right[:,1],right[:,0]);width=rx-lx
    if np.any(width<.035) or np.any(width>.8) or not lx[-1]<.5<rx[-1]:
        result['reason']='implausible_ego_geometry';return result
    poly=np.column_stack((np.r_[lx,rx[::-1]],np.r_[yy,yy[::-1]]))
    pixel=np.rint(poly*[w-1,h-1]).astype(np.int32);tmp=np.zeros(shape,np.uint8);cv2.fillPoly(tmp,[pixel],1)
    return dict(status='OBSERVED_CORRIDOR',reason='no_extrapolation',mask=tmp.astype(bool),polygon=poly.tolist(),
                source='current_lane_model',y_range=[float(lo),float(hi)],age=age)


def foot_support(box,shape):
    h,w=shape;p=np.asarray(box,float)
    if p.shape!=(4,) or not np.isfinite(p).all() or np.any((p<0)|(p>1)) or p[0]>=p[2] or p[1]>=p[3]:
        return None
    x=(p[0]+p[2])*.5*(w-1);y=p[3]*(h-1)
    # Bounded image uncertainty; never a metric distance or future trajectory.
    rx=float(np.clip((p[2]-p[0])*w*.55+2,4,24))
    ry=float(np.clip((p[3]-p[1])*h*.14+2,4,13))
    yy,xx=np.ogrid[:h,:w]
    mask=((xx-x)/rx)**2+((yy-y)/ry)**2<=1
    return dict(mask=mask,center=[x,y],radii=[rx,ry])


class RelativeFootMotion:
    """Short image motion after background affine compensation, not intent/speed."""
    def __init__(self):self.reset()

    def reset(self):self.gray=None;self.at=None;self.history={};self.previous_boxes=[]

    def update(self,gray,people,boxes,now,reset=False):
        gray=np.asarray(gray,np.uint8);h,w=gray.shape
        if reset or self.at is not None and (now<self.at or now-self.at>.6):self.reset()
        if self.at==now:return {}
        info=dict(valid=False,reason='insufficient_background',inliers=0)
        matrix=None
        if self.gray is not None and self.gray.shape==gray.shape:
            allowed=np.zeros(gray.shape,np.uint8);allowed[int(h*.4):int(h*.9)]=255
            for b in self.previous_boxes:
                p=np.rint(np.asarray(b)*[w-1,h-1,w-1,h-1]).astype(int)
                cv2.rectangle(allowed,tuple(p[:2]-3),tuple(p[2:]+3),0,-1)
            points=cv2.goodFeaturesToTrack(self.gray,180,.02,7,mask=allowed)
            if points is not None and len(points)>=16:
                moved,status,_=cv2.calcOpticalFlowPyrLK(self.gray,gray,points,None,winSize=(21,21),maxLevel=3)
                if moved is not None:
                    good=status.ravel()>0;a=points[good].reshape(-1,2);b=moved[good].reshape(-1,2)
                    finite=np.isfinite(a).all(1)&np.isfinite(b).all(1);a,b=a[finite],b[finite]
                    if len(a)>=16:
                        fitted,inliers=cv2.estimateAffinePartial2D(a,b,method=cv2.RANSAC,ransacReprojThreshold=2.5)
                        if fitted is not None:
                            scale=math.hypot(fitted[0,0],fitted[1,0]);rotation=abs(math.atan2(fitted[1,0],fitted[0,0]))
                            rate=float(inliers.mean());n=int(inliers.sum())
                            if n>=12 and rate>=.65 and .94<scale<1.06 and rotation<.04 and np.linalg.norm(fitted[:,2])<30:
                                matrix=fitted;info=dict(valid=True,reason='dominant_background_affine',inliers=n,inlier_fraction=rate)
        result={};new={}
        for person in people:
            key=person['id'];p=person['box'];foot=np.array([(p[0]+p[2])*.5*(w-1),p[3]*(h-1)])
            old=self.history.get(key)
            entry=dict(foot=foot,at=now,steps=[],start=now)
            transverse=False;residual=None
            reliable=not person.get('held') and not person.get('rider_ambiguous') and person.get('age',0)<=.25
            if reliable and matrix is not None and old and 0<now-old['at']<=.35:
                predicted=matrix@np.r_[old['foot'],1.]
                delta=foot-predicted
                # Reject abrupt detector jumps relative to visible person size.
                if np.linalg.norm(delta)<max(12,(p[3]-p[1])*h*.6):
                    steps=[s for s in old['steps'] if now-s[0]<=.65]+[(now,delta.tolist())]
                    entry['steps']=steps;entry['start']=old['start']
                    if len(steps)>=2:
                        d=np.asarray([s[1] for s in steps]);total=d.sum(0);residual=total.tolist()
                        consistent=abs(d[:,0].sum())>=.8*np.abs(d[:,0]).sum()
                        transverse=bool(consistent and abs(total[0])>=9 and abs(total[0])>1.5*abs(total[1]) and now-old['start']>=.35)
            if reliable:new[key]=entry
            result[key]=dict(transverse=transverse,compensation=info,residual_pixels=residual,
                             scope='short_image_motion_not_crossing_intent_or_metric_velocity')
        self.history=new;self.gray=gray.copy();self.at=now;self.previous_boxes=boxes
        return result


def classify_person(person,regions,path,allowed):
    """Return observable relation only; missing/unreliable geometry stays unknown."""
    base=dict(track_id=person['id'],contact='UNKNOWN',region_id=None,path_relation='UNKNOWN',
              raw_advisory='UNKNOWN',reason='unreliable_person',driving_permission=False)
    age=person.get('age',0)
    if person.get('held') or person.get('rider_ambiguous') or not math.isfinite(age) or not 0<=age<=.25:return base
    foot=foot_support(person['box'],allowed.shape)
    if foot is None:return base
    x,y=np.rint(foot['center']).astype(int)
    if person['box'][3]>=.995 or not allowed[y,x]:base['reason']='truncated_or_excluded_foot';return base
    base.update(foot_center=foot['center'],foot_radii=foot['radii'])
    candidates=[]
    for region in regions:
        if not region['confirmed']:continue
        direct=bool(region['observed'][y,x]);n=int(np.count_nonzero(region['observed']&foot['mask']))
        inferred=int(np.count_nonzero(region['inferred']&foot['mask']))
        if direct or n>=3 or inferred>=3:candidates.append((int(direct)*100000+n+inferred*.25,region,direct,n))
    motion_only=False
    if not candidates:
        # A separate weaker cue: transverse movement near a visible region.
        # It never fabricates an occupied area or upgrades to potential conflict.
        motion=person.get('motion',{})
        if motion.get('transverse') and motion.get('compensation',{}).get('valid'):
            limit=float(np.clip((person['box'][3]-person['box'][1])*allowed.shape[0]*.45,10,38))
            for region in regions:
                if not region['confirmed']:continue
                yy,xx=np.nonzero(region['observed'])
                distance=float(np.sqrt((xx-x)**2+(yy-y)**2).min())
                if distance<=limit:candidates.append((-distance,region,False,0))
            motion_only=bool(candidates)
        if not candidates:
            base.update(contact='NO_OBSERVED_ASSOCIATION',raw_advisory='NO_CURRENT_ASSOCIATION',reason='not_a_clear_road_claim')
            return base
    _,region,direct,n=max(candidates,key=lambda x:x[0]);base['region_id']=region['id']
    base.update(contact='OBSERVED_FOOT_CONTACT' if direct else 'POSSIBLE_OCCLUDED_CONTACT',
                contact_support_pixels=n,raw_advisory='CROSSING_ATTENTION',reason='path_unresolved')
    if motion_only:
        base.update(contact='TRANSVERSE_NEAR_REGION',reason='motion_near_visible_region_not_occupancy')
    if path['status']!='OBSERVED_CORRIDOR':return base
    ymin=(y-foot['radii'][1])/(allowed.shape[0]-1);ymax=(y+foot['radii'][1])/(allowed.shape[0]-1)
    lo,hi=path['y_range']
    if ymin<lo or ymax>hi:base['reason']='outside_observed_lane_extent';return base
    inside=np.count_nonzero(path['mask']&foot['mask'])/max(1,np.count_nonzero(foot['mask']))
    base['foot_corridor_fraction']=float(inside)
    if inside>=.3 and not motion_only:
        base.update(path_relation='IN_OBSERVED_CORRIDOR',raw_advisory='POTENTIAL_CONFLICT',reason='person_crossing_region_and_current_corridor')
    elif inside>=.3:
        base.update(path_relation='IN_CORRIDOR_NEAR_REGION',reason='motion_near_region_without_occupancy_evidence')
    elif inside>0:
        base.update(path_relation='NEAR_CORRIDOR_EDGE',raw_advisory='CROSSING_ATTENTION',reason='uncertain_foot_at_lane_edge')
    else:
        # Relevant band may cross the corridor although the person is not there yet.
        if np.count_nonzero(region['mask']&path['mask'])>=12:
            base.update(path_relation='SAME_BAND_OUTSIDE_CORRIDOR',reason='person_outside_but_band_intersects_corridor')
        else:
            ry0=region['bbox'][1]/(allowed.shape[0]-1);ry1=(region['bbox'][3]-1)/(allowed.shape[0]-1)
            if lo<=ry0 and ry1<=hi:
                base.update(path_relation='SIDE_REGION_OUTSIDE_CORRIDOR',raw_advisory='SIDE_OBSERVATION',reason='current_observed_regions_disjoint')
            else:base['reason']='crossing_region_extent_not_covered_by_lane'
    return base


class AdvisoryState:
    def __init__(self):self.reset()

    def reset(self):self.states={};self.last=None;self.session=None;self.last_rows=[]

    def update(self,rows,now,session=1,reset=False):
        if not math.isfinite(now):self.reset();raise ValueError('Invalid time')
        if reset or self.session!=session or (self.last is not None and (now<self.last or now-self.last>.6)):
            self.reset()
        if now==self.last:return self.last_rows
        self.session=session;self.last=now
        self.states={k:v for k,v in self.states.items() if now-v['seen']<=.6}
        out=[]
        for r in rows:
            row=dict(r);key=r['track_id'];raw=r['raw_advisory'];old=self.states.get(key)
            votes=old['votes']+1 if old and old['raw']==raw and now-old['seen']<=.35 else 1
            stable=raw if votes>=2 else 'CONFIRMING'
            if raw in ('UNKNOWN','NO_CURRENT_ASSOCIATION'):stable=raw
            self.states[key]=dict(raw=raw,votes=votes,seen=now)
            row.update(advisory=stable,confirmations=votes,driving_permission=False)
            out.append(row)
        self.last_rows=out
        return out


def frame_advisory(rows):
    for state in ('POTENTIAL_CONFLICT','CROSSING_ATTENTION','CONFIRMING','SIDE_OBSERVATION'):
        if any(r['advisory']==state for r in rows):return state
    return 'NO_CONFIRMED_RELATION'
