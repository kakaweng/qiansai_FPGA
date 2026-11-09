import hashlib
import json
import time
from pathlib import Path


class WindowsLaneSegmenter:
    def __init__(self, model_path=None, backend="cpu", threads=4):
        import cv2
        import numpy
        self.cv2, self.numpy = cv2, numpy
        self.path = Path(model_path) if model_path else Path(__file__).resolve().parent / "models" / "yolop-640-640.onnx"
        manifest = json.loads(Path(str(self.path) + ".json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        if digest != manifest["sha256"]:
            raise ValueError("Lane model SHA256 does not match its manifest")
        from windows_inference import load_inference_network
        self.net, self.runtime = load_inference_network(self.path, threads, backend)
        if not {"drive_area_seg", "lane_line_seg"}.issubset(self.net.getUnconnectedOutLayersNames()):
            raise ValueError("Lane model has incompatible output names")
        self.last_inference_ms = None

    def segment(self, image):
        cv2, numpy = self.cv2, self.numpy
        height, width = image.shape[:2]
        ratio = min(640 / width, 640 / height)
        resized_width, resized_height = round(width * ratio), round(height * ratio)
        left, top = (640 - resized_width) // 2, (640 - resized_height) // 2
        canvas = numpy.full((640, 640, 3), 114, dtype=numpy.uint8)
        canvas[top:top + resized_height, left:left + resized_width] = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(numpy.float32) / 255
        rgb = (rgb - numpy.array([0.485, 0.456, 0.406], dtype=numpy.float32)) / numpy.array([0.229, 0.224, 0.225], dtype=numpy.float32)
        self.net.setInput(numpy.ascontiguousarray(rgb.transpose(2, 0, 1)[None]))
        started = time.perf_counter()
        outputs = dict(zip(self.net.getUnconnectedOutLayersNames(), self.net.forward(self.net.getUnconnectedOutLayersNames())))
        drive, lanes = outputs["drive_area_seg"], outputs["lane_line_seg"]
        self.last_inference_ms = round((time.perf_counter() - started) * 1000, 2)
        masks = []
        for output in (drive, lanes):
            if output.shape != (1, 2, 640, 640) or not numpy.all(numpy.isfinite(output)):
                raise ValueError("Lane segmentation output is invalid")
            cropped = output[0, :, top:top + resized_height, left:left + resized_width]
            mask = (cropped[1] > cropped[0]).astype(numpy.uint8) * 255
            masks.append(cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST))
        return {"drivable_mask": masks[0], "lane_mask": masks[1], "inference_ms": self.last_inference_ms,
                "source": "YOLOP_BDD100K_pretrained", "model_sha256": "cd66a3e0087a7258ae07768cc02cb742eed93865727ae4c9baf969b8fa190696"}
