"""Causal prediction of the first annotated crossing onset; display experiments only."""
import math
from pathlib import Path
import json
import numpy as np
from pie_behavior import HISTORY, FEATURES, geometry

HORIZONS=(18,30)


def onset_info(records):
    crossing=sorted(f for f,r in records.items() if r.get('cross')=='crossing')
    if not crossing:return dict(frame=None,observable=True,reason='no_recorded_crossing')
    first=crossing[0]
    observable=(first-1 in records and records[first-1].get('cross')=='not-crossing'
                and all(records.get(first+i,{}).get('cross')=='crossing' for i in range(3)))
    return dict(frame=first,observable=observable,reason='observed_transition' if observable else 'left_censored_gap_or_unstable_transition')


def anticipation_labels(records,end,horizons=HORIZONS):
    if type(end) is not int or len(horizons)!=2 or not 0<horizons[0]<horizons[1]:raise ValueError('Invalid anticipation time contract')
    info=onset_info(records)
    if not info['observable'] or info['frame'] is not None and info['frame']<=end:return None
    first=end-30;future=end+max(horizons)
    if any(f not in records for f in range(first,future+1)):return None
    known=('not-crossing','crossing','crossing-irrelevant')
    if any(records[f].get('cross') not in known for f in range(first,future+1)):return None
    if any(records[f].get('cross')=='crossing' or records[f].get('occlusion')=='full' for f in range(first,end+1)):return None
    group='irrelevant_history' if any(records[f].get('cross')=='crossing-irrelevant' for f in range(first,end+1)) else 'strict_not_crossing'
    lead=info['frame']-end if info['frame'] is not None else None
    return dict(past=list(range(first,end+1,6)),labels=[int(lead is not None and 0<lead<=h) for h in horizons],
                first_onset=info['frame'],lead_frames=lead,group=group)


def build_anticipator(kind='temporal'):
    import torch
    from torch import nn
    if kind not in ('geometry','visual_linear','temporal'):raise ValueError('Unknown anticipator')

    class Anticipator(nn.Module):
        def __init__(self):
            super().__init__()
            self.register_buffer('appearance_mean',torch.zeros(FEATURES))
            self.register_buffer('appearance_basis',torch.zeros(FEATURES,16))
            self.register_buffer('appearance_scale',torch.ones(16))
            self.register_buffer('geometry_mean',torch.zeros(12))
            self.register_buffer('geometry_scale',torch.ones(12))
            self.kind=kind
            if kind=='temporal':
                self.temporal=nn.GRU(28,12,batch_first=True)
                self.head=nn.Sequential(nn.Dropout(.5),nn.Linear(12,2))
            else:
                self.head=nn.Linear(36 if kind=='geometry' else 84,2)

        def forward(self,appearance,geom):
            vis=(((appearance-self.appearance_mean)@self.appearance_basis)/self.appearance_scale).clamp(-5,5)
            geom=((geom-self.geometry_mean)/self.geometry_scale).clamp(-5,5)
            x=geom if self.kind=='geometry' else torch.cat((vis,geom),-1)
            if self.kind=='temporal':
                seq,_=self.temporal(x);logits=self.head(seq[:,-1])
            else:
                x=torch.cat((x[:,-1],x.mean(1),x[:,-1]-x[:,0]),-1);logits=self.head(x)
            first=torch.sigmoid(logits[:,0]);second=first+(1-first)*torch.sigmoid(logits[:,1])
            return torch.stack((first,second),-1)

    return Anticipator()


class AnticipationPredictor:
    def __init__(self,folder):
        import hashlib
        import onnxruntime as ort
        folder=Path(folder);self.manifest=json.loads((folder/'anticipation.manifest.json').read_text(encoding='utf-8'))
        if self.manifest['version']!=2 or self.manifest['horizons_seconds']!=[.6,1.] or self.manifest['history_samples']!=HISTORY:
            raise ValueError('Unsupported anticipation contract')
        for name,digest in self.manifest['onnx_sha256'].items():
            if hashlib.sha256((folder/name).read_bytes()).hexdigest()!=digest:raise ValueError('Anticipation hash mismatch')
        options=ort.SessionOptions();options.intra_op_num_threads=4
        self.session=ort.InferenceSession(str(folder/'anticipation.onnx'),options,providers=['CPUExecutionProvider'])

    def predict(self,features,boxes,timestamps):
        times=np.asarray(timestamps,float);features=np.asarray(features,np.float32)
        if times.shape!=(6,) or not np.isfinite(times).all() or np.any(np.abs(np.diff(times)-.2)>.04):raise ValueError('Need six fresh chronological 5Hz observations')
        if features.shape!=(6,960) or not np.isfinite(features).all():raise ValueError('Invalid appearance history')
        feed={'appearance':features[None],'geometry':geometry(boxes)[None]}
        names={x.name for x in self.session.get_inputs()}
        scores=self.session.run(None,{k:v for k,v in feed.items() if k in names})[0][0]
        if scores.shape!=(2,) or not np.isfinite(scores).all() or not 0<=scores[0]<=scores[1]<=1:raise ValueError('Invalid cumulative forecast')
        return dict(onset_scores=scores.tolist(),horizons_seconds=[.6,1.],
                    eligibility='requires_independent_evidence_of_not_yet_crossing',calibrated_probability=False,
                    driving_permission=False)


def alert_metrics(rows,scores,labels,threshold,head=1,confirmations=2):
    scores=np.asarray(scores,float);labels=np.asarray(labels)
    if scores.shape!=(len(rows),) or labels.shape!=(len(rows),) or not np.isfinite(scores).all():raise ValueError('Invalid evaluation rows')
    if not np.isin(labels,[0,1]).all() or np.any((scores<0)|(scores>1)):raise ValueError('Invalid scores or labels')
    if type(confirmations) is not int or confirmations<1:raise ValueError('Invalid confirmation count')
    if not math.isfinite(threshold) or not 0<=threshold<=1.000001:raise ValueError('Invalid threshold')
    grouped={}
    for i,row in enumerate(rows):grouped.setdefault((row['clip'],row['id']),[]).append(i)
    episodes=0;false_episodes=0;exposure=0.;eligible=set();detected=set();valid_starts=set();leads=[];valid_start_leads=[];negative_tracks=set();false_tracks=set()
    for key,indices in grouped.items():
        ordered=sorted(indices,key=lambda i:rows[i]['end']);votes=0;active=False;last=None;found=[];start_leads=[]
        if not any(labels[i] for i in ordered):negative_tracks.add(key)
        for i in ordered:
            row=rows[i];at=row['end'];consecutive=last is not None and at-last==6
            if at==last:continue
            if not consecutive:votes=0;active=False
            exposure+=.2
            if labels[i]:eligible.add(key)
            if scores[i]>=threshold:votes+=1
            else:votes=0;active=False
            if votes>=confirmations and not active:
                episodes+=1;active=True
                if not labels[i]:false_episodes+=1;false_tracks.add(key)
                else:valid_starts.add(key);start_leads.append(row['lead_frames']/30)
            if votes>=confirmations and labels[i]:
                detected.add(key);found.append(row['lead_frames']/30)
            last=at
        if found:leads.append(max(found))
        if start_leads:valid_start_leads.append(max(start_leads))
    return dict(alert_episodes=episodes,false_alert_episodes=false_episodes,observed_person_seconds=round(exposure,3),
                false_alerts_per_person_minute=false_episodes/max(1e-9,exposure/60),eligible_onset_tracks=len(eligible),
                detected_onset_tracks=len(detected),onset_track_recall=len(detected)/max(1,len(eligible)),
                valid_start_onset_tracks=len(valid_starts),carried_early_alert_only_tracks=len(detected-valid_starts),
                valid_start_lead_seconds=valid_start_leads,
                median_valid_start_lead_seconds=float(np.median(valid_start_leads)) if valid_start_leads else None,
                median_first_valid_lead_seconds=float(np.median(leads)) if leads else None,
                valid_lead_seconds=leads,fully_negative_tracks=len(negative_tracks),
                fully_negative_tracks_with_alert=len(negative_tracks&false_tracks),
                confirmations=confirmations,note='Detected means active confirmed alert within target window; early false alerts can carry into that window. Valid starts count fresh alert episodes only. Observed person time, not vehicle time.')
