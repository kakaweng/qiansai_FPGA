"""Local rehearsal conflicts in schematic units; never grants board permissions."""
import numpy as np
from junction_scene import route_path,route_pose


CROSSING_NAMES={'near':'驶入横道','far':'直行出口横道','left':'左转出口横道','right':'右转出口横道'}


class RouteGeometry:
    """Sample the rendered car's complete swept body, including its rear corners."""
    def __init__(self):
        self.distances=np.arange(0.,60000.1,250.)
        self.routes={}
        for direction in (1,2,3):
            path=route_path(direction)
            poses=[route_pose(path,distance) for distance in self.distances]
            centers=np.array([pose[0] for pose in poses])
            tangent=np.array([pose[1] for pose in poses])
            side=np.column_stack((-tangent[:,1],tangent[:,0]))
            windows={}
            for crossing in ('near',('left','far','right')[direction-1]):
                center=np.array({'near':(0.,9.6),'far':(0.,-9.6),
                                 'left':(-9.6,0.),'right':(9.6,0.)}[crossing])
                extent=np.array((10.,.8) if crossing in ('near','far') else (.8,10.))
                offset=np.abs(centers-center)
                # SAT for the car OBB against each crossing rectangle, with a buffer.
                overlap=np.all(offset<=extent+np.abs(tangent)*2.7+np.abs(side)*1.55,axis=1)
                for axis,radius in ((tangent,2.7),(side,1.55)):
                    overlap&=np.abs(np.sum((centers-center)*axis,axis=1))<=radius+np.sum(extent*np.abs(axis),axis=1)
                indices=np.flatnonzero(overlap)
                if len(indices):windows[crossing]=(float(self.distances[indices[0]]),float(self.distances[indices[-1]]))
            self.routes[direction]=(centers,tangent,side,windows)

    def windows(self,direction,distance):
        return {name:bounds for name,bounds in self.routes[direction][3].items() if bounds[1]>=distance}

    def intersects(self,direction,distance,points):
        centers,tangent,side,_=self.routes[direction]
        selected=self.distances>=max(0.,distance-250.)
        for point in points:
            offset=np.asarray(point,dtype=float)-centers[selected]
            along=np.abs(np.sum(offset*tangent[selected],axis=1))
            across=np.abs(np.sum(offset*side[selected],axis=1))
            if np.any((along<=3.5)&(across<=2.0)):return True
        return False


class RouteConflictGate:
    SOURCE_MAX_AGE=1.5  # Local caution only; does not extend board's 750 ms rule.
    CLEAR_SPAN=.5
    HOLD_SECONDS=1.2

    def __init__(self):
        self.geometry=RouteGeometry()
        self.reset()

    def reset(self):
        self.last_key=None;self.last_stamp=None;self.clear_since=None;self.clear_count=0
        self.last_clear_at=None;self.last_conflict=None;self.result={}

    def update(self,tracks,actors,observation,direction,distance,now):
        windows=self.geometry.windows(direction,distance)
        risks={};current_risks=set();unresolved={}
        current_ids=set(observation.get('ids',())) if observation else set()
        previous_conflicts=set(self.result.get('conflict_ids',()))
        for track in tracks:
            if track['cls']!=0 or track['inferred'] or not 0<=now-track['seen']<=2.6:continue
            box=track['box'];center=float((box[0]+box[2])/2)
            has_geometry=track.get('crossing') is not None and track.get('association') in ('FOOT_SUPPORT','NEAR_SUPPORT')
            eligible=(track['score']>=.45 and box[3]-box[1]>=.03 and
                      (.02<=center<=.98 if has_geometry else .18<=center<=.95) and .48<=box[3]<=1.)
            if not eligible and track['id'] not in previous_conflicts:continue
            crossing=track.get('crossing')
            if crossing is None:
                # A nearby, substantial unlocated person cannot count as a clear
                # observation. Distant/tiny roadside detections remain monitoring.
                if track['id'] in previous_conflicts or (track.get('association') and box[3]-box[1]>=.075 and box[3]>=.70 and .08<=center<=.92):
                    unresolved[track['id']]=track
                continue
            if crossing not in windows:continue
            crossing_flow=(track.get('behavior') in ('CROSSING','CROSSING_HOLD') and
                           track.get('behavior_basis')=='background_compensated_motion')
            # Include raw, smoothed, and actually drawn locations. Crowding layout
            # must never move a visible icon into the route without checking it.
            points=[track['target'],track['position']]
            points.extend(actor['ground'] for actor in actors if actor['id']==track['id'])
            future=track['target']+track['velocity']*1.0
            axis=1 if crossing in ('left','right') else 0
            future[axis]=np.clip(future[axis],-8.5,8.5)
            points.append(future)
            if not crossing_flow and not self.geometry.intersects(direction,distance,points):continue
            risks[track['id']]=track
            if track['id'] in current_ids:current_risks.add(track['id'])
        fresh=(observation is not None and observation.get('valid') is True and
               0<=now-observation['at']<=self.SOURCE_MAX_AGE)
        key=observation.get('key') if observation else None
        stamp=observation.get('stamp') if observation else None
        independent=(fresh and key!=self.last_key and
                     (self.last_stamp is None or stamp>self.last_stamp))
        if not fresh:
            self.clear_since=None;self.clear_count=0;self.last_clear_at=None
        if independent:
            self.last_key=key;self.last_stamp=stamp
            if current_risks or unresolved or observation.get('ambiguous'):
                self.clear_since=None;self.clear_count=0;self.last_clear_at=None
                if current_risks:self.last_conflict=observation['at']
            else:
                if self.last_clear_at is not None and observation['at']-self.last_clear_at>self.SOURCE_MAX_AGE:
                    self.clear_since=None;self.clear_count=0
                if self.clear_since is None:self.clear_since=observation['at']
                self.clear_count+=1;self.last_clear_at=observation['at']
        confirmed=any((track['contact'] and now-track['seen']<=.75) or
                      (track.get('crossing_evidence') and track.get('behavior')=='CROSSING' and
                       track.get('behavior_basis')=='background_compensated_motion' and now-track['seen']<=self.SOURCE_MAX_AGE)
                      for track in risks.values())
        cleared=(fresh and not observation.get('ambiguous') and self.clear_count>=2 and
                 self.clear_since is not None and observation['at']-self.clear_since>=self.CLEAR_SPAN and
                 (self.last_conflict is None or now-self.last_conflict>=self.HOLD_SECONDS))
        status='CLEAR' if not windows or (not risks and not unresolved and cleared) else 'YIELD' if confirmed else 'POSSIBLE' if risks else 'UNKNOWN'
        crossing=(min((track['crossing'] for track in risks.values()),key=lambda name:windows[name][0]) if risks else
                  min(windows,key=lambda name:windows[name][0]) if windows else None)
        if not fresh and windows:
            next_crossing=min(windows,key=lambda name:windows[name][0])
            if windows[next_crossing][0]<windows[crossing][0]:
                crossing=next_crossing;status='UNKNOWN'
        stop=max(distance,windows[crossing][0]-250.) if crossing else 60000.
        name=CROSSING_NAMES.get(crossing,'所选路线')
        detail={'CLEAR':'连续观测无路线冲突，继续示意预演',
                'YIELD':name+'有人，停车礼让',
                'POSSIBLE':name+'可能有人，停车确认',
                'UNKNOWN':name+'检测待确认，保持停车'}[status]
        if status=='UNKNOWN' and unresolved:detail='行人位置待确认，保持停车'
        if status=='YIELD' and any(track.get('crossing_evidence') and not track['contact'] for track in risks.values()):
            detail=name+'行人横穿中，停车礼让（运动推测）'
        self.result=dict(version=2,policy='source-band-route-conflict-v2',display_only=True,
                         status=status,crossing=crossing,detail=detail,stop_distance=stop,
                         conflict_ids=sorted(risks),unresolved_ids=sorted(unresolved),relevant_crossings=list(windows),
                         fresh=fresh,clear_samples=self.clear_count)
        for actor in actors:
            actor['route_conflict']=actor['id'] in risks
        return self.result
