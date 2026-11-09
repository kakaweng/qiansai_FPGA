"""Frozen-scene what-if rehearsal in relative display coordinates, never control."""
import math
import numpy as np

DIRECTIONS=('left','straight','right')


def route(direction, horizon=3., step=.05):
    if direction not in DIRECTIONS or not math.isfinite(horizon) or horizon<=0 or not math.isfinite(step) or step<=0:
        raise ValueError('Invalid rehearsal route')
    times=np.linspace(0,horizon,round(horizon/step)+1)
    u=times/horizon
    # User-selected display paths; not inferred road boundaries or metric motion.
    controls={'left':((0,0),(0,3),(-.7,4.6),(-2.5,4.8)),
              'straight':((0,0),(0,2),(0,4),(0,6.5)),
              'right':((0,0),(0,2.5),(.8,3.7),(2.5,4.2))}[direction]
    t=u[:,None];a,b,c,d=np.asarray(controls,float)
    xy=(1-t)**3*a+3*(1-t)**2*t*b+3*(1-t)*t*t*c+t**3*d
    return times,xy


def validate_actor(actor):
    for key in ('position','velocity'):
        value=np.asarray(actor.get(key),float)
        if value.shape!=(2,) or not np.isfinite(value).all():raise ValueError('Invalid actor '+key)
    if abs(actor['position'][0])>3.3 or not -.25<=actor['position'][1]<=7:raise ValueError('Actor outside display ground')
    if np.linalg.norm(actor['velocity'])>3:raise ValueError('Unbounded display motion')


def analyze(direction,actors):
    times,xy=route(direction);events=[]
    for actor in actors:
        validate_actor(actor)
        pos=np.array(actor['position']);velocity=np.array(actor['velocity'])
        future=pos+times[:,None]*velocity
        radius=.13 if actor['class_id']==0 else .28
        # Uncertainty is a schematic envelope, not a learned confidence region.
        uncertainty=.10+times*(.14 if actor['motion_known'] else .35)
        distance=np.linalg.norm(xy-future,axis=1)
        possible=np.flatnonzero(distance<(.20+radius+uncertainty))
        if len(possible):
            k=int(possible[0]);events.append(dict(id=actor['id'],at=float(times[k]),position=xy[k].tolist(),
                                                motion_known=bool(actor['motion_known']),kind='SCHEMATIC_ENVELOPE_OVERLAP'))
    events.sort(key=lambda x:x['at'])
    return dict(direction=direction,times=times.tolist(),points=xy.tolist(),events=events,
                status='SCHEMATIC_OVERLAP' if events else 'NO_OVERLAP_UNDER_ASSUMPTIONS',
                calibrated_geometry=False,driving_permission=False)


def ego_at(result,seconds,yield_at_overlap=True):
    if not math.isfinite(seconds):raise ValueError('Invalid simulation clock')
    time=float(np.clip(seconds,0,3))
    if yield_at_overlap and result['events']:
        time=min(time,max(0,result['events'][0]['at']-.25))
    p=np.asarray(result['points']);ts=np.asarray(result['times'])
    return [float(np.interp(time,ts,p[:,i])) for i in (0,1)]


def motion_from_compensation(box, motion, projection):
    """Use a short background-compensated image displacement as a display assumption."""
    point=np.array([(box[0]+box[2])*.5,box[3]],float)
    p,valid=projection.project([point])
    if not valid[0]:return None
    result=dict(position=p[0].tolist(),velocity=[0.,0.],motion_known=False)
    residual=motion.get('residual_pixels')
    if not motion.get('compensation',{}).get('valid') or residual is None:return result
    d=np.asarray(residual,float)
    if d.shape!=(2,) or not np.isfinite(d).all():return result
    duration=motion.get('window_seconds',0)
    if not math.isfinite(duration) or not .2<=duration<=.65:return result
    other,ok=projection.project([point+d/[639,359]])
    if ok[0]:
        velocity=(other[0]-p[0])/duration
        if np.linalg.norm(velocity)<=3:
            result.update(velocity=velocity.tolist(),motion_known=True)
    return result
