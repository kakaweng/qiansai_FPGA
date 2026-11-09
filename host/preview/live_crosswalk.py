"""Observed crosswalk geometry in the same source plane as lane/object evidence."""
import math
import string
import cv2
import numpy as np
from crosswalk_relations import CrossingRegions,classify_person,lane_corridor
from live_road import RoadRenderer,validate_world

MODEL_VERSION='mapillary_lraspp_v1_20261101'
SHAPE=(360,640)


def aligned_pedestrians(raw,world,crosswalk):
    if not isinstance(raw,dict) or raw.get('version')!=1 or raw.get('policy')!='crosswalk-contact-v1':return None
    if raw.get('producer')!=world['producer'] or raw.get('object_seq')!=world['object_seq']:return None
    if raw.get('object_ms')!=world['object_ms'] or raw.get('crosswalk_seq')!=crosswalk['source_seq']:return None
    if raw.get('crosswalk_ms')!=crosswalk['source_ms'] or raw.get('status') not in ('YIELD','YIELD_HOLD','CONFIRMING','CLEAR','OBSERVE'):return None
    people=raw.get('persons')
    if not isinstance(people,list) or len(people)>16:return None
    for person in people:
        if not isinstance(person,dict):return None
        box=person.get('box')
        if (not isinstance(box,list) or len(box)!=4 or any(type(value) is not int or not 0<=value<=10000 for value in box)
                or box[0]>=box[2] or box[1]>=box[3]):return None
        if type(person.get('samples')) is not int or person['samples']<1:return None
        if type(person.get('contact')) is not bool or type(person.get('confirmed')) is not bool:return None
    return people


def same_person(box,other):
    other=np.asarray(other,dtype=float)/10000
    lower=np.maximum(box[:2],other[:2]);upper=np.minimum(box[2:],other[2:])
    intersection=np.maximum(0,upper-lower).prod()
    union=(box[2:]-box[:2]).prod()+(other[2:]-other[:2]).prod()-intersection
    return intersection/max(1e-9,union)>=.55


def decode_crosswalk(raw):
    if not isinstance(raw,dict) or type(raw.get('version')) is not int or raw['version']!=1:return None
    if raw.get('model_version')!=MODEL_VERSION:return None
    for field in ('producer','source_seq','source_ms','sent_ms'):
        if type(raw.get(field)) is not int or not 0<=raw[field]<=2**64-1:return None
    if type(raw.get('valid')) is not bool or raw.get('held') is not False:return None
    dimensions=('width','height','plane_width','plane_height')
    if any(type(raw.get(field)) is not int for field in dimensions):return None
    if tuple(raw[field] for field in dimensions)!=(1920,1080,320,180):return None
    age=raw.get('age_ms')
    if type(age) not in (int,float) or not math.isfinite(age) or not -1<=age<=100000:return None
    if raw['valid'] and age<0:return None
    encoded=raw.get('mask_lsb_hex')
    if not isinstance(encoded,str) or len(encoded)!=14400 or any(character not in string.hexdigits for character in encoded):return None
    packed=np.frombuffer(bytes.fromhex(encoded),dtype=np.uint8)
    bits=((packed[:,None]>>np.arange(8,dtype=np.uint8))&1).reshape(180,320)
    return cv2.resize(bits,(640,360),interpolation=cv2.INTER_NEAREST).astype(bool)


def unavailable_crosswalk(raw):
    if not isinstance(raw,dict) or raw.get('valid') is not False or raw.get('age_ms')!=-1 or raw.get('mask_lsb_hex')!='':return None
    if type(raw['age_ms']) not in (int,float):return None
    probe=dict(raw,valid=True,age_ms=0,mask_lsb_hex='0'*14400)
    if decode_crosswalk(probe) is None:return None
    return 'WAITING' if not raw['producer'] or not raw['source_seq'] or not raw['source_ms'] else 'STALE'


class CrosswalkMap:
    def __init__(self,projection):
        self.projection=projection
        self.regions=CrossingRegions(SHAPE,max_gap=1.1,match_age=1.)
        self.geometry_key=None;self.allowed=None;self.maps=None
        self.reset()

    def reset(self):
        self.regions.reset();self.key=None;self.sequence=None;self.last_now=None
        self.cached=[];self.mask=None;self.evidence_at=None

    @staticmethod
    def empty(status,age=-1):
        return dict(status=status,age_ms=age,regions=[],relations=[],omitted=0,skew_ms=None)

    def _geometry(self):
        key=(self.projection.matrix.tobytes(),self.projection.roi.tobytes())
        if key==self.geometry_key:return
        self.geometry_key=key;self.reset()
        height,width=SHAPE
        rows,columns=np.indices(SHAPE,dtype=np.float64)
        _,valid=self.projection.project(np.column_stack((columns.ravel()/(width-1),rows.ravel()/(height-1))))
        self.allowed=valid.reshape(SHAPE)
        rows,columns=np.indices((RoadRenderer.HEIGHT,RoadRenderer.WIDTH),dtype=np.float64)
        depth=(RoadRenderer.ORIGIN_Y-rows)/57.
        forward=np.where(depth>0,np.expm1(depth*math.log(22)/7)/3,depth)
        lateral=(columns-328)/(87-4*depth)
        ground=np.stack((lateral,forward,np.ones_like(forward)),axis=-1)
        source=ground@np.linalg.inv(self.projection.matrix).T
        denominator=source[:,:,2]
        safe=np.isfinite(source).all(axis=2)&(denominator>1e-6)&(forward>=-.25)&(forward<=7)&(np.abs(lateral)<=3.3)
        normalized=np.full(source.shape[:2]+(2,),-1.,dtype=np.float64)
        normalized[safe]=source[safe,:2]/denominator[safe,None]
        left,top,right,bottom=self.projection.roi
        safe&=(normalized[:,:,0]>=left)&(normalized[:,:,0]<=right)&(normalized[:,:,1]>=top)&(normalized[:,:,1]<=bottom)
        self.maps=tuple(np.where(safe,normalized[:,:,index]*limit,-1).astype(np.float32)
                        for index,limit in ((0,width-1),(1,height-1)))

    def _project_mask(self,mask):
        return cv2.remap(mask.astype(np.uint8),self.maps[0],self.maps[1],cv2.INTER_NEAREST,
                         borderMode=cv2.BORDER_CONSTANT,borderValue=0).astype(bool)

    def update(self,snapshot,video_fresh,now,tracker,world_view,video_ms=None):
        self._geometry()
        if self.last_now is not None and now<self.last_now:
            self.reset();return self.empty('TIME_RESET')
        self.last_now=now
        if world_view.get('status')!='LIVE' or not video_fresh:
            self.reset();return self.empty(world_view.get('status','DISCONNECTED'))
        state=snapshot.get('state') or {};world=validate_world(state.get('world'))
        raw=state.get('crosswalk')
        if raw is None:
            self.reset();return self.empty('WAITING')
        unavailable=unavailable_crosswalk(raw)
        if unavailable is not None:
            self.reset();return self.empty(unavailable)
        mask=decode_crosswalk(raw)
        if world is None or mask is None:
            self.reset();return self.empty('INVALID')
        if raw['producer']!=world['producer']:
            self.reset();return self.empty('SOURCE_MISMATCH')
        key=(state.get('session'),raw['producer'],raw['width'],raw['height'])
        if key!=self.key:self.reset();self.key=key
        sequence=(raw['source_seq'],raw['source_ms'])
        if self.sequence and (sequence[0]<self.sequence[0] or sequence[1]<self.sequence[1]):
            self.reset();return self.empty('TIME_RESET')
        if self.sequence and sequence[0]==self.sequence[0] and sequence[1]!=self.sequence[1]:
            self.reset();return self.empty('INVALID')
        transport=world.get('_transport_ms',0)
        if type(transport) not in (int,float) or not math.isfinite(transport) or not 0<=transport<=5000:transport=5000
        age=raw['age_ms']+max(0,now-snapshot.get('received_at',0))*1000+transport
        if sequence==self.sequence and self.evidence_at is not None:
            age=max(age,(now-self.evidence_at)*1000)
        if (not raw['valid'] or not 0<=age<=1000 or raw['source_ms']>raw['sent_ms']
                or raw['sent_ms']-raw['source_ms']>1000):
            self.regions.reset();self.cached=[];self.mask=None
            return self.empty('STALE',age)
        if video_ms is not None and not -250<=video_ms-raw['source_ms']<=1250:
            self.reset();return self.empty('VIDEO_GAP',age)
        self.last_now=now
        if sequence!=self.sequence:
            vehicle_boxes=[track['raw'] for track in tracker.objects if track['cls'] in (2,5,7) and not track['held']
                           and abs((now-track['evidence_at'])*1000-age)<=350]
            regions=self.regions.update(mask,self.allowed,raw['source_ms']/1000,session=key,vehicle_boxes=vehicle_boxes)
            self.sequence=sequence;self.evidence_at=now-age/1000;self.mask=mask;self.cached=[]
            for region in regions:
                observed=self._project_mask(region['observed']);inferred=self._project_mask(region['inferred'])
                self.cached.append(dict(id=region['id'],confirmed=region['confirmed'],observed_map=observed,
                                        inferred_map=inferred,region=region))
        result=self.empty('LIVE',age)
        result['omitted']=int(np.count_nonzero(mask&~self.allowed)>0)
        result['skew_ms']=abs(world['object_ms']-raw['source_ms']) if world['object_valid'] else None
        can_associate=(result['skew_ms'] is not None and result['skew_ms']<=350
                       and 0<=world_view['ages'][1]<=1000 and age<=1000)
        gate_present='pedestrian' in state
        gate_people=aligned_pedestrians(state.get('pedestrian'),world,raw) if gate_present else None
        regions=[dict(item['region']) for item in self.cached]
        if gate_people is not None:
            for person in gate_people:
                if not person['contact'] or not person['confirmed']:continue
                column=round((person['box'][0]+person['box'][2])/20000*(SHAPE[1]-1))
                row=round(person['box'][3]/10000*(SHAPE[0]-1))
                for region in regions:
                    if region['observed'][row,column]:region['confirmed']=True
        path_lanes=[]
        if abs(world['lane_ms']-raw['source_ms'])<=350 and 0<=world_view['ages'][0]<=1000:
            path_lanes=[dict(role=lane['role'],quality=lane['quality']/10000.,points=lane['raw'].tolist())
                        for lane in tracker.lanes if not lane['held']]
        path=lane_corridor(path_lanes,age=0)
        if can_associate:
            for track in tracker.objects:
                if track['cls']!=0:continue
                person_age=now-track['evidence_at']
                aligned=abs(person_age*1000-age)<=350
                person=dict(id=track['id'],box=track['raw'].tolist(),held=track['held'] or person_age>.75 or not aligned,
                            age=0 if aligned else person_age)
                relation=classify_person(person,regions,path,self.allowed)
                relation.update(age_ms=age,source_skew_ms=result['skew_ms'])
                ground,projectable=self.projection.project([[(track['raw'][0]+track['raw'][2])/2,track['raw'][3]]])
                if projectable[0]:relation['ground_position']=ground[0].tolist()
                if gate_present:
                    matched=next((candidate for candidate in gate_people or [] if same_person(track['raw'],candidate['box'])),None)
                    supported=(matched is not None and matched['contact'] and matched['confirmed'] and
                               relation['contact']=='OBSERVED_FOOT_CONTACT')
                    if not supported:
                        relation.update(contact='UNKNOWN',region_id=None,reason='waiting_aligned_board_contact',raw_advisory='UNKNOWN')
                result['relations'].append(relation)
        for item in self.cached:
            visible=bool(item['observed_map'].any() or item['inferred_map'].any())
            if not visible:continue
            region=next(region for region in regions if region['id']==item['id'])
            people=[row['track_id'] for row in result['relations'] if row['region_id']==item['id']
                    and row['contact']=='OBSERVED_FOOT_CONTACT']
            result['regions'].append(dict(id=item['id'],confirmed=region['confirmed'],held=age>850,
                alpha=1. if age<=850 else .45,age_ms=age,persons=people,
                observed_map=item['observed_map'],inferred_map=item['inferred_map']))
        return result


class ObservedJunctionRenderer:
    def __init__(self,road_renderer):self.road_renderer=road_renderer

    @staticmethod
    def perception_objects(view):
        return view.get('objects',[]) if isinstance(view,dict) and view.get('status')=='LIVE' else []

    def frame(self,direction,scene,signal=0,held=False,perception=None):
        view=perception if isinstance(perception,dict) else dict(lanes=[],objects=[])
        return self.road_renderer.frame(view,direction=direction)
