"""Ego-fixed perception view alongside the existing junction simulation UI."""
import time
import math
from pathlib import Path
import cv2
import numpy as np
from PIL import Image,ImageDraw
from front_junction_preview import JunctionClient,JunctionDashboard,SceneLayout,RecognitionLayout
from front_p1_preview import FrontP1Dashboard,BG,HEADER,CARD,BORDER,TEXT,MUTED,DIM,KINDS
from live_road import GroundProjection,RoadTracker,RoadRenderer,validate_world
from live_crosswalk import CrosswalkMap
from hybrid_junction import HybridJunctionRenderer
from junction_presentation import JunctionPresentation
from frozen_junction import FrozenJunction

LABELS={'start':'开始检测','stop':'停止检测','left':'请求左变道','right':'请求右变道','cancel':'取消请求',
        'bind':'点选交通灯','unbind':'恢复自动','live_toggle':'大画面','scene_open':'路口演示',
        'calibrate':'地面标定','content_roi':'视频区域','reset_projection':'默认示意'}
HINTS={'start':'空格','stop':'X','left':'A','right':'D','cancel':'K','bind':'T','unbind':'C',
       'live_toggle':'V','scene_open':'M','calibrate':'H','content_roi':'U','reset_projection':'O'}


class LiveClient(JunctionClient):
    def request(self,route,post=False):
        start=time.monotonic()
        result=super().request(route,post)
        if route=='/api/status' and isinstance(result,dict) and isinstance(result.get('world'),dict):
            # Add the full round-trip as a conservative transport-age allowance.
            result['world']['_transport_ms']=max(0,(time.monotonic()-start)*1000)
        return result


class LiveLayout(SceneLayout):
    GROUPS=((16,'识别控制'),(180,'变道请求'),(552,'视频与地面'),(1000,'视图'))
    BUTTONS={'start':(16,804,156,844),'stop':(16,854,156,890),
             'left':(180,804,348,844),'right':(364,804,532,844),'cancel':(180,854,532,890),
             'calibrate':(552,804,760,844),'content_roi':(776,804,984,844),
             'bind':(552,854,686,890),'unbind':(696,854,830,890),'reset_projection':(840,854,984,890),
             'live_toggle':(1000,804,1168,844),'scene_open':(1000,854,1168,890)}


STATUS={'LIVE':('ok','实时映射'),'WAITING':('pending','等待感知接口'),
        'DISCONNECTED':('stale','等待检测与连接'),'STALE':('stale','感知结果过期'),
        'VIDEO_GAP':('stale','画面时间不匹配'),'TIME_RESET':('pending','时间变化，重新跟踪')}
CALIBRATION_STEPS=('点击同一车道的远端左边界','点击同一车道的远端右边界',
                   '点击同一车道的近端右边界','点击同一车道的近端左边界')


def anticipation_view(snapshot,video_fresh,now,video_ms=None):
    state=snapshot.get('state') or {}
    if 'anticipation' not in state: return None
    result=dict(status='INVALID',persons=[],age_ms=-1,eligibility='unverified',no_control=True)
    raw=state['anticipation']
    if not isinstance(raw,dict): return result
    if (raw.get('model_id')!='pie_anticipation_v2_20261101' or raw.get('device')!='RK3568_CPU'
            or raw.get('no_control') is not True or raw.get('eligibility')!='unverified'):
        return result
    if type(raw.get('age_ms')) is not int or not -1<=raw['age_ms']<=100000: return result
    people=raw.get('persons'); reason=raw.get('reason')
    if not isinstance(people,list) or len(people)>40 or not isinstance(reason,str) or len(reason)>120: return result
    identifiers=set()
    for person in people:
        if not isinstance(person,dict): return result
        identifier=person.get('id'); box=person.get('box'); samples=person.get('samples'); status=person.get('status')
        if type(identifier) is not int or not 1<=identifier<=2**64-1 or identifier in identifiers: return result
        identifiers.add(identifier)
        if (not isinstance(box,list) or len(box)!=4 or any(type(value) is not int or not 0<=value<=10000 for value in box)
                or box[0]>=box[2] or box[1]>=box[3]): return result
        if type(samples) is not int or not 0<=samples<=6: return result
        if status not in ('SCORED','WAITING_HISTORY','HISTORY_SHORT','CADENCE','AMBIGUOUS'): return result
        if status=='SCORED':
            scores=(person.get('score06'),person.get('score1'))
            if samples!=6 or any(type(value) not in (int,float) or not math.isfinite(value) for value in scores): return result
            if not 0<=scores[0]<=scores[1]<=1: return result
        elif 'score06' in person or 'score1' in person: return result
    received=snapshot.get('received_at')
    if type(received) not in (int,float) or not math.isfinite(received): return result
    elapsed=now-received
    if not video_fresh or snapshot.get('connected') is not True or not 0<=elapsed<=.5 or state.get('state')!='RUNNING':
        result['status']='DISCONNECTED'; return result
    world=validate_world(state.get('world'))
    if world is None:
        result['status']='WAITING'; return result
    transport=world.get('_transport_ms',0)
    if type(transport) not in (int,float) or not math.isfinite(transport) or not 0<=transport<=5000: transport=5000
    extra=elapsed*1000+transport
    if (not world['live'] or not 0<=world['report_age_ms']+extra<=750 or not world['object_valid']
            or not 0<=world['object_age_ms']+extra<=750 or not 0<=raw['age_ms']+extra<=750
            or reason in ('STALE','STOPPED')):
        result['status']='STALE'; return result
    if video_ms is not None and (not -250<=video_ms-world['frame_ms']<=1000 or not -250<=video_ms-world['object_ms']<=1000):
        result['status']='VIDEO_GAP'; return result
    result.update(status=reason if reason in ('NO_PERSON','WAITING_HISTORY','SCORED','CADENCE','AMBIGUOUS') else 'WAITING_HISTORY',
                  persons=[dict(person) for person in people],age_ms=raw['age_ms']+extra)
    return result


class LiveDashboard(JunctionDashboard):
    def __init__(self,client,font_path=None,front_only=True,projection_path=None):
        self.live_mode=True; self.calibration=None; self.frozen_video=None; self.calibration_points=[]
        self.projection_path=Path(projection_path) if projection_path else Path(__file__).resolve().parent/'front_ground_projection.json'
        self.projection=GroundProjection()
        error=''
        if self.projection_path.exists():
            try: self.projection=GroundProjection.load(self.projection_path)
            except (OSError,ValueError,KeyError,TypeError) as exc: error='标定文件不可用，已使用默认示意：'+str(exc)
        self.tracker=RoadTracker(self.projection); self.road_renderer=RoadRenderer()
        self.display_tracker=RoadTracker(self.projection,object_history=True)
        self.crosswalk_map=CrosswalkMap(self.projection)
        self.presentation=JunctionPresentation();self.presentation_scene_active=False
        self.freezer=FrozenJunction();self.freeze_mode=False;self.local_scene_override=None
        self.world_view=self.tracker.view(0); self.latest_video=None
        self.anticipation=None
        super().__init__(client,font_path,front_only)
        self.renderer=HybridJunctionRenderer()
        self.local_notice=error

    def _set_layout(self,enabled):
        desired=SceneLayout if enabled else LiveLayout if self.live_mode else RecognitionLayout
        if type(self.layout) is not desired:
            self.layout=desired(); self.drag_start=self.drag_end=None; self.selecting=False; self.cache_key=None
            self.calibration=None; self.frozen_video=None
        if desired is LiveLayout:
            self.buttons=[(box,LABELS[action],action) for action,box in self.layout.BUTTONS.items()]
        elif desired is RecognitionLayout:
            self.buttons=[(box,('实时映射' if action=='scene_open' else LABELS.get(action,action)),
                           'live_toggle' if action=='scene_open' else action) for action,box in self.layout.BUTTONS.items()]
        else:
            from front_junction_preview import LABELS as scene_labels
            self.layout.BUTTONS=dict(SceneLayout.BUTTONS)
            labels=dict(scene_labels)
            if self.freeze_mode:
                self.layout.BUTTONS['scene_capture']=self.layout.BUTTONS.pop('scene_force')
                self.layout.BUTTONS.pop('bind');self.layout.BUTTONS.pop('unbind')
                labels.update(scene_capture='冻结并分析',scene_start='播放 / 继续',scene_pause='暂停推演',scene_reset='重播此快照')
            self.buttons=[(box,labels[action],action) for action,box in self.layout.BUTTONS.items()]

    def _cancel_calibration(self):
        self.calibration=None; self.frozen_video=None; self.calibration_points=[]
        self.drag_start=self.drag_end=None; self.cache_key=None

    @staticmethod
    def canvas_patch(canvas,patch,x,y):
        x=max(0,min(canvas.shape[1]-patch.shape[1],int(x)))
        y=max(0,min(canvas.shape[0]-patch.shape[0],int(y)))
        canvas[y:y+patch.shape[0],x:x+patch.shape[1]]=patch

    def handle_action(self,action):
        if action=='scene_open':
            self.freeze_mode=True;self.local_scene_override=True;self.freezer.clear()
            self.freezer.direction=self.presentation.direction
            self._cancel_calibration();self._set_layout(True)
            self.local_notice='先在 Windows 暂停原视频，再按 N 分析当前场景'
            return
        if action=='scene_close':
            self.freeze_mode=False;self.local_scene_override=False;self.freezer.clear()
            self.presentation.action('scene_pause');self._cancel_calibration();self._set_layout(False)
            self.local_notice='已返回实时识别，可继续播放原视频'
            return
        if action=='scene_capture':
            if not self.freeze_mode:self.handle_action('scene_open')
            self.freezer.request(self.presentation.last_now or time.monotonic(),self.freezer.last_video_ms)
            self.cache_key=None;self.local_notice='等待暂停画面的检测完成'
            return
        if self.freeze_mode and action in ('turn_left','straight','turn_right','scene_start','scene_pause','scene_reset','scene_force'):
            if action=='scene_force':self.local_notice='冻结预演中使用 N 分析、G 播放、R 重播'
            else:
                self.freezer.action(action)
                self.local_notice='请先按 N 固定场景' if self.freezer.snapshot is None else '冻结快照的本地轨迹推演'
            self.cache_key=None
            return
        if type(self.layout) is SceneLayout and action in ('scene_start','scene_pause','scene_reset','scene_force'):
            self.presentation.action(action)
            self.flash=(action,time.monotonic());self.cache_key=None
            self.local_notice='本地场景预演，不改变视频与板端判定'
            return
        if action in ('turn_left','straight','turn_right'):
            self.presentation.action(action)
        if action=='scene_close':self.presentation.action('scene_pause')
        if action=='live_toggle':
            self._cancel_calibration(); self.live_mode=not self.live_mode
            self.tracker.reset(); self.display_tracker.reset(); self.crosswalk_map.reset(); self.local_notice=''; self._set_layout(False); return
        if action in ('calibrate','content_roi'):
            if self.calibration:
                self._cancel_calibration(); self.local_notice='已取消标定。'; return
            if self.latest_video is None or not (self.last_model or {}).get('live'):
                self.local_notice='请先开始检测，等待实时画面。'; return
            self.calibration=action; self.frozen_video=self.latest_video.copy(); self.calibration_points=[]
            self.drag_start=self.drag_end=None; self.selecting=False
            self.local_notice=CALIBRATION_STEPS[0] if action=='calibrate' else '拖框选完整视频内容，排除播放器边框和任务栏'
            self.cache_key=None; return
        if action=='reset_projection':
            self._cancel_calibration(); self._save_projection(GroundProjection())
            self.local_notice='已恢复全画面默认示意；换视频后可重新标定。'; return
        self._cancel_calibration()
        super().handle_action(action)

    def _save_projection(self,projection):
        try: projection.save(self.projection_path)
        except OSError as exc:
            self.local_notice='标定保存失败：'+str(exc); return False
        self.projection=projection; self.tracker.projection=projection; self.tracker.reset(); self.cache_key=None
        self.display_tracker.projection=projection;self.display_tracker.reset()
        self.crosswalk_map.projection=projection;self.crosswalk_map.reset()
        return True

    def handle_fpga_route(self,action):
        if action not in ('turn_left','straight','turn_right') or self.calibration:return False
        if not self.freeze_mode:self.handle_action('scene_open')
        self.handle_action(action)
        return True

    def handle_key(self,key):
        if ord('A')<=key<=ord('Z'): key+=32
        if key==ord('n'):
            self.handle_action('scene_capture');return
        if key==ord('v'):
            if self.scene and self.scene['enabled']:
                self.live_mode=True; self.handle_action('scene_close')
            else: self.handle_action('live_toggle')
            return
        if type(self.layout) is LiveLayout and key in (ord('h'),ord('u'),ord('o')):
            self.handle_action({ord('h'):'calibrate',ord('u'):'content_roi',ord('o'):'reset_projection'}[key]); return
        super().handle_key(key)

    def click(self,event,x,y,flags=None,parameter=None):
        if self.freeze_mode:
            if event==1:
                action=self.action_at(x,y)
                if action:self.handle_action(action)
            return
        if not self.calibration: return super().click(event,x,y,flags,parameter)
        if event==1:
            action=self.action_at(x,y)
            if action: self.handle_action(action); return
            if not self.layout.inside_video(x,y): return
            if self.calibration=='content_roi': self.drag_start=self.drag_end=(x,y); return
            self.calibration_points.append(np.array(self.layout.to_norm(x,y))/10000)
            if len(self.calibration_points)<4:
                self.local_notice=CALIBRATION_STEPS[len(self.calibration_points)]; return
            try:
                projection=GroundProjection(self.projection.roi,self.calibration_points,True)
                if self._save_projection(projection): self.local_notice='地面映射已保存；画面恢复实时，距离仍为相对示意。'
            except ValueError as exc: self.local_notice='标定未保存：'+str(exc)
            self._cancel_calibration()
        elif event==0 and self.drag_start is not None: self.drag_end=self.layout.clamp(x,y)
        elif event==4 and self.drag_start is not None:
            a=np.array(self.layout.to_norm(*self.drag_start))/10000
            b=np.array(self.layout.to_norm(x,y))/10000
            roi=[min(a[0],b[0]),min(a[1],b[1]),max(a[0],b[0]),max(a[1],b[1])]
            try:
                if self._save_projection(GroundProjection(roi=roi)):
                    self.local_notice='视频区域已保存；按 H 标定地面。'
            except ValueError as exc: self.local_notice='区域未保存：'+str(exc)
            self._cancel_calibration()

    def _draw_header(self,draw,view):
        if type(self.layout) is not LiveLayout: return super()._draw_header(draw,view)
        draw.rectangle((0,0,1600,56),fill=HEADER); draw.line((0,56,1600,56),fill=BORDER)
        width=self._text(draw,24,28,'实时道路 · 2.5D 感知',self.bold[24])
        self._pill(draw,24+width+22,28,*view['badge'],self.bold[16],'left')
        self._text(draw,640,28,'本车固定  /  真实目标  /  相对位置',self.regular[15],MUTED)
        self._text(draw,1584,28,'V 大画面 · M 路口演示 · S 截图',self.regular[15],MUTED,'right')

    def _draw_buttons(self,draw,view):
        if type(self.layout) is SceneLayout:
            super()._draw_buttons(draw,view)
            if self.freeze_mode:self._text(draw,180,872,'先暂停原视频，再按 N 分析',self.regular[15],MUTED)
            return
        for x,title in self.layout.GROUPS: self._text(draw,x,791,title,self.bold[15],MUTED)
        for (left,top,right,bottom),label,action in self.buttons:
            primary=action in ('start','live_toggle'); selected=action==self.calibration
            self._rrect(draw,(left,top,right,bottom),8,fill='#1e3a6a' if primary else '#1a2538',outline='#93c5fd' if selected else '#2f405f')
            self._text(draw,left+12,(top+bottom)/2,label,self.bold[16],TEXT,limit=right-left-42)
            self._text(draw,right-10,(top+bottom)/2,HINTS[action],self.bold[13],MUTED,'right')

    def _draw_direction(self,draw,view):
        if type(self.layout) is not LiveLayout: return super()._draw_direction(draw,view)
        left,top,_,_=self._card(draw,'direction','空间映射',('observe','相对位置'))
        self._text(draw,left+18,top+61,'本车固定 · 周围感知更新',self.bold[22])
        self._text(draw,left+18,top+98,'仅显示前视范围内可定位的目标。',self.regular[16],MUTED)
        self._text(draw,left+18,top+128,'右转根据横道接触关系提示礼让。',self.regular[15],DIM)

    def _draw_speed(self,draw,view):
        if self.freeze_mode and type(self.layout) is SceneLayout:
            left,top,_,_=self._card(draw,'speed','推演时间',view['speed_tag'])
            width=self._text(draw,left+18,top+65,'%.1f'%view['simulation_seconds'],self.bold[56])
            self._text(draw,left+width+27,top+77,'/ 3.0 秒',self.regular[18],MUTED)
            self._text(draw,left+180,top+57,view['scene_title'],self.bold[18],limit=180)
            self._text(draw,left+180,top+82,'示意推演，非实测速度',self.regular[14],DIM)
            return
        if type(self.layout) is not LiveLayout: return super()._draw_speed(draw,view)
        left,top,_,_=self._card(draw,'speed','显示策略',('ok','独立刷新'))
        self._text(draw,left+18,top+57,'跟踪 · 平滑 · 短时保持',self.bold[22])
        self._text(draw,left+18,top+83,'变道判定继续采用板端确认结果',self.regular[15],MUTED)

    def _draw_light(self,draw,view):
        if not view.get('presentation_mode'):return super()._draw_light(draw,view)
        left,top,right,bottom=self._card(draw,'light','场景感知',('observe','含推测'))
        self._text(draw,left+18,top+65,'感知更新 · 场景补全',self.bold[24])
        self._text(draw,left+18,top+102,'行人 %d    车辆 %d'%view['presentation_counts'],self.bold[18])
        self._text(draw,left+18,top+137,view['presentation_note'],self.regular[15],MUTED,limit=right-left-36)
        self._text(draw,left+18,top+164,'橙色：横穿运动 · 虚环：位置含推测',self.regular[14],DIM)

    def prepare_scene_view(self,model,view,snapshot,video_fresh,now):
        enabled=view['scene_enabled']
        if not enabled:
            self.presentation_scene_active=False
            return
        if not self.presentation_scene_active and not self.freeze_mode:
            direction=(model.get('p1') or {}).get('direction',3)
            if direction in (1,2,3):self.presentation.direction=direction
        self.presentation_scene_active=True
        completed=self.world_view.get('presentation',{})
        self.scene=self.freezer.scene() if self.freeze_mode else self.presentation.scene()
        phase=self.scene['phase']
        states={
            'READY':('选择路线，开始预演','J / I / L 选择路线，G 播放预演','action'),
            'APPROACH':('正在接近路口','感知驱动的场景预演，位置含推测','ok'),
            'CROSSING':('正在通过路口','沿选择路线预演，持续检查相关横道','ok'),
            'WAIT_PEDESTRIAN':('停车礼让',completed.get('route_gate',{}).get('detail','所选路线有行人，保持停车礼让'),'hold'),
            'WAIT_PERCEPTION':('路线冲突待确认',completed.get('route_gate',{}).get('detail','等待新的行人检测，保持停车'),'pending'),
            'WAIT_RED':('红灯等待预演','有效红灯参与场景，等待灯色更新','hold'),
            'WAIT_YELLOW':('黄灯等待预演','有效黄灯参与场景，等待灯色更新','hold'),
            'PAUSED':('预演已暂停','G 继续，R 回到起点','pending'),
            'DISCONNECTED':('连接中断，预演暂停','连接恢复后按 G 继续','stale'),
            'COMPLETE':('预演完成','R 重置场景，可切换路线再次预演','ok'),
            'VIOLATION':('违规预演','演示强行通行，不向板端发送行驶许可','alarm'),
        }
        title,detail,kind=states[phase]
        actors=completed.get('actors',[])
        counts=(sum(actor['cls']==0 for actor in actors),sum(actor['cls']!=0 for actor in actors))
        predicted=sum(actor.get('predicted',False) for actor in actors)
        note=('短时预测 %d 个目标'%predicted) if predicted else '目标跟随观测平滑更新' if actors else '等待目标，保留完整路口示意'
        gate=completed.get('route_gate',{})
        unresolved=completed.get('unresolved_people',0)
        crossing_count=sum(actor.get('behavior')=='CROSSING' for actor in actors if actor['cls']==0)
        if crossing_count:note='横穿中 %d 人 · 运动推测'%crossing_count
        elif unresolved:note='位置含推测 %d 人 · 保留显示'%unresolved
        elif phase not in ('READY','PAUSED') and gate.get('detail'):note=gate['detail']
        labels={'CROSSING':'横穿中','CROSSING_HOLD':'横穿短时保持','STILL':'静止观测','MOVING':'移动中','OBSERVING':'行为待确认'}
        behavior_rows=tuple((actor['id'],labels.get(actor.get('behavior'),'行为待确认'),
                            ('左 → 右' if actor.get('motion_direction',0)>0 else '右 → 左')
                            if actor.get('behavior') in ('CROSSING','CROSSING_HOLD') else '',
                            actor.get('position_estimated',False),actor.get('behavior') in ('CROSSING','CROSSING_HOLD'))
                           for actor in actors if actor['cls']==0)[:3]
        prediction=self.anticipation
        pie_text='PIE：未收到板端预测接口'
        if prediction is not None:
            scored=[person for person in prediction['persons'] if person['status']=='SCORED']
            if scored:
                selected=max(scored,key=lambda person:person['score1'])
                pie_text='PIE 板端 P%d：0.6s %.2f / 1s %.2f'%(selected['id'],selected['score06'],selected['score1'])
            else:
                pie_text={'CADENCE':'PIE：采样间隔不符，预测未就绪','AMBIGUOUS':'PIE：目标关联待确认',
                          'STALE':'PIE：结果已过期，分数已清除','WAITING_HISTORY':'PIE：等待有效历史 %d/6'%max(
                              [person['samples'] for person in prediction['persons']]+[0]),
                          'NO_PERSON':'PIE：当前没有可用轨迹','DISCONNECTED':'PIE：等待检测与连接'}.get(
                              prediction['status'],'PIE：等待有效预测接口')
        view.update(presentation_mode=True,presentation_counts=counts,presentation_note=note,
            behavior_rows=behavior_rows,pie_scene_text=pie_text,
            scene_title=title,scene_detail=detail,scene_kind=kind,
            direction=self.freezer.direction if self.freeze_mode else self.presentation.direction,
            speed=round(self.scene['speed_mm_s']*.0036),speed_tag=(kind,title),armed=self.scene['active'],
            people_spatial=False,people_state='PRESENTATION',people_feedback=(),people_total=None,people_relevant=0)
        if not view['lane_alarm']:view['banner']=(kind,detail)
        buttons=[]
        for action,status in view['buttons']:
            if action in ('turn_left','straight','turn_right'):
                status='selected' if action==('turn_left','straight','turn_right')[view['direction']-1] else ''
            buttons.append((action,status))
        view['buttons']=tuple(buttons)
        if self.freeze_mode:
            titles={'IDLE':('等待冻结场景','先在 Windows 暂停视频，再按 N 分析','action'),
                    'ANALYZING':('正在分析场景',self.freezer.detail,'pending'),
                    'ERROR':('场景尚未就绪',self.freezer.detail,'pending'),
                    'READY':('快照已就绪','J / I / L 选择路线，G 播放三秒推演','action'),
                    'PLAYING':('正在推演',gate.get('detail','沿冻结场景进行短时推演'),'hold' if gate.get('status') in ('YIELD','STOP_SIGNAL') else 'observe'),
                    'PAUSED':('推演已暂停','G 继续，R 重播同一快照','pending'),
                    'COMPLETE':('三秒推演结束',gate.get('detail','可切换方向比较，R 回到快照起点'),'hold' if gate.get('status') in ('YIELD','STOP_SIGNAL') else 'pending' if gate.get('status')=='UNKNOWN' else 'observe')}
            title,detail,kind=titles[self.freezer.status]
            if self.freezer.status=='PLAYING' and self.scene['phase']=='WAIT_PEDESTRIAN':title='停车礼让'
            if self.freezer.status=='PLAYING' and self.scene['phase'] in ('WAIT_RED','WAIT_YELLOW'):title='灯前等待'
            view.update(scene_title=title,scene_detail=detail,scene_kind=kind,speed_tag=(kind,title),
                        badge=('observe','冻结快照' if self.freezer.snapshot else '场景分析'),
                        banner=(kind,detail),simulation_seconds=round(self.freezer.elapsed,1),
                        capture_status=self.freezer.status,
                        presentation_note='暂停前运动 + 固定位置 · 后续轨迹含推测')
            if self.freezer.snapshot is not None:view.update(signal=self.freezer.snapshot['signal'],signal_held=False)
            view['pie_scene_text']='冻结时 '+view['pie_scene_text'] if self.freezer.snapshot is not None else '分析前需保留暂停前的行人运动历史'

    def render_panel(self,model,view=None):
        if type(self.layout) is SceneLayout:
            image=super().render_panel(model,view)
            pil=Image.fromarray(image[:,:,::-1]);draw=ImageDraw.Draw(pil)
            left,top,right,bottom=self.layout.SCENE
            draw.rectangle((left+1,top+1,right-1,top+67),fill=CARD)
            self._text(draw,left+20,top+25,'路口预演 · 冻结场景' if self.freeze_mode else '路口预演 · 感知增强',self.bold[22])
            self._text(draw,left+20,top+53,'固定位置 / 暂停前运动 / 三秒轨迹推演' if self.freeze_mode else '道路补全 / 目标平滑与短时预测 / 非实测路口',self.regular[14],MUTED)
            self._pill(draw,right-18,top+25,'observe','含推测')
            self._rrect(draw,(16,390,496,716),10,fill=CARD,outline=BORDER)
            self._text(draw,34,417,'行人行为 · 暂停前历史' if self.freeze_mode else '行人行为 · 实时分析',self.bold[20])
            rows=(view or {}).get('behavior_rows',())
            if not rows:self._text(draw,34,460,'等待行人检测，保留道路示意',self.regular[16],MUTED)
            for index,(identifier,status,direction,estimated,crossing) in enumerate(rows):
                y=456+index*48
                self._text(draw,34,y,'P%d  %s'%(identifier,status),self.bold[18], '#fbbf24' if crossing else TEXT)
                detail=(direction+' · ' if direction else '')+('位置为示意推测' if estimated else '横道区域关联')
                self._text(draw,34,y+21,detail,self.regular[14],MUTED)
            draw.line((34,591,478,591),fill=BORDER)
            self._text(draw,34,613,(view or {}).get('pie_scene_text','PIE：等待预测接口'),self.regular[14],MUTED,limit=444)
            self._text(draw,34,641,'实线：近一秒推测 · 虚线：更远的运动假设' if self.freeze_mode else '上方行为来自连续运动，PIE 为提前预测分数。',self.regular[13],DIM)
            self._text(draw,34,665,'暂停画面不增加行为历史；未知方向保留静止。' if self.freeze_mode else 'PIE 板端编号独立，分数未经概率校准。',self.regular[13],DIM)
            self._text(draw,34,698,'N 分析 · G 播放 · B 暂停 · R 重播' if self.freeze_mode else 'J / I / L 选择 · G 开始 · B 暂停 · R 重置',self.regular[13],MUTED)
            return np.array(pil)[:,:,::-1].copy()
        if type(self.layout) is not LiveLayout: return super().render_panel(model,view)
        image=FrontP1Dashboard.render_panel(self,model,view)
        pil=Image.fromarray(image[:,:,::-1]); draw=ImageDraw.Draw(pil)
        self._text(draw,20,82,'HDMI 实时识别 · 前方视角',self.bold[16],MUTED)
        self._rrect(draw,(16,390,496,716),10,fill=CARD,outline=BORDER)
        self._text(draw,34,419,'真实视频 → 实时道路',self.bold[22])
        lines=[('车道曲线','橙色实线 / 青色虚线 / 灰色待确认'),
               ('车辆与行人','持续编号；短暂漏检淡化，过期清除'),
               ('视频与地面','全屏播放；H 四点标定，U 选择区域')]
        for i,(title,detail) in enumerate(lines):
            self._text(draw,34,461+i*66,title,self.bold[18])
            self._text(draw,34,489+i*66,detail,self.regular[15],MUTED)
        self._text(draw,34,671,'换视频或窗口大小后，请检查映射。',self.regular[15],MUTED)
        self._text(draw,34,698,'M 保留路口演示，V 返回大视频。',self.regular[14],DIM)
        x0,y0,x1,y1=self.layout.SCENE
        self._rrect(draw,(x0,y0,x1,y1),10,fill=CARD,outline=BORDER)
        self._text(draw,x0+20,y0+25,'前方道路 · 实时 2.5D',self.bold[22])
        self._pill(draw,x1-18,y0+25,'observe','地面已标定' if self.projection.calibrated else '默认示意')
        self._text(draw,x0+20,y0+53,'平面投影 / 相对远近 / 不显示精确距离',self.regular[14],MUTED)
        self._text(draw,x0+20,y1-49,'淡色 = 保持    P = 行人    V = 车辆    B = 骑行',self.regular[15],MUTED)
        self._text(draw,x0+20,y1-22,'坡道、转弯或遮挡处，位置可能存在偏差。',self.regular[14],DIM)
        return np.array(pil)[:,:,::-1].copy()

    def _draw_anticipation(self,canvas):
        view=self.anticipation
        if view is None: return
        image=Image.new('RGB',(480,326),CARD); draw=ImageDraw.Draw(image)
        self._rrect(draw,(0,0,479,325),10,fill=CARD,outline=BORDER)
        self._text(draw,18,29,'行人预测 · RK3568 CPU',self.bold[18])
        self._pill(draw,462,29,'observe','实验分数')
        titles={'INVALID':'预测接口不匹配','DISCONNECTED':'连接或检测尚未就绪','WAITING':'等待有效感知接口',
                'STALE':'预测结果过期，分数已清除','VIDEO_GAP':'画面时间不匹配，分数已清除',
                'TIME_RESET':'时间变化，等待新预测',
                'NO_PERSON':'当前没有可用行人轨迹','WAITING_HISTORY':'正在收集行人历史',
                'SCORED':'最新板端结果','CADENCE':'采样间隔不匹配','AMBIGUOUS':'目标关联不确定'}
        title=titles[view['status']]
        if view['age_ms']>=0: title+=' · %d ms'%round(view['age_ms'])
        self._text(draw,18,61,title,self.regular[15],MUTED,limit=440)
        draw.line((18,82,462,82),fill=BORDER)
        states={'WAITING_HISTORY':'等待六次有效采样','HISTORY_SHORT':'历史不足','CADENCE':'采样间隔不匹配','AMBIGUOUS':'目标关联不确定'}
        for index,person in enumerate(view['persons'][:3]):
            top=106+index*53
            self._text(draw,18,top,'板端 P%d · 历史 %d/6'%(person['id'],person['samples']),self.bold[16],TEXT,limit=440)
            detail=('0.6 秒  %.3f    /    1 秒  %.3f'%(person['score06'],person['score1'])
                    if person['status']=='SCORED' else states[person['status']])
            self._text(draw,18,top+24,detail,self.regular[15],MUTED,limit=440)
        self._text(draw,18,260,'P 编号为板端轨迹，与右侧编号独立。',self.regular[13],DIM)
        self._text(draw,18,286,'动作阶段未确认 · 仅显示实验分数',self.regular[14],MUTED)
        self._text(draw,18,308,'分数未经概率校准，不提供驾驶许可。',self.regular[13],DIM)
        self.canvas_patch(canvas,np.array(image)[:,:,::-1],16,390)

    def _draw_crosswalk_status(self,canvas,left,top):
        crosswalk=self.world_view['crosswalk'];status=crosswalk['status']
        titles={'WAITING':'等待斑马线感知接口','INVALID':'斑马线接口不匹配','STALE':'斑马线结果过期',
                'SOURCE_MISMATCH':'斑马线来源不匹配','VIDEO_GAP':'斑马线画面时间不匹配',
                'TIME_RESET':'时间变化，重新确认斑马线','DISCONNECTED':'等待检测与连接'}
        if status=='LIVE':
            confirmed=[region for region in crosswalk['regions'] if region['confirmed']]
            if type(self.layout) is SceneLayout:
                occupants={}
                for person in self.renderer.perception_objects(self.world_view):
                    for identifier in person.get('crosswalk_ids',()):
                        occupants.setdefault(identifier,[]).append(person['id'])
                confirmed=[dict(region,persons=occupants.get(region['id'],[])) for region in confirmed]
            candidates=len(crosswalk['regions'])-len(confirmed)
            title='斑马线区域：确认 %d'%len(confirmed)
            if candidates:title+=' · 候选 %d'%candidates
            if not confirmed and candidates:title+=' · 等待后续观测'
            elif crosswalk['skew_ms'] is None or crosswalk['skew_ms']>350:title+=' · 行人关联未知'
            elif any(region['persons'] for region in confirmed):
                occupied=[region for region in confirmed if region['persons']]
                title+=' · '+ ' / '.join('Z%d:P%s%s'%(region['id'],','.join(map(str,region['persons'][:3])),
                                                       '…' if len(region['persons'])>3 else '')
                                         for region in occupied[:2])
                if len(occupied)>2:title+='…'
            else:title+=' · 暂无确认关联'
        else:title=titles.get(status,'等待斑马线感知')
        self.canvas_patch(canvas,self._patch(title,14,MUTED,CARD),left,top)
        if crosswalk['omitted']:
            self.canvas_patch(canvas,self._patch('部分斑马线无法定位',14,MUTED,CARD),left,top+28)

    def _lane_count_title(self):
        count=self.world_view['lane_bands']['visible_lane_count']
        if type(self.layout) is SceneLayout:
            count=self.renderer.lane_count(self.world_view)
            if count is not None:return '前方可见车道 %d'%count
            return '前方车道数待确认 · 边界 %d'%len(self.world_view['lanes'])
        if count is not None:return '可见车道 %d · 仅可见路段'%count
        return '边界 %d，车道数待确认'%len(self.world_view['lanes'])

    def compose(self,video,video_fresh,snapshot=None,now=None,stats=None,video_ms=None):
        now=time.monotonic() if now is None else now
        snapshot=self.client.snapshot() if snapshot is None else snapshot
        self.latest_video=video
        self.world_view=self.tracker.update(snapshot,video_fresh,now,video_ms)
        if 'pedestrian' in (snapshot.get('state') or {}):self.world_view['pedestrian_policy']='crosswalk_contact'
        self.world_view['crosswalk']=self.crosswalk_map.update(snapshot,video_fresh,now,self.tracker,self.world_view,video_ms)
        display_view=self.display_tracker.update(snapshot,video_fresh,now,video_ms)
        source_image=video[:self.layout.SOURCE_H,self.layout.SOURCE_X:self.layout.SOURCE_X+self.layout.SOURCE_W] if video is not None else None
        self.world_view['presentation']=self.presentation.update(snapshot,self.world_view,video_fresh,now,video_ms,source_image)
        self.anticipation=anticipation_view(snapshot,video_fresh,now,video_ms)
        if self.anticipation is not None and self.world_view['status']!='LIVE':
            self.anticipation.update(status=self.world_view['status'],persons=[],age_ms=-1)
        state=snapshot.get('state') or {}
        world=validate_world(state.get('display_world') if state.get('display_only') is True else None)
        if world is None:world=validate_world(state.get('world'))
        if world is not None:
            world=dict(world)
            world['_session']=state.get('session')
            transport=world.get('_transport_ms',0)
            transport=transport if type(transport) in (int,float) and math.isfinite(transport) and transport>=0 else 5000
            world['report_age_ms']+=max(0.,now-snapshot.get('received_at',0))*1000+transport
        self.freezer.record(source_image,video_ms,world,self.presentation.tracks)
        if self.freeze_mode:
            elapsed=now-snapshot.get('received_at',0)
            connected=video_fresh and snapshot.get('connected') is True and 0<=elapsed<=.5 and state.get('state')=='RUNNING'
            p1=(state.get('drive') or {}).get('p1') or {}
            signal=p1.get('signal',0) if 0<=p1.get('signal_age_ms',-1)<=2500 else 0
            self.freezer.collect(now,video_ms,world,state.get('crosswalk'),self.presentation,video,connected,self.anticipation,signal)
            self.freezer.tick(now)
            self.world_view['presentation']=self.freezer.view(self.world_view['presentation'])
            if self.freezer.snapshot is not None:self.anticipation=self.freezer.snapshot['anticipation']
        canvas=super().compose(video,video_fresh,snapshot,now,stats)
        if type(self.layout) is SceneLayout:
            if self.freeze_mode:
                cv2.rectangle(canvas,(16,59),(497,98),(16,12,10),-1)
                x,y,right,bottom=self.layout.video_rect()
                if self.freezer.frame is not None:
                    source=self.freezer.frame[:self.layout.SOURCE_H,self.layout.SOURCE_X:self.layout.SOURCE_X+self.layout.SOURCE_W]
                    canvas[y:bottom,x:right]=cv2.resize(source,(self.layout.VIDEO_W,self.layout.VIDEO_H))
                label='HDMI 冻结快照 · N 重新分析' if self.freezer.snapshot else '先暂停 Windows 视频，再按 N 分析'
                self.canvas_patch(canvas,self._patch(label,15,MUTED,BG),20,69)
            x0,y0,_,_=self.layout.SCENE
            completed=self.world_view['presentation']
            title='%d 车道布局 · %s'%(completed['lane_count'],'示意' if completed['lane_inferred'] else '参考观测')
            self.canvas_patch(canvas,self._patch(title,15,MUTED,CARD),x0+20,y0+78)
            detail='完整横道 · '+('示意补全' if completed['crosswalk_inferred'] else '观测与示意融合')
            self.canvas_patch(canvas,self._patch(detail,14,MUTED,CARD),x0+20,y0+108)
            return canvas
        if type(self.layout) is not LiveLayout: return canvas
        self._draw_anticipation(canvas)
        x0,y0,x1,y1=self.layout.SCENE
        road_view=self.world_view
        if not road_view['objects'] and display_view['objects'] and display_view['status']=='LIVE':
            road_view=dict(road_view,objects=[dict(obj,held=True,alpha=min(.45,obj['alpha'])) for obj in display_view['objects']],
                           ages=(road_view['ages'][0],display_view['ages'][1]))
        road=self.road_renderer.frame(road_view)
        canvas[y0+68:y1-75,x0+1:x1-1]=road[68:-75,1:-1]
        kind,title=STATUS[self.world_view['status']]
        patch=self._patch(self._lane_count_title() if self.world_view['status']=='LIVE' else title,16,KINDS[kind][1],CARD)
        self.canvas_patch(canvas,patch,x0+20,y0+78)
        self._draw_crosswalk_status(canvas,x0+20,y0+110)
        la,oa=road_view['ages']; skew=self.world_view['skew']
        age=lambda x: '--' if x<0 else str(round(x/50)*50)
        cv2.putText(canvas,'LANE %s ms   OBJ %s ms   GAP %s ms'%(age(la),age(oa),age(skew if skew is not None else -1)),
                    (x0+20,y1-81),cv2.FONT_HERSHEY_SIMPLEX,.45,(150,137,114),1,cv2.LINE_AA)
        if skew is not None and skew>350:
            self.canvas_patch(canvas,self._patch('两类结果存在时间差',14,'#fbbf24',CARD),x0+350,y0+78)
        if self.world_view['omitted']:
            self.canvas_patch(canvas,self._patch('部分目标落点不可定位',14,MUTED,CARD),x0+20,y0+166)
        if self.calibration and self.frozen_video is not None:
            vx,vy,ex,ey=self.layout.video_rect(); L=self.layout
            source=self.frozen_video[:L.SOURCE_H,L.SOURCE_X:L.SOURCE_X+L.SOURCE_W]
            canvas[vy:ey,vx:ex]=cv2.resize(source,(L.VIDEO_W,L.VIDEO_H))
            self._paste(canvas,self._patch('标定暂停画面 · 再按 H / U 取消',14,'#fde68a','#41351c'),vx+5,vy+5)
            if self.calibration=='calibrate':
                for i,p in enumerate(self.calibration_points):
                    xy=L.from_norm(*(p*10000)); cv2.circle(canvas,xy,5,(0,220,255),-1)
                    cv2.putText(canvas,str(i+1),xy,cv2.FONT_HERSHEY_SIMPLEX,.6,(0,220,255),2)
            elif self.drag_start is not None: cv2.rectangle(canvas,self.drag_start,self.drag_end,(0,220,255),2)
        return canvas

    def resolve_scene(self,model,snapshot,video_fresh,now):
        if self.freeze_mode:return self.freezer.scene()
        if self.local_scene_override is False:return None
        return super().resolve_scene(model,snapshot,video_fresh,now)
