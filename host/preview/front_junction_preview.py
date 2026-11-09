"""P1 recognition + intersection scene, preserving the Claude v3 visual language.

The same named scene actions can later be emitted by FPGA keys. This module only
renders board state and sends input; its interpolation never grants entry permission.
"""
import time
import cv2
from front_p1_preview import (FrontP1Client, FrontP1Dashboard, P1Layout, LAYOUT,
                              p1_presentation, p1_view, _bgr, _mix,
                              BG, HEADER, CARD, BORDER, TEXT, MUTED, DIM, KINDS)
from junction_scene import JunctionRenderer, scene_state, SCENE_PHASES

SCENE_ACTIONS = {'scene_open', 'scene_close', 'scene_start', 'scene_pause', 'scene_reset', 'scene_force'}
LABELS = {
    'start': '开始检测', 'stop': '停止检测', 'bind': '点选交通灯', 'unbind': '恢复自动',
    'left': '请求左变道', 'right': '请求右变道', 'cancel': '取消请求',
    'scene_open': '路口演示', 'scene_close': '返回识别', 'scene_start': '开始 / 继续',
    'scene_pause': '暂停车辆', 'scene_reset': '重置场景', 'scene_force': '违规通行',
    'turn_left': '左转', 'straight': '直行', 'turn_right': '右转',
}
KEYS = {32:'start', ord('x'):'stop', ord('t'):'bind', ord('c'):'unbind',
        ord('a'):'left', ord('d'):'right', ord('k'):'cancel',
        ord('j'):'turn_left', ord('i'):'straight', ord('l'):'turn_right',
        ord('g'):'scene_start', ord('b'):'scene_pause', ord('r'):'scene_reset', ord('f'):'scene_force'}
HINTS = {value: ('空格' if key==32 else chr(key).upper()) for key,value in KEYS.items()}
HINTS.update(scene_open='M', scene_close='M')
HINTS['scene_capture']='N'


class RecognitionLayout(P1Layout):
    GROUPS=((16,'识别控制'),(336,'变道请求'),(736,'交通灯'),(1016,'交互场景'))
    BUTTONS={'start':(16,814,158,878), 'stop':(174,814,316,878),
             'left':(336,814,512,844), 'right':(528,814,704,844), 'cancel':(336,854,704,888),
             'bind':(736,814,984,844),'unbind':(736,854,984,888),'scene_open':(1016,814,1168,888)}


class SceneLayout(P1Layout):
    VIDEO_X, VIDEO_Y, VIDEO_W, VIDEO_H = 16,100,480,270
    SCENE=(512,68,1168,716)
    GROUPS=((16,'模式'),(180,'行驶方向'),(552,'场景操作'),(1016,'报警演示'))
    BUTTONS={'scene_close':(16,804,156,844),'stop':(16,854,156,890),
             'turn_left':(180,804,292,844),'straight':(300,804,412,844),'turn_right':(420,804,532,844),
             'bind':(180,854,352,890),'unbind':(360,854,532,890),
             'scene_start':(552,804,760,844),'scene_pause':(776,804,992,844),
             'scene_reset':(552,854,992,890),'scene_force':(1016,804,1168,890)}


class JunctionClient(FrontP1Client):
    def allowed_action(self, action):
        return (isinstance(action,str) and action in SCENE_ACTIONS) or super().allowed_action(action)


def junction_model(snapshot, video_fresh, now):
    model=p1_presentation(snapshot,video_fresh,now)
    state=snapshot.get('state') or {}
    raw=(state.get('drive') or {}).get('p1') or {}
    elapsed=now-snapshot.get('received_at',0)
    scene=scene_state(raw.get('scene'), model['live'] and state.get('state')=='RUNNING', elapsed) if model['p1'] else None
    model['scene']=scene
    if scene and scene['enabled']:
        reason = model['p1'].get('reason')
        if raw.get('direction') == 3 and reason in ('WAIT_PEDESTRIAN', 'WAIT_PERCEPTION'):
            scene.update(speed_mm_s=0, draw_distance=scene['distance_mm'], phase=reason)
        # The legacy P1 display stops movement whenever lamp evidence expires.
        # A scene vehicle already inside the intersection instead follows its latched route.
        model['p1'].update(speed_kmh=round(scene['speed_mm_s']*3.6/1000),
                           led=raw.get('direction',0) if scene['crossed'] and scene['speed_mm_s'] else 0,
                           alarm=scene['violation'] and scene['fresh'], armed=scene['active'])
    return model


class JunctionDashboard(FrontP1Dashboard):
    def __init__(self,client,font_path=None,front_only=True):
        super().__init__(client,font_path,front_only)
        self.renderer=JunctionRenderer()
        self.scene=None
        self._set_layout(False)

    def _set_layout(self,enabled):
        desired=SceneLayout if enabled else RecognitionLayout
        if type(self.layout) is not desired:
            self.layout=desired()
            self.buttons=[(box,LABELS[action],action) for action,box in self.layout.BUTTONS.items()]
            self.drag_start=self.drag_end=None
            self.selecting=False
            self.cache_key=None

    def handle_action(self,action):
        if action in SCENE_ACTIONS and not self.scene:
            self.local_notice='请先更新路口场景服务，再进入演示。'
            return
        super().handle_action(action)

    def handle_key(self,key):
        if ord('A')<=key<=ord('Z'): key+=32
        if key==ord('m'):
            self.handle_action('scene_close' if self.scene and self.scene['enabled'] else 'scene_open')
            return
        action=KEYS.get(key)
        if action:
            if action not in self.layout.BUTTONS and action not in ('start','left','right','cancel'):
                self.local_notice='按 M 进入路口演示后使用此操作。'
                return
            self.handle_action(action)

    def _draw_header(self,draw,view):
        draw.rectangle((0,0,1600,56),fill=HEADER)
        draw.line((0,56,1600,56),fill=BORDER)
        title='路口交互 · 智能辅助驾驶' if view['scene_enabled'] else 'HDMI 单路 · 识别与请求'
        width=self._text(draw,24,28,title,self.bold[24])
        self._pill(draw,24+width+22,28,*view['badge'],self.bold[16],'left')
        self._text(draw,640,28,'真实感知  /  场景交互',self.regular[15],MUTED)
        self._text(draw,1584,28,'M 切换模式 · S 截图 · Q 退出',self.regular[15],MUTED,'right')

    def _draw_buttons(self,draw,view):
        for x,title in self.layout.GROUPS:
            self._text(draw,x,791,title,self.bold[15],MUTED)
        states=dict(view['buttons'])
        for (left,top,right,bottom),label,action in self.buttons:
            selected=states.get(action)=='selected'
            primary=action in ('start','scene_start','scene_open')
            danger=action=='scene_force'
            fill='#3b1219' if danger else '#1d4ed8' if selected else '#1e3a6a' if primary else '#1a2538'
            border='#f87171' if danger else '#93c5fd' if selected else '#3b82f6' if primary else '#2f405f'
            self._rrect(draw,(left,top,right,bottom),8,fill=fill,outline=border,width=2 if primary or selected else 1)
            cy=(top+bottom)/2
            hint=HINTS[action]
            if bottom-top>60:
                self._text(draw,(left+right)/2,top+23,label,self.bold[18],TEXT,'center')
                self._text(draw,(left+right)/2,bottom-19,('红灯等待时按 ' if danger else '按 ')+hint,self.regular[13],MUTED,'center')
            else:
                self._text(draw,left+12,cy,label,self.bold[16],TEXT,limit=right-left-48)
                self._text(draw,right-12,cy,hint,self.bold[14],MUTED,'right')

    def _draw_direction(self,draw,view):
        if view['scene_enabled']:
            return super()._draw_direction(draw,view)
        left,top,right,bottom=self._card(draw,'direction','路口交互',('observe','按 M 进入'))
        self._text(draw,left+18,top+61,'在 2.5D 场景中操作车辆',self.bold[22])
        self._text(draw,left+18,top+98,'选择路线，观察停车、通行与报警。',self.regular[16],MUTED)
        self._text(draw,left+18,top+128,'实景视频继续提供识别结果。',self.regular[15],DIM)

    def _draw_speed(self,draw,view):
        if not view['scene_enabled']:
            left,top,_,_=self._card(draw,'speed','当前任务',('ok','实景识别'))
            self._text(draw,left+18,top+57,'识别道路 · 响应变道请求',self.bold[22])
            self._text(draw,left+18,top+83,'A / D 请求变道，K 取消',self.regular[15],MUTED)
            return
        left,top,_,_=self._card(draw,'speed','场景车速',view['speed_tag'])
        width=self._text(draw,left+18,top+65,str(view['speed'] or 0),self.bold[56])
        self._text(draw,left+width+27,top+77,'km/h',self.regular[18],MUTED)
        self._text(draw,left+180,top+57,view['scene_title'],self.bold[18])
        self._text(draw,left+180,top+82,'交互演示中的模拟值',self.regular[14],DIM)

    def render_panel(self,model,view=None):
        image=super().render_panel(model,view)
        if not view['scene_enabled']: return image
        import numpy as np
        from PIL import Image,ImageDraw
        pil=Image.fromarray(image[:,:,::-1]); draw=ImageDraw.Draw(pil)
        light_hint = 'HDMI 实时识别 · 右转无需选灯' if view['direction'] == 3 else 'HDMI 实时识别 · 点击 LIGHT 选灯'
        self._text(draw,20,82,light_hint,self.bold[16],MUTED)
        self._rrect(draw,(16,390,496,716),10,fill=CARD,outline=BORDER)
        self._text(draw,34,417,'本次路口演示',self.bold[20])
        if view['direction'] == 3:
            second = ('02  确认通行关系', '连续确认横道内行人后提示礼让')
            detail = 'G 开始，按实时关联暂停或继续'
        else:
            second = ('02  关联交通灯', '多灯时在上方视频点击对应 LIGHT')
            detail = 'G 开始后，红灯等候，绿灯通行'
        lines=[('01  选择路线','J / I / L 选择左转、直行或右转'),
               second, ('03  开始场景', detail)]
        for index,(title,detail) in enumerate(lines):
            y=460+index*67
            self._text(draw,34,y,title,self.bold[18],TEXT)
            self._text(draw,34,y+27,detail,self.regular[15],MUTED)
        self._text(draw,34,674,'B 暂停车辆 · R 重置场景',self.regular[15],MUTED)
        self._text(draw,34,699,'交互演示，不改变原视频。',self.regular[14],DIM)
        x0,y0,x1,y1=self.layout.SCENE
        self._rrect(draw,(x0,y0,x1,y1),10,fill=CARD,outline=BORDER)
        self._text(draw,x0+20,y0+25,'路口场景 · 2.5D',self.bold[22])
        self._pill(draw,x1-18,y0+25,view['scene_kind'],view['scene_title'])
        self._text(draw,x0+20,y0+53,'识别结果 / 交互演示 / 模拟车辆',self.regular[14],MUTED)
        self._text(draw,x0+20,y1-69,view['scene_title'],self.bold[24],KINDS[view['scene_kind']][1])
        self._text(draw,x0+20,y1-37,view['scene_detail'],self.regular[16],MUTED)
        return np.array(pil)[:,:,::-1].copy()

    def prepare_scene_view(self,model,view,snapshot,video_fresh,now):
        return

    def resolve_scene(self,model,snapshot,video_fresh,now):
        return model['scene']

    def compose(self,video,video_fresh,snapshot=None,now=None,stats=None):
        if video.shape[:2]!=(480,1280): raise ValueError('P1 requires a 1280x480 board frame')
        now=time.monotonic() if now is None else now
        snapshot=self.client.snapshot() if snapshot is None else snapshot
        model=junction_model(snapshot,video_fresh,now)
        self.scene=self.resolve_scene(model,snapshot,video_fresh,now)
        enabled=bool(self.scene and self.scene['enabled'])
        self._set_layout(enabled)
        view=p1_view(model,snapshot,video_fresh,self.selecting,self.local_notice or model.get('notice',''))
        view['scene_enabled']=enabled
        if enabled:
            title,detail,kind=SCENE_PHASES[self.scene['phase']]
            if (view.get('people_spatial') and view['direction']==3 and
                    self.scene['phase'] not in ('READY','PAUSED','DISCONNECTED','COMPLETE')):
                title,detail,kind=view['people_feedback']
            if self.scene['violation']:
                kind='alarm'; detail='本次违规已记录 · 按 R 重置后清除'
            view.update(scene_title=title,scene_detail=detail,scene_kind=kind)
            view['speed_tag']=(kind,title)
            view['banner']=(kind,detail)
            # Live status takes priority over the latched event if a stream disappears.
            if not self.scene['fresh']: view['banner']=('stale','演示已暂停，连接恢复后按 G 继续')
        elif not view['lane_alarm'] and view['running']:
            view['banner']=('observe',model['title'] if view['request'] else '识别已就绪，可请求变道或按 M 进入路口演示')
        self.prepare_scene_view(model,view,snapshot,video_fresh,now)
        key=repr(sorted(view.items()))
        if key!=self.cache_key:
            self.cached,self.cache_key=self.render_panel(model,view),key
        self.last_model,self.last_view=model,view
        L=self.layout; canvas=self.cached.copy()
        x0,y0,x1,y1=L.video_rect()
        source=video[:L.SOURCE_H,L.SOURCE_X:L.SOURCE_X+L.SOURCE_W]
        canvas[y0:y1,x0:x1]=cv2.resize(source,(L.VIDEO_W,L.VIDEO_H),interpolation=cv2.INTER_LINEAR)
        if not view['live']:
            canvas[y0:y1,x0:x1]//=2
            patch=self._patch('连接中断 · 等待更新',18,'#ede9fe','#3b2a63')
            self._paste(canvas,patch,x0+15,y0+15)
        p1=model['p1']; roi=p1.get('roi')
        if p1.get('bound') and isinstance(roi,list) and len(roi)==4 and all(type(v) is int and 0<=v<=10000 for v in roi):
            first,last=L.from_norm(*roi[:2]),L.from_norm(*roi[2:])
            cv2.rectangle(canvas,first,last,(255,180,0),2)
            self._paste(canvas,self._patch('已选灯',14,BG,'#00b4ff'),first[0],first[1]-30)
        if self.selecting:
            cv2.rectangle(canvas,(x0+1,y0+1),(x1-2,y1-2),(255,230,0),2)
            self._paste(canvas,self._patch('点击对应 LIGHT，也可拖框',16,BG,'#00e6ff'),x0+8,y0+8)
        if self.drag_start is not None: cv2.rectangle(canvas,self.drag_start,self.drag_end,(0,255,255),2)
        if enabled:
            sx,sy,ex,ey=L.SCENE
            frame=self.renderer.frame(view['direction'],self.scene,view['signal'],view['signal_held'],
                                      perception=getattr(self,'world_view',None))
            canvas[sy+68:ey-92,sx+1:ex-1]=frame[68:-92,1:-1]
            fraction=self.scene['draw_distance']/self.scene['end_mm']
            cv2.line(canvas,(sx+20,ey-15),(ex-20,ey-15),(77,50,36),3,cv2.LINE_AA)
            cv2.line(canvas,(sx+20,ey-15),(sx+20+round((ex-sx-40)*fraction),ey-15),
                     _bgr(KINDS[view['scene_kind']][1]),3,cv2.LINE_AA)
            if self.scene['violation']:
                cv2.rectangle(canvas,(sx+1,sy+1),(ex-2,ey-2),_bgr(KINDS['alarm'][1]),3)
        elif view['lane_alarm']:
            cv2.rectangle(canvas,(x0,y0),(x1-1,y1-1),_bgr(KINDS['alarm'][1]),4)
        if self.flash and 0<=now-self.flash[1]<=self.FLASH_SECONDS and self.flash[0] in L.BUTTONS:
            a,b,c,d=L.BUTTONS[self.flash[0]]; cv2.rectangle(canvas,(a,b),(c-1,d-1),(255,255,255),2)
        if stats is not None:
            cv2.putText(canvas,'%.1f FPS'%stats[0] if video_fresh else 'NO VIDEO',(1002,34),
                        cv2.FONT_HERSHEY_SIMPLEX,.6,(153,211,52) if video_fresh else (250,139,167),1,cv2.LINE_AA)
        return canvas
