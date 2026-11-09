"""Lightweight 2.5D schematic renderer. Motion/state are supplied by the RK controller.

The road is a preset intersection, not a reconstruction of HDMI geometry.
No signal classification or driving permissions are computed here.
"""
import math
import cv2
import numpy as np


def route_path(direction):
    """Shared schematic route: rendering and conflict checks use identical geometry."""
    x = (1.6,4.8,8.0)[direction-1]
    approach = np.column_stack((np.full(40,x), np.linspace(28,13,40)))
    t=np.linspace(0,1,180)[:,None]
    controls = {1: ((x,13),(x,-4.8),(-13,-4.8),(-29,-4.8)),
                2: ((x,13),(x,0),(x,-14),(x,-29)),
                3: ((x,13),(x,4.8),(17,4.8),(29,4.8))}[direction]
    a,b,c,d=map(np.array,controls)
    crossing=(1-t)**3*a+3*(1-t)**2*t*b+3*(1-t)*t*t*c+t**3*d
    lengths=np.r_[0.,np.cumsum(np.linalg.norm(np.diff(crossing,axis=0),axis=1))]
    return approach,crossing,lengths/lengths[-1]


def route_pose(path,distance):
    approach,crossing,normalized=path
    if distance<15000:
        return np.array([approach[0,0],28-15*distance/15000]),np.array([0.,-1.])
    progress=min(1.,max(0.,(distance-15000)/45000))
    index=max(1,min(len(crossing)-1,int(np.searchsorted(normalized,progress))))
    ratio=(progress-normalized[index-1])/(normalized[index]-normalized[index-1])
    tangent=crossing[index]-crossing[index-1]
    return crossing[index-1]+ratio*tangent,tangent/np.linalg.norm(tangent)

SCENE_PHASES = {
    'READY': ('待开始', '选择车道后开始演示', 'action'),
    'APPROACH': ('接近路口', '车辆正在接近停止线', 'ok'),
    'SELECT_LIGHT': ('等待选灯', '点击视频中对应的 LIGHT', 'action'),
    'WAIT_RED': ('红灯等待', '停在停止线前，等待绿灯', 'ok'),
    'WAIT_YELLOW': ('黄灯等待', '保持停车，等待有效绿灯', 'hold'),
    'WAIT_SIGNAL': ('灯色待确认', '等待新的有效灯色证据', 'pending'),
    'WAIT_PEDESTRIAN': ('礼让行人', '检测到行人，保持停车礼让', 'hold'),
    'WAIT_PERCEPTION': ('等待行人检测', '等待新的有效行人检测结果', 'pending'),
    'RIGHT_GO': ('可右转', '右转演示继续，需让行直行车辆', 'observe'),
    'CROSSING': ('通过路口', '沿所选路线完成通行', 'ok'),
    'VIOLATION': ('违规通行', '红 / 黄灯强行通行报警', 'alarm'),
    'PAUSED': ('手动暂停', '按 G 继续当前场景', 'pending'),
    'DISCONNECTED': ('演示已暂停', '连接恢复后按 G 继续', 'stale'),
    'COMPLETE': ('本次完成', '按 R 重置后可再次演示', 'ok'),
}


def scene_state(raw, running, elapsed):
    """Validate board scene payload; stale snapshots never animate or show permission."""
    if not isinstance(raw, dict) or raw.get('version') != 1:
        return None
    required = ('enabled', 'active', 'crossed', 'violation')
    if any(type(raw.get(k)) is not bool for k in required):
        return None
    ranges = {'run_id': (0, 0xffffffff), 'distance_mm': (0, 60000),
              'stop_mm': (15000, 15000), 'end_mm': (60000, 60000), 'speed_mm_s': (0, 4000)}
    if any(type(raw.get(k)) is not int or not low <= raw[k] <= high for k, (low, high) in ranges.items()):
        return None
    if raw.get('phase') not in SCENE_PHASES:
        return None
    if raw['distance_mm'] > raw['stop_mm'] and not raw['crossed']:
        return None
    result = dict(raw)
    result['fresh'] = running and 0 <= elapsed <= .5
    if not result['fresh']:
        result.update(active=False, speed_mm_s=0, phase='DISCONNECTED')
    if not result['active']:
        result['speed_mm_s'] = 0
    # Short bounded extrapolation only smooths board motion, never crosses an unapproved line.
    cap = result['end_mm'] if result['crossed'] else result['stop_mm']
    result['draw_distance'] = min(cap, result['distance_mm'] +
                                  result['speed_mm_s'] * min(.15, max(0., elapsed)))
    return result


class JunctionRenderer:
    WIDTH, HEIGHT = 656, 648

    def __init__(self,dynamic_road=False):
        self.dynamic_road=dynamic_road
        self.base = self._background()
        self.paths = {d: self._path(d) for d in (1, 2, 3)}

    @staticmethod
    def project(x, y, z=0):
        return (int(round(float(328 + (x - y) * 7.2))),
                int(round(float(315 + (x + y) * 4.0 - z * 9))))

    def polygon(self, image, world, color):
        points = np.array([self.project(*p) for p in world], np.int32)
        cv2.fillPoly(image, [points], color, lineType=cv2.LINE_AA)

    def line(self, image, world, color, width=1):
        points = np.array([self.project(*p) for p in world], np.int32)
        cv2.polylines(image, [points], False, color, width, cv2.LINE_AA)

    def _block(self, image, x, y, w, h, z, roof, side):
        self.polygon(image, [(x,y,0),(x+w,y,0),(x+w,y,z),(x,y,z)], side)
        self.polygon(image, [(x+w,y,0),(x+w,y+h,0),(x+w,y+h,z),(x+w,y,z)], tuple(max(0,c-8) for c in side))
        self.polygon(image, [(x,y,z),(x+w,y,z),(x+w,y+h,z),(x,y+h,z)], roof)
        self.line(image, [(x,y,z),(x+w,y,z),(x+w,y+h,z),(x,y+h,z),(x,y,z)], (91,72,48))

    def _background(self):
        out = np.full((self.HEIGHT, self.WIDTH, 3), (40, 26, 16), np.uint8)
        # Ground, raised sidewalks, parks and a compact city block use the dashboard palette.
        self.polygon(out, [(-33,-33,0),(33,-33,0),(33,33,0),(-33,33,0)], (48,38,26))
        for x, y in ((-29,-29),(11,-29),(-29,11),(11,11)):
            self.polygon(out, [(x,y,0),(x+18,y,0),(x+18,y+18,0),(x,y+18,0)], (63,49,32))
            self._block(out, x+1,y+1,16,16,.3,(57,46,30),(49,37,25))
        road = (55,44,32)
        self.polygon(out, [(-10,-33,0),(10,-33,0),(10,33,0),(-10,33,0)], road)
        self.polygon(out, [(-33,-10,0),(33,-10,0),(33,10,0),(-33,10,0)], road)
        for edge in (-10,10):
            for lo, hi in ((-33,-10),(10,33)):
                self.line(out, [(edge,lo,0),(edge,hi,0)], (108,88,62), 2)
                self.line(out, [(lo,edge,0),(hi,edge,0)], (108,88,62), 2)
        for sign in (-1,1):
            # Yellow opposing-flow divider and three approach lanes.
            self.line(out, [(0,sign*12,0),(0,sign*33,0)], (72,176,210), 2)
            self.line(out, [(sign*12,0,0),(sign*33,0,0)], (72,176,210), 2)
            if not self.dynamic_road:
                for lane in (-6.4,-3.2,3.2,6.4):
                    for at in range(14,32,5):
                        self.line(out, [(lane,sign*at,0),(lane,sign*(at+2.5),0)], (150,143,127), 1)
                        self.line(out, [(sign*at,lane,0),(sign*(at+2.5),lane,0)], (150,143,127), 1)
                for at in np.arange(-8.8,9,2):
                    self.polygon(out, [(at,sign*8.7,0),(at+.9,sign*8.7,0),
                                       (at+.9,sign*10.5,0),(at,sign*10.5,0)], (194,190,174))
                    self.polygon(out, [(sign*8.7,at,0),(sign*8.7,at+.9,0),
                                       (sign*10.5,at+.9,0),(sign*10.5,at,0)], (194,190,174))
        self.line(out, [(0.4,11.2,0),(9.4,11.2,0)], (232,231,218), 4)
        self._block(out,-26,-27,11,10,3.2,(104,86,58),(68,57,37))
        self._block(out,16,-25,8,12,2,(88,75,48),(60,49,31))
        self._block(out,-27,16,9,8,1.4,(77,74,44),(48,50,29))
        for x,y in ((14,15),(19,21),(25,15),(-14,-23),(-23,13),(13,-14)):
            p=self.project(x,y,.2); top=self.project(x,y,1.3)
            cv2.line(out,p,top,(59,70,72),3,cv2.LINE_AA)
            cv2.ellipse(out,top,(10,8),0,0,360,(75,103,45),-1,cv2.LINE_AA)
            cv2.circle(out,(top[0]-2,top[1]-2),5,(94,129,56),-1,cv2.LINE_AA)
        # Static direction arrows on the three incoming lanes.
        if not self.dynamic_road:
            for direction,x in ((1,1.6),(2,4.8),(3,8.0)):
                a=self.project(x,23); b=self.project(x,19)
                if direction != 2: b=self.project(x+(-2 if direction==1 else 2),20)
                cv2.arrowedLine(out,a,b,(209,207,193),2,cv2.LINE_AA,tipLength=.4)
        return out

    def _path(self, direction):
        return route_path(direction)

    def pose(self, direction, distance):
        return route_pose(self.paths[direction],distance)

    @staticmethod
    def perception_objects(view):
        """Place observed relative targets in a fixed illustrative approach frame."""
        if not isinstance(view,dict) or view.get('status')!='LIVE': return []
        objects=view.get('objects')
        if not isinstance(objects,list) or len(objects)>40: return []
        result=[]
        for obj in objects:
            if not isinstance(obj,dict): continue
            identifier=obj.get('id'); cls=obj.get('cls'); alpha=obj.get('alpha')
            if type(identifier) is not int or not 1<=identifier<=2**64-1: continue
            if type(cls) is not int or cls not in (0,1,2,3,5,7): continue
            if type(obj.get('held')) is not bool: continue
            if type(alpha) not in (int,float,np.float32,np.float64) or not math.isfinite(alpha) or not 0<alpha<=1: continue
            try: position=np.asarray(obj.get('position'),dtype=float)
            except (TypeError,ValueError): continue
            if position.shape!=(2,) or not np.isfinite(position).all(): continue
            lateral,forward=position
            if not -3.3<=lateral<=3.3 or not -.25<=forward<=7: continue
            result.append(dict(id=identifier,cls=cls,held=obj['held'],alpha=float(alpha),
                               ground=(4.8+float(lateral)*3.2,13.-float(forward)*5.)))
        return result

    def _target(self,image,obj):
        target_x,target_y=obj['ground']; point=self.project(target_x,target_y)
        alpha=obj['alpha']; color=(226,150,233) if obj['cls']==0 else (200,201,83)
        if obj.get('association_pending'):color=(150,153,156)
        if obj.get('behavior') in ('CROSSING','CROSSING_HOLD'):color=(72,166,250)
        color=tuple(int(round(float(channel*alpha+background*(1-alpha))))
                    for channel,background in zip(color,(40,26,16)))
        cv2.ellipse(image,point,(11,5),0,0,360,(65,48,37),-1,cv2.LINE_AA)
        if obj['cls']==0:
            cv2.line(image,(point[0]-6,point[1]),(point[0],point[1]-15),color,4,cv2.LINE_AA)
            cv2.line(image,(point[0]+6,point[1]),(point[0],point[1]-15),color,4,cv2.LINE_AA)
            cv2.line(image,(point[0]-8,point[1]-15),(point[0]+8,point[1]-15),color,3,cv2.LINE_AA)
            cv2.line(image,(point[0],point[1]-12),(point[0],point[1]-27),color,5,cv2.LINE_AA)
            cv2.circle(image,(point[0],point[1]-35),6,color,-1,cv2.LINE_AA)
        elif obj['cls'] in (1,3):
            cv2.circle(image,(point[0]-7,point[1]),5,color,2,cv2.LINE_AA)
            cv2.circle(image,(point[0]+7,point[1]),5,color,2,cv2.LINE_AA)
            cv2.line(image,(point[0]-7,point[1]),(point[0],point[1]-15),color,3,cv2.LINE_AA)
            cv2.line(image,(point[0]+7,point[1]),(point[0],point[1]-15),color,3,cv2.LINE_AA)
            cv2.circle(image,(point[0],point[1]-23),5,color,-1,cv2.LINE_AA)
        else:
            length=4.4 if obj['cls'] in (5,7) else 3.2
            self._block(image,target_x-.75,target_y-length/2,1.5,length,1.4,color,
                        tuple(int(channel*.6) for channel in color))
        label=('P' if obj['cls']==0 else 'B' if obj['cls'] in (1,3) else 'V')+str(obj['id'])+(' ~' if obj['held'] else '')
        if obj.get('label_visible',True):
            cv2.putText(image,label,(point[0]+10,point[1]-29),cv2.FONT_HERSHEY_SIMPLEX,.45,color,1,cv2.LINE_AA)

    def _draw_road(self,image,perception):
        pass

    def frame(self, direction, scene, signal=0, held=False, perception=None):
        image=self.base.copy()
        self._draw_road(image,perception)
        direction=direction if direction in (1,2,3) else 2
        approach, crossing,_=self.paths[direction]
        path=np.vstack((approach,crossing))
        points=np.array([self.project(x,y,.04) for x,y in path],np.int32)
        color=(111,113,248) if scene.get('violation') else (240,170,52)
        cv2.polylines(image,[points],False,tuple(int(c*.3) for c in color),16,cv2.LINE_AA)
        cv2.polylines(image,[points],False,color,3,cv2.LINE_AA)
        cv2.arrowedLine(image,tuple(points[-10]),tuple(points[-1]),color,3,cv2.LINE_AA,tipLength=.7)
        # Signal associated by the user with this route (schematic placement).
        base=self.project(11,10,0); head=self.project(11,10,7)
        cv2.line(image,base,head,(145,133,107),3,cv2.LINE_AA)
        hx,hy=head
        cv2.rectangle(image,(hx-14,hy-48),(hx+14,hy+8),(23,16,10),-1)
        colors=((82,90,106),(82,90,255),(49,197,255),(138,230,46))
        for lamp in (1,2,3):
            lit=signal==lamp
            col=colors[lamp] if lit else tuple(c//6 for c in colors[lamp])
            if lit and held: col=tuple(int(c*.6) for c in col)
            cv2.circle(image,(hx,hy-38+(lamp-1)*17),6,col,-1,cv2.LINE_AA)
        center,tangent=self.pose(direction,scene.get('draw_distance',scene.get('distance_mm',0)))
        objects=sorted(self.perception_objects(perception),key=lambda obj:self.project(*obj['ground'])[1])
        depth=self.project(*center)[1]
        for obj in objects:
            if self.project(*obj['ground'])[1]<=depth: self._target(image,obj)
        side=np.array([-tangent[1],tangent[0]])
        def quad(width,length,z):
            return [tuple(center+side*s*width+tangent*f*length)+(z,) for s,f in ((-1,-1),(1,-1),(1,1),(-1,1))]
        shadow=[(x+.5,y+.4,z) for x,y,z in quad(1.35,2.6,.02)]
        self.polygon(image,shadow,(25,21,15))
        body=quad(1.05,2.2,.45); roof=quad(.88,1.9,1.9)
        for i in range(4):
            self.polygon(image,[body[i],body[(i+1)%4],roof[(i+1)%4],roof[i]],(153,105,49))
        self.polygon(image,roof,(237,178,90))
        self.line(image,roof+[roof[0]],(255,227,168),1)
        wind=[tuple(center+side*s*.74+tangent*f)+(1.95,) for s,f in ((-1,.5),(1,.5),(1,1.3),(-1,1.3))]
        self.polygon(image,wind,(100,67,32))
        rear=[tuple(center+side*s*.74+tangent*f)+(1.95,) for s,f in ((-1,-1.5),(1,-1.5),(1,-1),(-1,-1))]
        self.polygon(image,rear,(104,70,35))
        if scene.get('violation'):
            px,py=self.project(*center)
            cv2.ellipse(image,(px,py),(31,17),0,0,360,(113,113,248),2,cv2.LINE_AA)
        for obj in objects:
            if self.project(*obj['ground'])[1]>depth: self._target(image,obj)
        return image
