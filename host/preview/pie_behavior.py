"""PIE pedestrian forecast contract. Scores never grant a driving permission."""
import json
from pathlib import Path
import cv2
import numpy as np

HISTORY = 6
STEP_SECONDS = .2
FEATURES = 960
LABELS = ('not-crossing', 'crossing', 'crossing-irrelevant')


def crop_tensor(frame, box):
    """RGB, 25% context each side, 128 square resize, ImageNet normalization."""
    box = np.asarray(box, float)
    if box.shape != (4,) or not np.isfinite(box).all() or np.any(box < 0) or np.any(box > 1):
        raise ValueError('Invalid normalized box')
    if box[0] >= box[2] or box[1] >= box[3]:
        raise ValueError('Empty box')
    h, w = frame.shape[:2]
    size = box[2:] - box[:2]
    lo = np.maximum(0, box[:2] - .25 * size)
    hi = np.minimum(1, box[2:] + .25 * size)
    x1, y1 = np.floor(lo * [w, h]).astype(int)
    x2, y2 = np.ceil(hi * [w, h]).astype(int)
    crop = cv2.resize(frame[y1:y2, x1:x2], (128, 128), interpolation=cv2.INTER_LINEAR)
    rgb = crop[:, :, ::-1].astype(np.float32) / 255
    rgb = (rgb - np.array([.485, .456, .406], np.float32)) / np.array([.229, .224, .225], np.float32)
    return np.ascontiguousarray(rgb.transpose(2, 0, 1))


def geometry(boxes):
    boxes = np.asarray(boxes, np.float32)
    if boxes.shape[-2:] != (HISTORY, 4) or not np.isfinite(boxes).all():
        raise ValueError('Expected six chronological boxes')
    if np.any(boxes < 0) or np.any(boxes > 1) or np.any(boxes[..., 2:] <= boxes[..., :2]):
        raise ValueError('Invalid history geometry')
    center = (boxes[..., :2] + boxes[..., 2:]) * .5
    size = boxes[..., 2:] - boxes[..., :2]
    state = np.concatenate((center, size), -1)
    delta = np.diff(state, axis=-2, prepend=state[..., :1, :])
    relative = state - state[..., -1:, :]
    return np.concatenate((state, delta * 5, relative), -1).astype(np.float32)


def build_model():
    import torch
    from torch import nn

    class Forecast(nn.Module):
        def __init__(self):
            super().__init__()
            self.register_buffer('appearance_mean', torch.zeros(FEATURES))
            self.register_buffer('appearance_std', torch.ones(FEATURES))
            self.register_buffer('geometry_mean', torch.zeros(12))
            self.register_buffer('geometry_std', torch.ones(12))
            self.visual = nn.Sequential(nn.Linear(FEATURES, 48), nn.ReLU(), nn.Dropout(.35))
            self.temporal = nn.GRU(60, 48, batch_first=True)
            self.head = nn.Sequential(nn.Linear(48, 32), nn.ReLU(), nn.Dropout(.25))
            self.future = nn.Linear(32, 1)
            self.current = nn.Linear(32, 3)

        def forward(self, appearance, geom):
            appearance = ((appearance-self.appearance_mean)/self.appearance_std).clamp(-6, 6)
            geom = ((geom-self.geometry_mean)/self.geometry_std).clamp(-6, 6)
            x = torch.cat((self.visual(appearance), geom), -1)
            x, _ = self.temporal(x)
            hidden = self.head(x[:, -1])
            return self.future(hidden).squeeze(-1), self.current(hidden)

    return Forecast()


def build_encoder():
    from torch import nn
    from torchvision.models.segmentation import lraspp_mobilenet_v3_large, LRASPP_MobileNet_V3_Large_Weights

    class Encoder(nn.Module):
        def __init__(self):
            super().__init__()
            self.backbone = lraspp_mobilenet_v3_large(weights=LRASPP_MobileNet_V3_Large_Weights.DEFAULT).backbone

        def forward(self, rgb):
            return self.backbone(rgb)['high'].mean((2, 3))

    return Encoder().eval()


class BehaviorPredictor:
    def __init__(self, folder):
        import onnxruntime as ort
        import hashlib
        folder = Path(folder)
        self.manifest = json.loads((folder / 'behavior.manifest.json').read_text(encoding='utf-8'))
        if self.manifest['history_samples'] != HISTORY or self.manifest['labels_current'] != list(LABELS):
            raise ValueError('Behavior contract mismatch')
        for name, digest in self.manifest['onnx_sha256'].items():
            if hashlib.sha256((folder/name).read_bytes()).hexdigest() != digest:
                raise ValueError('Behavior model hash mismatch')
        options = ort.SessionOptions(); options.intra_op_num_threads = 4
        self.encoder = ort.InferenceSession(str(folder/'appearance.onnx'), options, providers=['CPUExecutionProvider'])
        self.forecast = ort.InferenceSession(str(folder/'forecast.onnx'), options, providers=['CPUExecutionProvider'])

    def embed(self, frame, boxes):
        if not boxes: return np.empty((0, FEATURES), np.float32)
        return self.encoder.run(None, {'rgb': np.stack([crop_tensor(frame, b) for b in boxes])})[0]

    def predict(self, features, boxes, timestamps):
        times = np.asarray(timestamps, float)
        if times.shape != (HISTORY,) or not np.isfinite(times).all() or np.any(np.abs(np.diff(times)-STEP_SECONDS) > .04):
            raise ValueError('History must contain six fresh 5Hz observations')
        features = np.asarray(features, np.float32)
        if features.shape != (HISTORY, FEATURES) or not np.isfinite(features).all():
            raise ValueError('Invalid appearance history')
        f, current = self.forecast.run(None, {'appearance': features[None], 'geometry': geometry(boxes)[None]})
        score = float(1/(1+np.exp(-np.clip(f[0], -40, 40))))
        current = current[0] - current[0].max(); current = np.exp(current); current /= current.sum()
        return dict(crossing_score=score, current_scores=current.tolist(),
                    horizon_seconds=1., calibrated_probability=False, driving_permission=False)
