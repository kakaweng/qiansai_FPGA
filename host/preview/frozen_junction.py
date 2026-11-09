"""Detached, causal snapshot rehearsal. No board commands or model scores here.

HDMI still arrives when its upstream player is paused. Source timestamps alone
therefore cannot identify distinct observations of pedestrian motion.
"""
from collections import deque
import copy
import cv2
import numpy as np
from junction_scene import route_path, route_pose
from live_crosswalk import decode_crosswalk
from junction_association import JunctionAssociator


class SourcePicture:
    """Compare scene pixels while accounting for the board's known drawing.

    A paused HDMI source still gets new YOLO boxes and lane overlays. Reject
    thin opaque palette strokes, and compare possible underlying RGB values
    under the *actual* 18% corridor / 45% class blends. This is deliberately
    limited to those paint operations, not arbitrary brightness fitting or
    unrestricted optical-flow alignment that could hide a moving source.
    """
    COLORS = np.array([(255,0,255),(0,255,0),(0,255,255),(132,130,132),
                       (0,170,255),(255,211,0),(181,182,181),(255,255,255)],np.int16)
    STATES = [(1.,np.zeros(3))]
    for corridor in (False, True):
        gain = .82 if corridor else 1.
        bias = np.array([80.,220.,80.])*.18 if corridor else np.zeros(3)
        if corridor: STATES.append((gain,bias))
        for color in ((0,170,255),(255,210,0),(210,90,210)):
            STATES.append((gain*.55,bias*.55+np.array(color)*.45))
    UNCERTAINTY = [np.ones(3,np.float32)*2 if gain==1. else
                   np.array([8.,4.,8.],np.float32)/gain+2 for gain,_ in STATES]

    def __init__(self, image):
        if image.ndim == 2: image=cv2.cvtColor(image,cv2.COLOR_GRAY2BGR)
        # Nearest sampling preserves exact RGB565 palette colours. Blurring or
        # area resize would mix opaque box strokes into the scene beforehand.
        pixels=cv2.resize(image,(192,108),interpolation=cv2.INTER_NEAREST)[10:94]
        channels=pixels.astype(np.int16)
        palette=np.zeros(pixels.shape[:2],bool)
        for blue,green,red in self.COLORS:
            palette|=((np.abs(channels[...,0]-blue)<=2)&(np.abs(channels[...,1]-green)<=2)
                      &(np.abs(channels[...,2]-red)<=2))
        palette=palette.astype(np.uint8)
        # Keep broad natural regions even when they share a palette colour.
        strokes=palette-cv2.erode(palette,np.ones((3,3),np.uint8))
        self.valid=~cv2.dilate(strokes,np.ones((3,3),np.uint8)).astype(bool)
        self.values=[];self.possible=[]
        for gain,bias in self.STATES:
            base=((pixels.astype(np.float32)-bias.astype(np.float32))/gain)
            self.values.append(base)
            possible=np.all((base>=-22)&(base<=277),axis=-1)
            # Lane segmentation/corridor paint starts at the model's 35% crop.
            if gain!=1.: possible[:28]=False
            self.possible.append(possible)

    def difference(self, other):
        visible=self.valid&other.valid
        # Most pixels are already identical, or explained by the first blend.
        # Remove explained pixels immediately instead of doing 64 full-image
        # comparisons on every preview refresh.
        remaining=visible.ravel().copy()
        current=[value.reshape(-1,3) for value in self.values]
        prior=[value.reshape(-1,3) for value in other.values]
        for index in range(len(current)):
            positions=np.flatnonzero(remaining)
            if not len(positions): break
            for previous in range(len(prior)):
                tolerance=self.UNCERTAINTY[index]+self.UNCERTAINTY[previous]+5
                error=np.abs(current[index][positions]-prior[previous][positions])
                possible=self.possible[index].ravel()[positions]&other.possible[previous].ravel()[positions]
                explained=possible&np.all(error<=tolerance,axis=-1)
                remaining[positions[explained]]=False
                positions=positions[~explained]
                if not len(positions): break
        changed=remaining.reshape(visible.shape).astype(np.uint8)
        # Aliased thin strokes at the sampling boundary are not source motion.
        changed=cv2.medianBlur(changed,3).astype(bool)&visible
        count=int(np.count_nonzero(visible))
        fraction=float(np.count_nonzero(changed))/max(1,count)
        # Local checks retain sensitivity to people moving in an otherwise
        # stationary scene. Offset grids avoid a person straddling tile edges.
        peak=0.
        for y in (0,6):
            for x in (0,6):
                height=(changed.shape[0]-y)//12*12;width=(changed.shape[1]-x)//12*12
                blocks=changed[y:y+height,x:x+width].reshape(height//12,12,width//12,12)
                support=visible[y:y+height,x:x+width].reshape(height//12,12,width//12,12)
                amounts=blocks.sum(axis=(1,3));areas=support.sum(axis=(1,3))
                eligible=(areas>=72)&(amounts>=18)
                peak=max(peak,float(np.max(np.where(eligible,amounts/np.maximum(1,areas),0))))
        return dict(changed=count<visible.size*.25 or fraction>.008 or peak>.15,
                    changed_fraction=fraction,local_fraction=peak,visible_fraction=count/visible.size)


class FrozenJunction:
    HORIZON = 3.
    CAPTURE_TIMEOUT = 12.
    RATE = 60000. / HORIZON  # Schematic route progress, not measured speed.

    def __init__(self):
        self.archive = deque(maxlen=100)
        self.source = None
        self.reference = None
        self.stable_ms = None
        self.last_video_ms = None
        self.last_picture_ms = None
        self.last_key = None
        self.pause_motion = None
        self.picture_change = None
        self.direction = 3
        self.clear()

    def clear(self):
        self.status = 'IDLE'
        self.detail = '先在 Windows 暂停视频，再按 N 冻结并分析'
        self.snapshot = None
        self.frame = None
        self.started = None
        self.request_ms = None
        self.capture_source = None
        self.elapsed = 0.
        self.distance = 0.
        self.playing = False
        self.last_tick = None
        self.gate = dict(status='UNKNOWN', detail='等待冻结场景', conflict_ids=[], crossing=None)

    @staticmethod
    def signature(image):
        return SourcePicture(image)

    def record(self, image, video_ms, world, tracks):
        if image is None or type(video_ms) is not int or world is None: return
        source = (world.get('_session'), world['producer'], image.shape[:2])
        reset = (source != self.source or
                 (self.last_video_ms is not None and video_ms < self.last_video_ms))
        if reset:
            self.archive.clear(); self.reference = None; self.last_key = None
            self.stable_ms = None; self.pause_motion = None
            self.last_picture_ms = None
        self.source = source
        self.last_video_ms = video_ms
        # UI repaints can reuse one received frame. Sample at most 12.5 Hz;
        # motion archives still accept every independent object observation.
        if self.last_picture_ms is None or video_ms-self.last_picture_ms >= 80:
            self.last_picture_ms = video_ms
            signature = self.signature(image)
            self.picture_change = None if self.reference is None else signature.difference(self.reference)
            # Keep a fixed anchor: slow motion must accumulate against the last
            # changed picture rather than disappear in consecutive-frame noise.
            if self.picture_change is None or self.picture_change['changed']:
                self.reference = signature
                self.stable_ms = video_ms
                self.pause_motion = None
        key = (world['object_seq'], world['object_ms'])
        if key != self.last_key and key[0] and key[1] <= video_ms:
            self.last_key = key
            self.archive.append(dict(at=world['object_ms'], tracks=copy.deepcopy(tracks)))
        if self.pause_motion is None and self.stable_ms is not None and video_ms-self.stable_ms >= 450:
            self.pause_motion = copy.deepcopy([row for row in self.archive
                                              if self.stable_ms-1700 <= row['at'] <= self.stable_ms+100])
        while self.archive and video_ms-self.archive[0]['at'] > 6500: self.archive.popleft()

    def request(self, now, video_ms):
        self.clear()
        self.status = 'ANALYZING'
        self.detail = '等待暂停画面稳定，以及同一画面的行人与横道结果'
        self.started = now
        self.request_ms = video_ms
        self.capture_source = self.source

    def _fail(self, reason):
        self.status = 'ERROR'; self.detail = reason

    def collect(self, now, video_ms, world, crosswalk, presentation, board_frame, connected, anticipation=None,signal=0):
        if self.status != 'ANALYZING': return
        if now-self.started > self.CAPTURE_TIMEOUT:
            self._fail('分析超时：请确认源视频已暂停且检测连接正常，按 N 重试'); return
        if not connected or world is None or type(video_ms) is not int:
            self.detail = '等待视频与检测连接恢复，再收齐当前画面'; return
        if self.capture_source is not None and self.source != self.capture_source:
            self._fail('视频来源已变化，请按 N 重新分析'); return
        if self.request_ms is not None and video_ms < self.request_ms:
            self._fail('视频时间已重置，请按 N 重新分析'); return
        if self.stable_ms is None or video_ms-self.stable_ms < 450:
            self.detail = '画面仍在变化，请先在 Windows 暂停视频'; return
        # Allow inference captured after the stable image began, including a
        # result that completed just before the user pressed N. No cross-source
        # masks, old moving frames, held geometry or expired packets are used.
        object_ok = (world['live'] and world['object_valid'] and world['object_seq']>0 and 0 <= world['report_age_ms'] <= 750
                     and 0 <= video_ms-world['object_ms'] <= 2500
                     and world['object_ms'] >= self.stable_ms)
        mask_ok = (isinstance(crosswalk, dict) and crosswalk.get('valid') is True
                   and crosswalk.get('held') is False and crosswalk.get('producer') == world['producer']
                   and type(crosswalk.get('age_ms')) in (int,float) and 0 <= crosswalk['age_ms'] <= 2500
                   and type(crosswalk.get('source_ms')) is int
                   and type(crosswalk.get('sent_ms')) is int
                   and type(crosswalk.get('source_seq')) is int and crosswalk['source_seq']>0
                   and self.stable_ms <= crosswalk['source_ms'] <= video_ms
                   and 0 <= video_ms-crosswalk['source_ms'] <= 2500
                   and abs(crosswalk['source_ms']-world['object_ms']) <= 350
                   and crosswalk['source_ms'] <= crosswalk.get('sent_ms',0) <= video_ms
                   and decode_crosswalk(crosswalk) is not None)
        if not object_ok or not mask_ok:
            self.detail = '正在收齐暂停画面的行人检测与斑马线分割'; return
        self._capture(now, video_ms, presentation, board_frame, anticipation,crosswalk,signal,world)

    def _capture(self, now, video_ms, presentation, board_frame, anticipation,crosswalk,signal,world):
        frozen = copy.deepcopy(presentation.last_view)
        geometry=JunctionAssociator.snapshot_geometry(crosswalk)
        frozen['crossing_geometry']=dict(geometry.view(),status='SNAPSHOT')
        frozen['crosswalk_inferred']=not any(band['role'] for band in geometry.bands)
        tracks = {track['id']:track for track in presentation.tracks}
        anchor = self.stable_ms
        # Retain motion measured immediately before the picture became still.
        history = self.pause_motion if self.pause_motion is not None else [
            row for row in self.archive if anchor-1700 <= row['at'] <= anchor+100]
        actors = []
        for actor in frozen.get('actors', []):
            if actor.get('inferred'): continue
            track = tracks.get(actor['id'])
            if track is None or track['object_ms'] < anchor: continue
            actor['ground'] = tuple(map(float, track['target']))
            relation=geometry.associate(track['box'],track['object_ms']) if actor['cls'] in (0,1,3) and 'box' in track else None
            if relation and relation['point'] is not None:
                actor.update(ground=tuple(map(float,relation['point'])),crossing=relation['crossing'],
                             association=relation['association'],region_id=relation['region_id'])
                band=next(band for band in geometry.bands if band['id']==relation['region_id'])
                actor['position_estimated']=bool(band.get('role_estimated')) or relation['association']!='FOOT_SUPPORT'
            actor['held'] = False; actor['predicted'] = False; actor['alpha'] = 1.
            actor['contact'] = False  # Snapshot evidence is never live contact.
            velocity = np.zeros(2)
            samples = [t for row in history for t in row['tracks']
                       if t['id'] == actor['id'] and not t['inferred']]
            moving = [t for t in samples if t.get('behavior') in ('CROSSING', 'MOVING')
                      and t.get('behavior_basis') == 'background_compensated_motion']
            if moving:
                prior = moving[-1]
                if actor.get('crossing') is None and prior.get('behavior')=='CROSSING' and prior.get('crossing') in ('near','far'):
                    actor.update(crossing=prior['crossing'],position_estimated=True,association='PAST_MOTION_ESTIMATE')
                    center=sum(track['box'][[0,2]])/2 if 'box' in track else .5
                    actor['ground']=(float(np.clip((center-.5)*22.,-11.5,11.5)),9.6 if prior['crossing']=='near' else -9.6)
                motion = prior.get('motion_history', [])
                if len(motion) >= 3 and motion[-1]['at']-motion[0]['at'] >= .45:
                    residual = (motion[-1]['total']-motion[0]['total'])/(motion[-1]['at']-motion[0]['at'])
                    role = actor.get('crossing')
                    if role in ('near', 'far'):
                        velocity[0] = np.clip(residual[0]*28., -4., 4.)
                    elif role in ('left', 'right'):
                        sign = 1 if role == 'right' else -1
                        velocity[1] = np.clip(-sign*residual[0]*35.+residual[1]*8., -4., 4.)
                    else:
                        velocity = np.clip(residual*[23.,32.], -4., 4.)
                    actor['behavior'] = prior['behavior']
                    actor['motion_direction'] = prior.get('motion_direction', 0)
            actor['simulation_velocity'] = velocity.tolist()
            actor['trajectory_basis'] = 'past_compensated_motion' if np.linalg.norm(velocity) > .15 else 'position_only'
            actors.append(actor)
        frozen.update(actors=actors, frozen=True, snapshot_ms=video_ms, snapshot_anchor_ms=anchor,
                      anticipation=copy.deepcopy(anticipation),signal=signal if signal in (1,2,3) else 0, display_only=True)
        frozen['capture_evidence']=dict(producer=world['producer'],object_seq=world['object_seq'],
            object_ms=world['object_ms'],crosswalk_seq=crosswalk['source_seq'],crosswalk_ms=crosswalk['source_ms'],
            skew_ms=abs(crosswalk['source_ms']-world['object_ms']))
        if not world['lane_valid'] or world['lane_ms']<anchor:frozen['lane_inferred']=True
        self.snapshot = frozen; self.frame = board_frame.copy()
        self.status = 'READY'; self.detail = '场景已固定：选择方向，按 G 播放三秒推演'
        self.last_tick = now
        self.gate = self._conflicts()

    def action(self, action):
        if action in ('turn_left', 'straight', 'turn_right'):
            self.direction = ('turn_left', 'straight', 'turn_right').index(action)+1
            self.elapsed = 0.; self.distance = 0.; self.playing = False
            if self.snapshot is not None: self.status = 'READY'; self.gate = self._conflicts()
        elif self.snapshot is not None:
            if action == 'scene_start' and self.elapsed < self.HORIZON:
                self.playing = True; self.status = 'PLAYING'
            elif action == 'scene_pause': self.playing = False; self.status = 'PAUSED'
            elif action == 'scene_reset':
                self.playing = False; self.elapsed = 0.; self.distance = 0.; self.status = 'READY'
                self.gate = self._conflicts()

    def tick(self, now):
        delta = 0. if self.last_tick is None else min(.1, max(0., now-self.last_tick))
        self.last_tick = now
        if not self.playing: return
        self.gate = self._conflicts()
        self.elapsed = min(self.HORIZON, self.elapsed+delta)
        destination = min(60000., self.distance+delta*self.RATE)
        if self.gate['status'] != 'CLEAR':
            destination = min(destination, max(self.distance, self.gate.get('stop_distance', self.distance)))
        self.distance = destination
        self.gate = self._conflicts()
        if self.elapsed >= self.HORIZON-1e-8:
            self.elapsed = self.HORIZON; self.playing = False; self.status = 'COMPLETE'
            if self.distance < 59999. and self.gate['status']=='CLEAR':
                self.gate.update(status='UNKNOWN',detail='三秒推演结束，尚未完成通行；可重播比较')

    @staticmethod
    def actor_position(actor, at):
        position = np.asarray(actor['ground'])+np.asarray(actor['simulation_velocity'])*at
        role = actor.get('crossing')
        if role in ('near','far'): position[0] = np.clip(position[0], -13., 13.)
        elif role in ('left','right'): position[1] = np.clip(position[1], -13., 13.)
        return position

    def _conflicts(self):
        if self.snapshot is None:
            return dict(status='UNKNOWN', detail='等待冻结场景', conflict_ids=[], crossing=None)
        path = route_path(self.direction)
        conflicts = []; first_distance = 60000.; crossing = None; points = []
        for at in np.arange(self.elapsed, self.HORIZON+.001, .05):
            distance = min(60000., self.distance+(at-self.elapsed)*self.RATE)
            center, tangent = route_pose(path, distance)
            side = np.array([-tangent[1], tangent[0]])
            for actor in self.snapshot['actors']:
                if actor['cls'] not in (0,1,3): continue
                position = self.actor_position(actor, at)
                offset = position-center
                margin = .7 if actor.get('position_estimated') else .3
                if abs(float(offset@tangent)) <= 3.+margin and abs(float(offset@side)) <= 1.8+margin:
                    conflicts.append(actor['id'])
                    if distance < first_distance:
                        first_distance = distance; crossing = actor.get('crossing'); points = [position.tolist()]
        status = 'YIELD' if conflicts else 'CLEAR'
        if self.direction!=3 and self.snapshot.get('signal') in (1,2) and self.distance<=14500:
            return dict(status='STOP_SIGNAL',detail='冻结时为红灯，模拟车辆保持等待' if self.snapshot['signal']==1 else '冻结时为黄灯，模拟车辆保持等待',
                        conflict_ids=sorted(set(conflicts)),crossing=crossing,conflict_points=points,
                        stop_distance=min(14500.,max(self.distance,first_distance-2200.)) if conflicts else 14500.,display_only=True)
        return dict(status=status, detail='预测路径可能交汇，模拟车辆先礼让' if conflicts else '三秒示意范围内未见路径交汇',
                    conflict_ids=sorted(set(conflicts)), crossing=crossing,
                    stop_distance=max(self.distance, first_distance-2200.), conflict_points=points, display_only=True)

    def scene(self):
        phase = ('READY' if self.status in ('IDLE','ANALYZING','ERROR','READY') else
                 'PAUSED' if self.status == 'PAUSED' else 'COMPLETE' if self.status == 'COMPLETE' else
                 'WAIT_PEDESTRIAN' if self.gate['status'] == 'YIELD' and self.distance >= self.gate.get('stop_distance', 0)-1 else
                 'CROSSING' if self.distance >= 15000 else 'APPROACH')
        if self.playing and self.gate['status']=='STOP_SIGNAL' and self.distance>=self.gate['stop_distance']-1:
            phase='WAIT_RED' if self.snapshot['signal']==1 else 'WAIT_YELLOW'
        return dict(enabled=True, active=self.playing, phase=phase, draw_distance=self.distance,
                    distance_mm=self.distance, end_mm=60000, stop_mm=15000,
                    fresh=self.snapshot is not None, speed_mm_s=self.RATE if self.playing and phase not in ('WAIT_PEDESTRIAN','WAIT_RED','WAIT_YELLOW') else 0,
                    crossed=self.distance>15000, violation=False)

    def view(self, fallback):
        result = copy.deepcopy(self.snapshot if self.snapshot is not None else fallback)
        if self.snapshot is None: result['actors'] = []
        else:
            for actor in result['actors']:
                actor['ground'] = tuple(map(float, self.actor_position(actor, self.elapsed)))
                actor['route_conflict'] = actor['id'] in self.gate['conflict_ids']
                actor['predicted'] = self.elapsed > 0
                actor['trajectory'] = [self.actor_position(actor, at).tolist()
                                       for at in np.linspace(self.elapsed, self.HORIZON, 13)]
        result.update(route_gate=copy.deepcopy(self.gate), scene=self.scene(), direction=self.direction,
                      frozen=True, capture_status=self.status, simulation_seconds=self.elapsed)
        return result
