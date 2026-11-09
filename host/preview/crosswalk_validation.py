"""Offline crosswalk display/evidence prototype; never issues driving commands.

All masks share the resized source-image plane (letterbox already removed).
Held pixels are display-only; support time is never renewed by prediction/polls.
"""
import math
import cv2
import numpy as np


def exclusion_mask(shape, polygons):
    height, width = shape
    allowed = np.ones(shape, np.uint8)
    for polygon in polygons:
        p = np.asarray(polygon, dtype=float)
        if p.ndim != 2 or p.shape[1] != 2 or len(p) < 3 or not np.isfinite(p).all() or np.any((p < 0) | (p > 1)):
            raise ValueError('Exclusion polygons require finite normalized vertices')
        points = np.rint(p * [width - 1, height - 1]).astype(np.int32)
        if cv2.contourArea(points) < 1:
            raise ValueError('Degenerate exclusion polygon')
        cv2.fillPoly(allowed, [points], 0)
    return allowed.astype(bool)


def clean_components(mask, min_pixels=18):
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    keep = np.zeros(count, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_pixels
    return keep[labels]


class CrosswalkTemporal:
    def __init__(self, allowed, hold_seconds=.4, max_gap=.6, min_pixels=18):
        self.allowed = np.asarray(allowed, dtype=bool).copy()
        if self.allowed.ndim != 2 or not self.allowed.any():
            raise ValueError('Empty valid image area')
        self.hold = float(hold_seconds)
        self.max_gap = float(max_gap)
        self.min_pixels = min_pixels
        self.reset()

    def reset(self):
        self.gray = None
        self.timestamp = None
        self.probability = None
        self.active = np.zeros(self.allowed.shape, bool)
        self.votes = np.zeros(self.allowed.shape, np.uint8)
        self.evidence_age = np.full(self.allowed.shape, 1e6, np.float32)
        self.last_result = None

    def _align(self, gray):
        # Backward flow maps each current pixel to its previous-frame location.
        flow = cv2.calcOpticalFlowFarneback(gray, self.gray, None, .5, 3, 15, 3, 5, 1.2, 0)
        yy, xx = np.indices(gray.shape, dtype=np.float32)
        mx, my = xx + flow[..., 0], yy + flow[..., 1]
        def warp(a, nearest=False):
            return cv2.remap(a.astype(np.float32), mx, my,
                             cv2.INTER_NEAREST if nearest else cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        photometric = np.abs(gray.astype(np.float32) - warp(self.gray))
        valid = (mx >= 0) & (my >= 0) & (mx <= gray.shape[1]-1) & (my <= gray.shape[0]-1)
        valid &= (photometric < 35) & (np.linalg.norm(flow, axis=2) < gray.shape[1]*.12)
        valid &= self.allowed & (warp(self.allowed, True) > .5)
        return warp, valid

    def update(self, probability, gray, timestamp, valid=True):
        if not math.isfinite(timestamp):
            self.reset()
            raise ValueError('Non-finite source timestamp')
        p = np.asarray(probability, dtype=np.float32)
        gray = np.asarray(gray)
        if not valid or p.shape != self.allowed.shape or gray.shape != self.allowed.shape or not np.isfinite(p).all() or np.any((p < 0) | (p > 1)):
            self.reset()
            return self.view(timestamp, 'INVALID')
        gray = gray.astype(np.uint8)
        reset_reason = 'NONE'
        if self.timestamp is not None:
            if timestamp == self.timestamp:
                return self.view(timestamp, 'DUPLICATE')
            if timestamp < self.timestamp or timestamp - self.timestamp > self.max_gap:
                self.reset()
                reset_reason = 'TIME_RESET'
            elif np.mean(np.abs(gray.astype(float)-self.gray.astype(float))) > 60:
                self.reset()
                reset_reason = 'SCENE_CUT'
        raw = p > .5
        candidate = clean_components(raw & self.allowed, self.min_pixels)
        previous_active = np.zeros(p.shape, bool)
        previous_votes = np.zeros(p.shape, np.uint8)
        previous_age = np.full(p.shape, 1e6, np.float32)
        filtered = p.copy()
        if self.gray is not None:
            warp, aligned = self._align(gray)
            previous_active = (warp(self.active, True) > .5) & aligned
            previous_votes = np.where(aligned, warp(self.votes, True), 0).astype(np.uint8)
            # Warp bounded ages, never absolute timestamps through float32 maps.
            # PIE clips begin hundreds of seconds into the video; absolute time
            # rounding otherwise suppresses fresh pixels at arbitrary frames.
            previous_age = np.where(aligned, warp(self.evidence_age, True)+(timestamp-self.timestamp), 1e6)
            filtered[aligned] = .7*p[aligned] + .3*warp(self.probability)[aligned]
        votes = np.where(candidate, np.minimum(previous_votes.astype(int)+1, 2), 0).astype(np.uint8)
        fresh = candidate & (filtered > .5) & ((votes >= 2) | previous_active)
        fresh = clean_components(fresh, self.min_pixels)
        # Clear explicit background evidence; only uncertainty may be held.
        # Otherwise a just-passed zebra can linger on a now-clear road surface.
        held = previous_active & ~fresh & (p >= .15) & (previous_age <= self.hold+1e-6)
        held &= self.allowed
        self.evidence_age = np.where(fresh, 0., previous_age).astype(np.float32)
        self.active = fresh | held
        self.votes = votes
        self.gray = gray.copy()
        self.probability = filtered
        self.timestamp = float(timestamp)
        self.last_result = dict(raw=raw, candidate=candidate, fresh=fresh, held=held,
                                display=self.active.copy(), reason=reset_reason)
        return self.view(timestamp)

    def view(self, now, reason=None):
        if self.last_result is None:
            empty = np.zeros(self.allowed.shape, bool)
            return dict(raw=empty, candidate=empty, fresh=empty, held=empty,
                        display=empty, reason=reason or 'WAITING')
        result = {k: v.copy() if isinstance(v, np.ndarray) else v for k, v in self.last_result.items()}
        current = 0 <= now-self.timestamp <= .001
        age = self.evidence_age+(now-self.timestamp)
        alive = (age >= -1e-5) & (age <= self.hold+1e-6)
        result['fresh'] &= current & alive
        result['display'] &= alive
        result['held'] = result['display'] & ~result['fresh']
        if reason:
            result['reason'] = reason
        return result


def pedestrian_relation(box, fresh_mask, allowed, object_age=0., held=False, rider_ambiguous=False):
    """Observed footpoint relation only. Absence never implies a clear/safe road."""
    if held or object_age > .25 or object_age < 0 or rider_ambiguous:
        return 'UNKNOWN_EVIDENCE'
    p = np.asarray(box, dtype=float)
    if p.shape != (4,) or not np.isfinite(p).all() or np.any((p < 0) | (p > 1)) or p[0] >= p[2] or p[1] >= p[3]:
        return 'UNKNOWN_EVIDENCE'
    h, w = fresh_mask.shape
    x, y = int(round((p[0]+p[2])*.5*(w-1))), int(round(p[3]*(h-1)))
    if p[3] >= .995 or not allowed[y, x]:
        return 'UNKNOWN_EVIDENCE'
    if not fresh_mask.any():
        return 'NO_CURRENT_ZEBRA_EVIDENCE'
    # Small contact neighbourhood tolerates the person's own occlusion of paint.
    radius = 3
    patch = fresh_mask[max(0,y-radius):min(h,y+radius+1),max(0,x-radius):min(w,x+radius+1)]
    if np.count_nonzero(patch) >= 3:
        return 'ZEBRA_CONTACT_CANDIDATE'
    return 'NO_OBSERVED_CONTACT'


def relation_record(track_id, relation):
    return dict(track_id=track_id, image_relation=relation,
                ego_path='UNRESOLVED', yielding_decision='NOT_EVALUATED', driving_permission=False)
