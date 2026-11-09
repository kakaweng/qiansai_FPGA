"""Source-plane crossing association for the local, schematic rehearsal.

Roles are estimates from observed bands, never pedestrian image-x bins. Missing
geometry stays unresolved; it must not create a pedestrian on a template road.
No board control or metric reconstruction is performed here.
"""
import math
import cv2
import numpy as np
from crosswalk_relations import CrossingRegions
from live_crosswalk import decode_crosswalk,SHAPE,MODEL_VERSION


class JunctionAssociator:
    MAX_AGE_MS=1250
    MAX_SKEW_MS=350

    def __init__(self):
        self.extractor=CrossingRegions(SHAPE,max_gap=1.5,match_age=1.2)
        self.allowed=np.zeros(SHAPE,bool)
        # The extreme lower edge is frequently bonnet/reflected paint in PIE.
        # Omission here is uncertainty, never evidence that a crossing is clear.
        self.allowed[round(SHAPE[0]*.45):round(SHAPE[0]*.93)]=True
        self.reset()

    def reset(self):
        self.extractor.reset();self.key=None;self.sequence=None;self.evidence_at=None
        self.bands=[];self.status='WAITING';self.source_ms=None;self.encoded=None

    def _invalidate(self,status):
        # Keep source watermarks: rereading the same expired packet cannot give
        # old geometry a new local arrival time on the following UI refresh.
        self.extractor.reset();self.bands=[];self.status=status

    @staticmethod
    def _band(region):
        yy,xx=np.nonzero(region['observed'])
        points=np.column_stack((xx/(SHAPE[1]-1),yy/(SHAPE[0]-1)))
        center=np.mean(points,axis=0)
        _,vectors=np.linalg.eigh(np.cov(points.T));axis=vectors[:,-1]
        if axis[0]<0:axis=-axis
        # Use an image line only for local interpolation, not a ground-plane hull.
        slope=float(np.clip(axis[1]/max(.001,axis[0]),-2.,2.))
        low,high=np.percentile(points[:,0],[2,98])
        return dict(id=region['id'],observed=region['observed'],pixels=region['pixels'],
                    low=float(low),high=float(high),center=center,slope=slope,
                    role=None,confirmed=region['confirmed'])

    @staticmethod
    def _merge(bands):
        """Join only adjacent, collinear fragments; keep the observed pixels."""
        result=[]
        for band in sorted(bands,key=lambda row:row['low']):
            match=None
            for old in result:
                x=(max(old['low'],band['low'])+min(old['high'],band['high']))/2
                y1=old['center'][1]+old['slope']*(x-old['center'][0])
                y2=band['center'][1]+band['slope']*(x-band['center'][0])
                gap=max(0.,band['low']-old['high'],old['low']-band['high'])
                if gap<=.085 and abs(y1-y2)<=.025 and abs(old['slope']-band['slope'])<=.18:
                    match=old;break
            if match is None:result.append(band);continue
            weight=match['pixels']+band['pixels']
            match['center']=(match['center']*match['pixels']+band['center']*band['pixels'])/weight
            match['slope']=(match['slope']*match['pixels']+band['slope']*band['pixels'])/weight
            match.update(low=min(match['low'],band['low']),high=max(match['high'],band['high']),pixels=weight,
                         observed=match['observed']|band['observed'],confirmed=match['confirmed'] or band['confirmed'])
        return result

    def update(self,world,raw,session,now,elapsed=0.,video_ms=None):
        if not isinstance(raw,dict) or world is None:self.reset();return
        key=(session,world['producer'])
        if key!=self.key:self.reset();self.key=key
        age=raw.get('age_ms',-1)
        transport=world.get('_transport_ms',0)
        if type(transport) not in (int,float) or not math.isfinite(transport) or not 0<=transport<=5000:transport=5000
        sequence=(raw.get('source_seq'),raw.get('source_ms'))
        valid_numbers=all(type(value) is int and value>0 for value in sequence)
        age=age+max(0.,elapsed)*1000+transport if type(age) in (int,float) else -1
        if self.evidence_at is not None and sequence==self.sequence and age>=0:
            age=max(age,(now-self.evidence_at)*1000)
        if (not valid_numbers or raw.get('producer')!=world['producer'] or raw.get('valid') is not True or
                raw.get('held') is not False or raw.get('model_version')!=MODEL_VERSION or raw.get('version')!=1 or
                tuple(raw.get(field) for field in ('width','height','plane_width','plane_height'))!=(1920,1080,320,180) or
                not math.isfinite(age) or not 0<=age<=self.MAX_AGE_MS or
                type(raw.get('sent_ms')) is not int or not sequence[1]<=raw['sent_ms']<=sequence[1]+self.MAX_AGE_MS or
                sequence[1]>world['frame_ms'] or
                (video_ms is not None and not -250<=video_ms-sequence[1]<=self.MAX_AGE_MS)):
            self._invalidate('STALE');return
        if self.sequence and (sequence[0]<self.sequence[0] or sequence[1]<self.sequence[1] or
                              (sequence[0]==self.sequence[0] and sequence[1]!=self.sequence[1])):
            self._invalidate('REORDERED');return
        if sequence==self.sequence:
            if raw.get('mask_lsb_hex')!=self.encoded:self._invalidate('INVALID')
            return
        mask=decode_crosswalk(raw)
        if mask is None:self._invalidate('INVALID');return
        regions=self.extractor.update(mask,self.allowed,sequence[1]/1000,session=key)
        bands=self._merge([self._band(region) for region in regions])
        self.estimate_roles(bands)
        for band in bands:
            band['pixel_y'],band['pixel_x']=np.nonzero(band['observed'])
        self.bands=bands;self.sequence=sequence;self.source_ms=sequence[1];self.encoded=raw['mask_lsb_hex']
        self.evidence_at=now-age/1000;self.status='LIVE'

    @staticmethod
    def estimate_roles(bands):
        for band in bands:
            low,high=band['low'],band['high'];width=high-low;center=band['center']
            if width<.07 or abs(band['slope'])>.75:continue
            if low<=.48 and high>=.52 and width>=.18:
                band['role']='near' if center[1]>=.72 else 'far'
            elif high<.49 and band['slope']>=-.12:band['role']='left'
            elif low>.51 and band['slope']<=.12:band['role']='right'
        central=sorted((band for band in bands if band['role'] in ('near','far')),key=lambda row:row['center'][1])
        if len(central)>1:
            for band in central[:-1]:
                if central[-1]['center'][1]-band['center'][1]>=.10:band['role']='far'
                else:band['role']=None

    @classmethod
    def snapshot_geometry(cls,raw):
        """Spatial-only extraction after the caller verifies a static source.

        This object is detached from live time/permission state. No synthetic
        fresh packet is passed through the live validator.
        """
        result=cls();mask=decode_crosswalk(raw)
        if mask is None:return result
        regions=result.extractor.update(mask,result.allowed,raw['source_ms']/1000,session=('snapshot',raw['producer']))
        result.bands=result._merge([result._band(region) for region in regions])
        result.estimate_roles(result.bands)
        for band in result.bands:
            # A side crossing may extend slightly past image center during a
            # turn. Resolve that narrow dead-zone only in the frozen schematic.
            if band['role'] is None and .15<=band['high']-band['low']<=.5 and abs(band['slope'])<.35:
                if band['low']>=.42 and band['center'][0]>=.58:band['role']='right';band['role_estimated']=True
                elif band['high']<=.58 and band['center'][0]<=.42:band['role']='left';band['role_estimated']=True
            band['pixel_y'],band['pixel_x']=np.nonzero(band['observed'])
        result.sequence=(raw['source_seq'],raw['source_ms']);result.source_ms=raw['source_ms']
        result.status='LIVE'  # associate()'s spatial precondition; view is marked SNAPSHOT below.
        return result

    def associate(self,box,object_ms):
        result=dict(crossing=None,point=None,association='UNRESOLVED',region_id=None,
                    geometry_key=self.sequence,geometry_ms=self.source_ms)
        if self.status!='LIVE' or self.source_ms is None or abs(object_ms-self.source_ms)>self.MAX_SKEW_MS:return result
        box=np.asarray(box,dtype=float)
        if box.shape!=(4,) or not np.isfinite(box).all() or np.any((box<0)|(box>1)) or box[3]>=.99:return result
        h,w=SHAPE;x=(box[0]+box[2])*.5*(w-1);y=box[3]*(h-1)
        rx=float(np.clip((box[2]-box[0])*w*.55+2,4,24))
        ry=float(np.clip((box[3]-box[1])*h*.14+2,4,13))
        candidates=[]
        for band in self.bands:
            yy,xx=band['pixel_y'],band['pixel_x']
            if not len(xx):continue
            squared=((xx-x)/rx)**2+((yy-y)/ry)**2
            distance=float(np.min(squared))**.5
            support=int(np.count_nonzero(squared<=1.))
            if distance<=2.0:candidates.append((distance,band,support))
        if not candidates:
            result['association']='NO_NEARBY_SUPPORT';return result
        candidates.sort(key=lambda row:row[0]);distance,band,support=candidates[0]
        # Similar evidence for two different crossings cannot pick a confident role.
        if len(candidates)>1 and candidates[1][0]-distance<.35 and candidates[1][1]['role']!=band['role']:
            result['association']='AMBIGUOUS_BANDS';return result
        result.update(region_id=band['id'],association='FOOT_SUPPORT' if support>=3 else 'NEAR_SUPPORT')
        if support<3 and not band['confirmed']:
            result['association']='NEAR_UNCONFIRMED';return result
        role=band['role']
        if role is None:result['association']='UNRESOLVED_BAND';return result
        horizontal=x/(w-1)
        progress=float(np.clip((horizontal-band['low'])/max(.07,band['high']-band['low']),-.12,1.12))
        # Endpoints come from this band, not the full camera width or nearby people.
        along=-8.5+17.*progress
        if role in ('near','far'):point=(along,9.6 if role=='near' else -9.6)
        else:point=(-9.6 if role=='left' else 9.6,along if role=='left' else -along)
        result.update(crossing=role,point=np.array(point,dtype=float))
        return result

    def view(self):
        return dict(status=self.status,source_ms=self.source_ms,bands=[dict(id=band['id'],role=band['role'],
            low=band['low'],high=band['high'],center=band['center'].tolist(),slope=band['slope']) for band in self.bands])
