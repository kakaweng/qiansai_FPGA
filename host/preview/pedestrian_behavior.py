"""Causal pedestrian motion evidence for the local rehearsal, not PIE forecasts.

Use real, timestamped image pairs to remove dominant camera motion. Missing
images/cadence remain unverified; no repeated detections or future labels count.
"""
from collections import deque
import math
import cv2
import numpy as np


class SourceMotion:
    def __init__(self):self.reset()

    def reset(self):self.frames=deque();self.last_pair=None;self.cached=None

    def add(self,image,stamp):
        if image is None or type(stamp) is not int or stamp<=0:return
        if self.frames and stamp<self.frames[-1][0]:self.reset()
        if self.frames and stamp-self.frames[-1][0]<70:return
        small=cv2.resize(image,(320,180))
        gray=cv2.cvtColor(small,cv2.COLOR_BGR2GRAY) if small.ndim==3 else small
        self.frames.append((stamp,gray.copy()))
        while self.frames and (stamp-self.frames[0][0]>4500 or len(self.frames)>60):self.frames.popleft()

    def _frame(self,stamp):
        return next((frame for at,frame in reversed(self.frames) if 0<=stamp-at<=220),None)

    def between(self,previous,current,boxes=()):
        key=(previous,current)
        if key==self.last_pair:return self.cached
        self.last_pair=key;self.cached=None
        if previous is None or not 80<=current-previous<=1500:return None
        first,second=self._frame(previous),self._frame(current)
        if first is None or second is None:return None
        mask=np.zeros(first.shape,np.uint8);mask[32:158]=255
        for box in boxes:
            x1,y1,x2,y2=np.rint(np.asarray(box)*[319,179,319,179]).astype(int)
            cv2.rectangle(mask,(int(x1)-3,int(y1)-3),(int(x2)+3,int(y2)+3),0,-1)
        points=cv2.goodFeaturesToTrack(first,140,.015,6,mask=mask)
        if points is None or len(points)<16:return None
        moved,status,_=cv2.calcOpticalFlowPyrLK(first,second,points,None,winSize=(21,21),maxLevel=3)
        if moved is None:return None
        good=status.ravel()>0;a=points[good].reshape(-1,2);b=moved[good].reshape(-1,2)
        finite=np.isfinite(a).all(1)&np.isfinite(b).all(1);a,b=a[finite],b[finite]
        if len(a)<16:return None
        matrix,inliers=cv2.estimateAffinePartial2D(a,b,method=cv2.RANSAC,ransacReprojThreshold=2.)
        if matrix is None or inliers is None:return None
        scale=math.hypot(matrix[0,0],matrix[1,0]);angle=abs(math.atan2(matrix[1,0],matrix[0,0]))
        residual=np.linalg.norm((a@matrix[:,:2].T+matrix[:,2])-b,axis=1)
        if (int(inliers.sum())<14 or inliers.mean()<.65 or not .88<scale<1.12 or angle>.16 or
                np.median(residual[inliers.ravel()>0])>1.5):return None
        self.cached=np.array([[matrix[0,0],matrix[0,1]*179/319,matrix[0,2]/319],
                              [matrix[1,0]*319/179,matrix[1,1],matrix[1,2]/179]])
        return self.cached


def observe_behavior(track,previous,at,matrix):
    """Variable-cadence motion rules; this does not call the learned PIE model."""
    box=track['box'];foot=np.array([(box[0]+box[2])/2,box[3]])
    track.update(behavior='OBSERVING',behavior_basis='insufficient_motion',motion_direction=0,
                 motion_dx=0.,crossing_evidence=False)
    history=[] if previous is None else previous.get('motion_history',[])
    last_crossing=None if previous is None else previous.get('last_crossing')
    if previous is not None and matrix is not None and history and .08<=at-history[-1]['at']<=1.5:
        old=history[-1]
        residual=foot-matrix@np.r_[old['foot'],1.]
        if np.linalg.norm(residual)<.28:
            history=[row for row in history if 0<=at-row['at']<=1.8]
            total=old['total']+residual
            history.append(dict(at=at,foot=foot,total=total,step=residual))
        else:history=[]
    else:history=[]
    if not history:history=[dict(at=at,foot=foot,total=np.zeros(2),step=np.zeros(2))]
    track['motion_history']=history
    if len(history)>=3 and at-history[0]['at']>=.45:
        displacement=history[-1]['total']-history[0]['total']
        steps=np.array([row['step'] for row in history[1:]])
        span=at-history[0]['at'];horizontal=abs(float(displacement[0]))
        consistent=horizontal>=.8*float(np.abs(steps[:,0]).sum())
        near=box[3]-box[1]>=.09 and box[3]>=.62
        crossing=(near and horizontal>=.045 and horizontal/span>=.035 and consistent and
                  horizontal>1.8*abs(float(displacement[1])))
        track.update(behavior_basis='background_compensated_motion',motion_dx=float(displacement[0]),
                     motion_direction=1 if displacement[0]>0 else -1 if displacement[0]<0 else 0)
        if crossing:
            track.update(behavior='CROSSING',crossing_evidence=True);last_crossing=at
        elif np.linalg.norm(displacement)<.015:track['behavior']='STILL'
        else:track['behavior']='MOVING'
    if track['behavior']!='CROSSING' and last_crossing is not None and 0<=at-last_crossing<=.9:
        track.update(behavior='CROSSING_HOLD',crossing_evidence=False,
                     behavior_basis=previous.get('behavior_basis','insufficient_motion') if previous else 'insufficient_motion',
                     motion_direction=previous.get('motion_direction',0) if previous else 0)
    track['last_crossing']=last_crossing
