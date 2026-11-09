"""Preset intersection with live approach lanes, crossing regions and targets."""
import math
import cv2
import numpy as np
from junction_scene import JunctionRenderer
from live_road import RoadRenderer


class HybridJunctionRenderer(JunctionRenderer):
    def __init__(self):
        super().__init__(dynamic_road=True)
        rows,columns=np.indices((self.HEIGHT,self.WIDTH),dtype=np.float64)
        difference=(columns-328)/7.2
        total=(rows-315)/4.
        scene_x=(difference+total)/2
        scene_y=(total-difference)/2
        self.scene_x=scene_x.astype(np.float32)
        self.scene_y=scene_y.astype(np.float32)
        lateral=(scene_x-4.8)/3.2
        forward=(13.-scene_y)/5.
        allowed=(np.abs(lateral)<=3.3)&(forward>=-.25)&(forward<=7.)
        depth=forward.copy()
        positive=forward>0
        depth[positive]=np.log1p(3*forward[positive])/math.log(22)*7
        self.map_x=np.where(allowed,328+lateral*(87-4*depth),-1).astype(np.float32)
        self.map_y=np.where(allowed,RoadRenderer.ORIGIN_Y-depth*57,-1).astype(np.float32)

    @staticmethod
    def lane_count(view):
        if not isinstance(view,dict) or view.get('status')!='LIVE':return None
        lanes=view.get('lane_bands')
        if not isinstance(lanes,dict) or lanes.get('status')!='OBSERVED':return None
        count=lanes.get('visible_lane_count')
        return count if type(count) is int and 1<=count<=8 else None

    def crosswalk_remap(self,region,field='observed_map'):
        empty=np.zeros((self.HEIGHT,self.WIDTH),dtype=bool)
        if not isinstance(region,dict) or field not in ('observed_map','inferred_map'):return empty
        mask=np.asarray(region.get(field))
        if mask.shape!=(RoadRenderer.HEIGHT,RoadRenderer.WIDTH) or mask.dtype.kind not in 'bu':return empty
        return cv2.remap((mask>0).astype(np.uint8),self.map_x,self.map_y,cv2.INTER_NEAREST,
                         borderMode=cv2.BORDER_CONSTANT,borderValue=0).astype(bool)

    @staticmethod
    def _regions(view):
        if not isinstance(view,dict) or view.get('status')!='LIVE':return []
        crosswalk=view.get('crosswalk')
        if not isinstance(crosswalk,dict) or crosswalk.get('status')!='LIVE':return []
        regions=crosswalk.get('regions')
        if not isinstance(regions,list):return []
        result=[]
        for region in regions:
            if not isinstance(region,dict):continue
            if type(region.get('id')) is not int or not 1<=region['id']<=2**64-1:continue
            if type(region.get('confirmed')) is not bool or type(region.get('held')) is not bool:continue
            alpha=region.get('alpha');age=region.get('age_ms')
            if type(alpha) not in (int,float) or not math.isfinite(alpha) or not 0<alpha<=1:continue
            if type(age) not in (int,float) or not math.isfinite(age) or not 0<=age<=1000:continue
            people=region.get('persons')
            if not isinstance(people,list) or any(type(person) is not int or person<=0 for person in people):continue
            result.append(region)
        return result

    @classmethod
    def perception_objects(cls,view):
        presentation=view.get('presentation') if isinstance(view,dict) else None
        if isinstance(presentation,dict) and presentation.get('display_only') is True:
            return [dict(actor,crosswalk_ids=()) for actor in presentation.get('actors',[]) if not actor.get('inferred')]
        objects=JunctionRenderer.perception_objects(view)
        regions={region['id']:region for region in cls._regions(view) if region['confirmed'] and not region['held']}
        crosswalk=view.get('crosswalk',{}) if isinstance(view,dict) else {}
        relations=crosswalk.get('relations',[]) if isinstance(crosswalk,dict) else []
        associations={};positions={}
        if isinstance(relations,list):
            for relation in relations:
                if not isinstance(relation,dict):continue
                identifier=relation.get('track_id');region_id=relation.get('region_id')
                if type(identifier) is not int or type(region_id) is not int:continue
                if relation.get('contact')!='OBSERVED_FOOT_CONTACT':continue
                if region_id in regions and identifier in regions[region_id]['persons']:
                    associations.setdefault(identifier,set()).add(region_id)
                    position=np.asarray(relation.get('ground_position'),dtype=float)
                    if position.shape==(2,) and np.isfinite(position).all() and abs(position[0])<=3.3 and -.25<=position[1]<=7:
                        positions[identifier]=(4.8+float(position[0])*3.2,13-float(position[1])*5)
        for obj in objects:
            obj['crosswalk_ids']=tuple(sorted(associations.get(obj['id'],()))) if obj['cls']==0 and not obj['held'] else ()
            if obj['crosswalk_ids'] and obj['id'] in positions:obj['ground']=positions[obj['id']]
        if isinstance(view,dict) and view.get('pedestrian_policy')=='crosswalk_contact':
            metadata={obj['id']:obj for obj in view.get('objects',[])}
            visible=[]
            for obj in objects:
                source=metadata[obj['id']]
                if obj['held'] or source.get('age_ms',10000)>850:continue
                if obj['cls']==0:
                    associated=bool(obj['crosswalk_ids']) and obj['id'] in positions
                    if not associated:
                        if source.get('samples',0)<2 or source.get('score',0)<.5:continue
                        obj['association_pending']=True
                        obj['alpha']=min(obj['alpha'],.6)
                elif source.get('samples',0)<2 or source.get('score',0)<.5:continue
                obj['label_visible']=obj['cls']==0 and not obj.get('association_pending')
                visible.append(obj)
            people=[obj for obj in visible if obj['cls']==0 and not obj.get('association_pending')]
            observing=[obj for obj in visible if obj['cls']==0 and obj.get('association_pending')]
            vehicles=sorted((obj for obj in visible if obj['cls']!=0),key=lambda obj:-obj['ground'][1])[:4]
            return people[:8]+observing[:3]+vehicles
        return objects

    def _draw_road(self,image,view):
        presentation=view.get('presentation') if isinstance(view,dict) else None
        if isinstance(presentation,dict) and presentation.get('display_only') is True:
            self._draw_completed_road(image,presentation)
            return
        count=self.lane_count(view)
        if count is not None:
            layer=image.copy()
            for lane in range(count):
                left=10.*lane/count;right=10.*(lane+1)/count
                self.polygon(layer,[(left,11.5,0),(right,11.5,0),(right,33,0),(left,33,0)],
                             (75,62,42) if lane%2==0 else (62,52,38))
            cv2.addWeighted(image,.62,layer,.38,0,dst=image)
            for boundary in range(1,count):
                lateral=10.*boundary/count
                for start in range(13,32,5):
                    self.line(image,[(lateral,start,0),(lateral,start+2.5,0)],(167,163,144),1)
            for lane in range(count):
                point=self.project(10.*(lane+.5)/count,29.)
                cv2.putText(image,'L%d'%(lane+1),(point[0]-6,point[1]),cv2.FONT_HERSHEY_SIMPLEX,.36,
                            (187,180,151),1,cv2.LINE_AA)
        occupants={}
        for obj in self.perception_objects(view):
            for identifier in obj['crosswalk_ids']:
                occupants.setdefault(identifier,[]).append(obj['id'])
        for region in self._regions(view):
            people=occupants.get(region['id'],[])
            occupied=bool(people)
            color=(226,150,233) if occupied else (194,206,190) if region['confirmed'] else (124,138,140)
            alpha=region['alpha']*(.85 if region['confirmed'] else .45)
            observed=self.crosswalk_remap(region)
            inferred=self.crosswalk_remap(region,'inferred_map')
            for mask,strength in ((observed,alpha*.3 if region['confirmed'] else alpha),(inferred,alpha*.25)):
                image[mask]=np.rint(image[mask]*(1-strength)+np.asarray(color)*strength).astype(np.uint8)
            rows,columns=np.nonzero(observed)
            if region['confirmed'] and len(rows)>2:
                horizontal=self.scene_x[rows,columns]
                vertical=self.scene_y[rows,columns]
                centered_x=horizontal-horizontal.mean()
                centered_y=vertical-vertical.mean()
                angle=.5*math.atan2(2*float(np.mean(centered_x*centered_y)),
                                   float(np.mean(centered_x*centered_x)-np.mean(centered_y*centered_y)))
                along=horizontal*math.cos(angle)+vertical*math.sin(angle)
                stripes=np.remainder(along,1.8)<1.
                stripe_rows=rows[stripes];stripe_columns=columns[stripes]
                image[stripe_rows,stripe_columns]=np.rint(image[stripe_rows,stripe_columns]*(1-alpha)
                    +np.asarray(color)*alpha).astype(np.uint8)
            contours,_=cv2.findContours(observed.astype(np.uint8),cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(image,contours,-1,color,1,cv2.LINE_AA)
            if len(rows):
                label='Z%d'%region['id']+(' ?' if not region['confirmed'] else '')
                if occupied:label+=':'+','.join('P%d'%person for person in people[:3])
                cv2.putText(image,label,(int(columns.min()),max(83,int(rows.min())-5)),
                            cv2.FONT_HERSHEY_SIMPLEX,.4,color,1,cv2.LINE_AA)

    def _target(self,image,obj):
        super()._target(image,obj)
        if obj.get('position_estimated'):
            point=self.project(*obj['ground'])
            color=(72,166,250) if obj.get('behavior') in ('CROSSING','CROSSING_HOLD') else (155,157,157)
            for start in (0,90,180,270):cv2.ellipse(image,point,(14,7),0,start,start+55,color,1,cv2.LINE_AA)
        if obj.get('behavior') in ('CROSSING','CROSSING_HOLD') and obj.get('motion_direction'):
            x,y=obj['ground'];direction=obj['motion_direction']
            cv2.arrowedLine(image,self.project(x,y,.12),self.project(x+direction*2.5,y,.12),
                            (72,166,250),2,cv2.LINE_AA,tipLength=.4)
        if obj.get('route_conflict'):
            point=self.project(*obj['ground'])
            cv2.ellipse(image,point,(17,9),0,0,360,(72,166,250),2,cv2.LINE_AA)
        if obj.get('predicted'):
            point=self.project(*obj['ground'])
            for start in (0,90,180,270):
                cv2.ellipse(image,point,(13,6),0,start,start+55,(170,158,99),1,cv2.LINE_AA)
        identifiers=obj.get('crosswalk_ids',())
        if identifiers:
            point=self.project(*obj['ground'])
            cv2.putText(image,','.join('Z%d'%identifier for identifier in identifiers[:3]),
                        (point[0]+10,point[1]-44),cv2.FONT_HERSHEY_SIMPLEX,.4,(226,150,233),1,cv2.LINE_AA)

    def _draw_completed_road(self,image,presentation):
        gate=presentation.get('route_gate',{})
        geometry=presentation.get('crossing_geometry')
        observed_roles={band['role'] for band in geometry.get('bands',[]) if band.get('role')} if geometry and geometry.get('status') in ('LIVE','SNAPSHOT') else set()
        crossing=gate.get('crossing')
        if gate.get('status') in ('YIELD','POSSIBLE','UNKNOWN') and crossing:
            bounds={'near':(-10.,8.6,10.,10.6),'far':(-10.,-10.6,10.,-8.6),
                    'left':(-10.6,-10.,-8.6,10.),'right':(8.6,-10.,10.6,10.)}
            a,b,c,d=bounds[crossing]
            color=(72,166,250) if gate['status']!='UNKNOWN' else (137,131,117)
            self.line(image,[(a,b,.08),(c,b,.08),(c,d,.08),(a,d,.08),(a,b,.08)],color,2)
        count=presentation['lane_count']
        for sign in (-1,1):
            for axis in (0,1):
                lanes=count if axis==0 and sign==1 else 2
                for boundary in range(1,lanes):
                    lateral=10.*boundary/lanes
                    for side in (-1,1):
                        for start in range(14,32,5):
                            points=[(side*lateral,sign*start,0),(side*lateral,sign*(start+2.5),0)]
                            if axis:points=[(forward,lateral,height) for lateral,forward,height in points]
                            self.line(image,points,(151,155,146),1)
                for stripe in np.arange(-9.,9.,1.65):
                    points=[(float(stripe),sign*8.3,0),(float(stripe+.85),sign*8.3,0),
                            (float(stripe+.85),sign*10.8,0),(float(stripe),sign*10.8,0)]
                    if axis:points=[(forward,lateral,height) for lateral,forward,height in points]
                    role=('right' if sign==1 else 'left') if axis else ('near' if sign==1 else 'far')
                    observed=role in observed_roles if geometry is not None else not presentation['crosswalk_inferred']
                    color=(195,207,192) if observed else (125,132,126)
                    self.polygon(image,points,color)
        for lane in range(count):
            lateral=10.*(lane+.5)/count
            first=self.project(lateral,26);last=self.project(lateral,22)
            cv2.arrowedLine(image,first,last,(194,194,175),2,cv2.LINE_AA,tipLength=.4)
        if presentation.get('frozen'):
            elapsed=presentation.get('simulation_seconds',0.)
            for actor in presentation.get('actors',[]):
                points=actor.get('trajectory',[])
                if actor['cls'] not in (0,1,3) or len(points)<2:continue
                if np.linalg.norm(np.asarray(points[-1])-points[0])<.2:continue
                color=(72,166,250) if actor.get('route_conflict') else (195,195,75)
                for index,(first,last) in enumerate(zip(points,points[1:])):
                    if index*(3.-elapsed)/(len(points)-1)<=1. or index%2==0:
                        self.line(image,[(*first,.15),(*last,.15)],color,2)
            for point in gate.get('conflict_points',[]):
                x,y=self.project(*point)
                cv2.line(image,(x-6,y-6),(x+6,y+6),(72,166,250),2,cv2.LINE_AA)
                cv2.line(image,(x-6,y+6),(x+6,y-6),(72,166,250),2,cv2.LINE_AA)
