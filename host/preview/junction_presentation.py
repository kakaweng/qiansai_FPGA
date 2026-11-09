"""Display-only scene completion and rehearsal; never emits board permissions."""
import math
import numpy as np
from live_road import validate_world
from junction_route import RouteConflictGate
from junction_association import JunctionAssociator
from pedestrian_behavior import SourceMotion,observe_behavior


class JunctionPresentation:
    def __init__(self):
        self.next_id=1
        self.route_gate=RouteConflictGate()
        self.associator=JunctionAssociator()
        self.motion=SourceMotion()
        self.reset()

    def reset(self):
        self.session=None;self.frame=None;self.sequence=None;self.last_now=None
        self.tracks=[];self.lane_sequence=None;self.lane_count=2;self.lane_at=None
        self.pending_lane=None;self.lane_votes=0;self.crosswalk_at=None
        self.active=False;self.phase='READY';self.distance=0.;self.direction=3;self.force=False
        self.connected=False;self.last_view={}
        self.observation=None;self.route_gate.reset();self.associator.reset();self.motion.reset()

    @staticmethod
    def family(category):
        return 0 if category==0 else 1 if category in (1,3) else 2

    @staticmethod
    def layout_point(box,category,crossing=None):
        horizontal=float((box[0]+box[2])/2)
        foot=float(box[3])
        if category==0:
            if crossing in ('left','right'):
                side=1. if crossing=='right' else -1.
                lateral=side*(horizontal-.5)
                along=8.-16.*(lateral-.06)/.34+8.*(foot-.8)
                return np.array([side*9.6,float(np.clip(along,-8.5,8.5))])
            if crossing in ('near','far'):
                return np.array([np.clip((horizontal-.5)*32.,-8.5,8.5),9.6 if crossing=='near' else -9.6])
            # Keep the detected person visible. Relative body size supplies a
            # coarse display depth; the dashed glyph does not claim a crossing.
            depth=float(np.clip((box[3]-box[1]-.035)/.28,0,1))
            return np.array([(horizontal-.5)*23.,-14.+32.*depth])
        depth=float(np.clip((foot-.46)/.5,0,1))
        return np.array([np.clip((horizontal-.5)*25.,-8.5,8.5),-19.+43.*depth])

    def action(self,action):
        if action=='scene_start':
            if self.connected:
                if self.distance>=60000:self.distance=0.
                self.active=True;self.phase='APPROACH';self.force=False
        elif action=='scene_pause':self.active=False;self.phase='PAUSED'
        elif action=='scene_reset':
            self.active=False;self.phase='READY';self.distance=0.;self.force=False
            self.route_gate.reset()
        elif action=='scene_force':
            if self.connected:self.active=True;self.force=True
        elif action in ('turn_left','straight','turn_right'):
            self.direction=('turn_left','straight','turn_right').index(action)+1
            self.active=False;self.distance=0.;self.phase='READY';self.force=False
            self.route_gate.reset()

    def _place(self,track,object_ms):
        relation=self.associator.associate(track['box'],object_ms) if not track['inferred'] else dict(
            crossing=None,point=None,association='COUNT_ONLY',region_id=None,geometry_key=None)
        track.update(crossing=relation['crossing'],association=relation['association'],
                     region_id=relation['region_id'],geometry_key=relation['geometry_key'])
        if (not track['inferred'] and track.get('behavior') in ('CROSSING','CROSSING_HOLD') and
                track['box'][3]-track['box'][1]>=.12 and track['box'][3]>=.68 and
                relation['association']!='FOOT_SUPPORT'):
            track.update(crossing='near',association='MOTION_NEAR_ESTIMATE')
            return np.array([np.clip(((track['box'][0]+track['box'][2])/2-.5)*22.,-11.5,11.5),9.6])
        return relation['point'] if relation['point'] is not None else self.layout_point(track['box'],0)

    def _refresh_geometry(self):
        for track in self.tracks:
            if track['cls']!=0 or track['inferred'] or track.get('geometry_key')==self.associator.sequence:continue
            if self.associator.status!='LIVE' or abs(track['object_ms']-self.associator.source_ms)>350:continue
            crossing=track['crossing'];target=self._place(track,track['object_ms'])
            track['target']=target
            if crossing!=track['crossing']:
                track['position']=target.copy();track['velocity']=np.zeros(2)

    def _observe(self,world,state,now,age,associations):
        sequence=(world['object_seq'],world['object_ms'])
        if sequence==self.sequence or not sequence[0] or not 0<=age<=2.5:return
        # Session/frame discontinuities reset in update(); reordered inference
        # results must not refresh tracks or contribute a new empty observation.
        if self.sequence and (sequence[0]<=self.sequence[0] or sequence[1]<=self.sequence[1]):return
        matrix=self.motion.between(self.sequence[1] if self.sequence else None,world['object_ms'],
                                   [track['box'] for track in self.tracks if not track['inferred']])
        self.sequence=sequence
        evidence_at=now-age
        candidates=[]
        for category,score,*coordinates in world['objects']:
            if score<3500:continue
            box=np.asarray(coordinates,dtype=float)/10000
            if box[3]-box[1]<.018:continue
            candidates.append(dict(cls=category,score=score/10000,box=box,inferred=False))
        raw_gate=state.get('pedestrian') or {}
        if any(candidate['cls']==0 for candidate in candidates):
            self.tracks=[track for track in self.tracks if not track['inferred']]
        count=raw_gate.get('total',0) if isinstance(raw_gate,dict) else 0
        gate_age=raw_gate.get('age_ms',-1) if isinstance(raw_gate,dict) else -1
        gate_fresh=(type(gate_age) in (int,float) and 0<=gate_age<=2500 and
                    raw_gate.get('producer')==world['producer'] and raw_gate.get('object_seq')==world['object_seq'])
        recent_people=any(track['cls']==0 and not track['inferred'] and evidence_at-track['seen']<2.2 for track in self.tracks)
        if gate_fresh and not recent_people and not any(candidate['cls']==0 for candidate in candidates) and type(count) is int and count>0:
            for index in range(min(3,count)):
                center=.25+.12*index
                candidates.append(dict(cls=0,score=.35,box=np.array([center-.02,.5,center+.02,.72]),inferred=True))
        used=set()
        for candidate in candidates:
            center=(candidate['box'][:2]+candidate['box'][2:])/2
            matches=[]
            for index,track in enumerate(self.tracks):
                if index in used or self.family(track['cls'])!=self.family(candidate['cls']):continue
                if track['inferred']!=candidate['inferred'] or evidence_at-track['seen']>2.2:continue
                previous=(track['box'][:2]+track['box'][2:])/2
                gap=evidence_at-track['seen']
                prediction=previous+track.get('image_velocity',np.zeros(2))*min(.8,max(0.,gap))
                ratio=(candidate['box'][3]-candidate['box'][1])/max(.018,track['box'][3]-track['box'][1])
                cost=float(np.linalg.norm(center-prediction))
                if .45<ratio<2.2 and cost<.16:matches.append((cost,index))
            match=min(matches)[1] if matches else None
            candidate['object_ms']=world['object_ms']
            candidate['crossing']=None
            previous_track=self.tracks[match] if match is not None else None
            if candidate['cls']==0 and not candidate['inferred']:
                observe_behavior(candidate,previous_track,evidence_at,matrix)
            candidate['image_velocity']=np.zeros(2)
            if previous_track is not None:
                previous_center=(previous_track['box'][:2]+previous_track['box'][2:])/2
                candidate['image_velocity']=np.clip((center-previous_center)/max(.08,evidence_at-previous_track['seen']),-.4,.4)
            target=self._place(candidate,world['object_ms']) if candidate['cls']==0 else self.layout_point(candidate['box'],candidate['cls'])
            crossing=candidate['crossing']
            contact=any(np.linalg.norm(center-person['center'])<.08 for person in associations) if candidate['cls']==0 else False
            if matches:
                _,index=min(matches);track=self.tracks[index];used.add(index)
                elapsed=max(.05,evidence_at-track['seen'])
                velocity=np.clip((target-track['target'])/elapsed,-3.,3.)
                if crossing!=track.get('crossing'):
                    track['position']=target.copy();track['velocity']=np.zeros(2);velocity=np.zeros(2)
                track.update(candidate,samples=min(1000,track['samples']+1),seen=evidence_at,
                    velocity=track['velocity']*.65+velocity*.35,target=target,contact=contact,crossing=crossing)
            else:
                self.tracks.append(dict(candidate,id=self.next_id,samples=1,seen=evidence_at,
                    velocity=np.zeros(2),target=target,position=target.copy(),contact=contact,crossing=crossing))
                used.add(len(self.tracks)-1);self.next_id+=1
        self.observation=dict(key=sequence,stamp=world['object_ms'],at=evidence_at,
            valid=world['live'] and world['object_valid'] and 0<=world['report_age_ms']<=750,
            ids=[self.tracks[index]['id'] for index in used if not self.tracks[index]['inferred']],
            ambiguous=any(candidate['inferred'] for candidate in candidates))
        self.tracks.sort(key=lambda track:(-track['seen'],-track['score']))
        self.tracks=self.tracks[:24]

    def update(self,snapshot,world_view,video_fresh,now,video_ms=None,source_image=None):
        state=snapshot.get('state') or {}
        elapsed=now-snapshot.get('received_at',0)
        connected=video_fresh and snapshot.get('connected') is True and 0<=elapsed<=.5 and state.get('state')=='RUNNING'
        if not connected:
            self.reset();self.phase='DISCONNECTED'
            return self.view(now)
        world=validate_world(state.get('world'))
        reject_world=False
        if world is not None:
            session=(state.get('session'),world['producer'])
            frame=(world['frame_seq'],world['frame_ms'])
            discontinuity=(self.session is not None and session!=self.session) or (self.frame is not None and
                (frame[0]<self.frame[0] or frame[1]<self.frame[1]))
            if self.last_now is not None and now<self.last_now:discontinuity=True
            if video_ms is not None and not -300<=video_ms-world['frame_ms']<=2500:
                discontinuity=True;reject_world=True
            if discontinuity:self.reset()
            self.session=session;self.frame=frame
        delta=0. if self.last_now is None else min(.1,max(0.,now-self.last_now))
        self.last_now=now;self.connected=True
        self.motion.add(source_image,video_ms)
        if world is not None and not reject_world:
            transport=world.get('_transport_ms',0)
            if type(transport) not in (int,float) or not math.isfinite(transport) or transport<0:transport=5000
            self.associator.update(world,state.get('crosswalk'),state.get('session'),now,elapsed,video_ms)
            self._refresh_geometry()
            age=world['object_age_ms']/1000+elapsed+transport/1000
            if world['object_age_ms']<0:age=(world['frame_ms']-world['object_ms'])/1000+elapsed+transport/1000
            gate=state.get('pedestrian') or {}
            gate_age=gate.get('age_ms',-1) if isinstance(gate,dict) else -1
            aligned=(isinstance(gate,dict) and gate.get('producer')==world['producer'] and
                     gate.get('object_seq')==world['object_seq'] and gate.get('object_ms')==world['object_ms'] and
                     type(gate_age) in (int,float) and math.isfinite(gate_age) and
                     0<=gate_age+elapsed*1000+transport<=750 and gate.get('status')=='YIELD' and
                     (gate.get('crosswalk_seq'),gate.get('crosswalk_ms'))==self.associator.sequence)
            associations=[]
            for person in gate.get('persons',[]) if aligned else []:
                if not isinstance(person,dict) or not person.get('confirmed') or not person.get('contact'):continue
                box=person.get('box')
                if isinstance(box,list) and len(box)==4:
                    associations.append(dict(center=(np.array(box[:2])+box[2:])/20000))
            display=validate_world(state.get('display_world')) if state.get('display_only') is True else None
            if (display is not None and display['live'] and display['producer']==world['producer'] and
                    0<=display['report_age_ms']+elapsed*1000<=750 and 0<=display['object_age_ms']<=2500):
                same_sequence=display['object_seq']==world['object_seq'] and display['object_ms']==world['object_ms']
                self._observe(display,state,now,display['object_age_ms']/1000+elapsed+transport/1000,
                              associations if same_sequence else [])
            else:self._observe(world,state,now,age,associations)
            lane_sequence=(world['lane_seq'],world['lane_ms'])
            count=(world_view.get('lane_bands') or {}).get('visible_lane_count')
            if lane_sequence!=self.lane_sequence and type(count) is int and 1<=count<=5:
                self.lane_sequence=lane_sequence
                self.lane_votes=self.lane_votes+1 if self.pending_lane==count else 1
                self.pending_lane=count
                if self.lane_votes>=2:
                    self.lane_count=count;self.lane_at=now-max(0,world['lane_age_ms']/1000)-elapsed
            if self.associator.status=='LIVE' and any(band['role'] for band in self.associator.bands):
                self.crosswalk_at=self.associator.evidence_at
        self.tracks=[track for track in self.tracks if 0<=now-track['seen']<=2.6]
        for track in self.tracks:
            age=max(0.,now-track['seen'])
            prediction=track['target']+track['velocity']*min(age,1.0)
            if track['cls']==0:
                if track['crossing'] is not None:
                    along=1 if track['crossing'] in ('left','right') else 0
                    prediction[along]=np.clip(prediction[along],-10.5,10.5)
            weight=1.-math.exp(-delta/ .18) if delta else 0.
            track['position']+=np.clip(prediction-track['position'],-2.,2.)*weight
        view=self.view(now)
        view['route_gate']=self.route_gate.update(self.tracks,view['actors'],self.observation,
                                                self.direction,self.distance,now)
        raw_p1=((state.get('drive') or {}).get('p1') or {})
        self._advance(view,raw_p1,delta)
        view.update(scene=self.scene(),direction=self.direction)
        self.last_view=view
        return view

    def _advance(self,view,p1,delta):
        if not self.active:return
        signal=p1.get('signal',0) if 0<=p1.get('signal_age_ms',-1)<=750 else 0
        speed=5000.
        destination=min(60000.,self.distance+delta*speed)
        if not self.force:
            gate=view['route_gate']
            limits=[]
            if gate['status']!='CLEAR':
                phase='WAIT_PEDESTRIAN' if gate['status']=='YIELD' else 'WAIT_PERCEPTION'
                limits.append((gate['stop_distance'],phase))
            if self.direction!=3 and self.distance<=14500 and signal in (1,2):
                limits.append((14500.,'WAIT_RED' if signal==1 else 'WAIT_YELLOW'))
            if limits:
                stop,phase=min(limits,key=lambda item:item[0])
                if destination>=stop:
                    self.distance=max(self.distance,stop);self.phase=phase;return
        self.phase='VIOLATION' if self.force else 'APPROACH' if self.distance<15000 else 'CROSSING'
        self.distance=destination
        if self.distance>=60000:self.active=False;self.phase='COMPLETE'

    def scene(self):
        moving=self.active and self.phase in ('APPROACH','CROSSING','VIOLATION')
        return dict(enabled=True,active=self.active,phase=self.phase,draw_distance=self.distance,
                    distance_mm=self.distance,end_mm=60000,stop_mm=15000,fresh=self.connected,
                    speed_mm_s=5000 if moving else 0,crossed=self.distance>15000,violation=self.force)

    def view(self,now):
        actors=[]
        for track in self.tracks:
            age=now-track['seen']
            if track['samples']<2 and track['score']<.7 and not track['inferred']:continue
            if not 0<=age<=2.6:continue
            predicted=age>.75 or track['inferred']
            actors.append(dict(id=track['id'],cls=track['cls'],ground=tuple(map(float,track['position'])),
                crossing=track.get('crossing'),
                association_pending=track['cls']==0 and track.get('crossing') is None,
                association=track.get('association'),region_id=track.get('region_id'),
                position_estimated=track['cls']==0 and track.get('association') not in ('FOOT_SUPPORT','NEAR_SUPPORT'),
                behavior=('CROSSING_HOLD' if track.get('behavior')=='CROSSING' and
                          (not self.observation or track['id'] not in self.observation['ids'] or age>1.5)
                          else track.get('behavior','OBSERVING')),
                behavior_basis=track.get('behavior_basis','insufficient_motion'),motion_direction=track.get('motion_direction',0),
                source_foot=(float((track['box'][0]+track['box'][2])/2),float(track['box'][3])),
                source_ms=track.get('object_ms'),source_box=track['box'].tolist(),
                held=predicted,predicted=predicted,contact=track['contact'] and age<=.75,
                alpha=max(.15,min(1.,(2.6-age)/1.2))*(.7 if track['inferred'] else 1.),
                label_visible=False,inferred=track['inferred']))
        people=sorted((actor for actor in actors if actor['cls']==0),
                      key=lambda actor:(actor.get('behavior') not in ('CROSSING','CROSSING_HOLD'),
                                        actor['inferred'],-actor['alpha']))[:6]
        vehicles=[actor for actor in actors if actor['cls']!=0][:5]
        observed=self.lane_at is not None and 0<=now-self.lane_at<=5.
        return dict(version=1,display_only=True,connected=self.connected,actors=people+vehicles,
                    lane_count=self.lane_count if observed else 2,lane_inferred=not observed,
                    crosswalk_inferred=self.crosswalk_at is None or now-self.crosswalk_at>3.,
                    scene=self.scene(),direction=self.direction,route_gate=self.route_gate.result,
                    crossing_geometry=self.associator.view(),
                    unresolved_people=sum(actor.get('association_pending',False) for actor in people))
