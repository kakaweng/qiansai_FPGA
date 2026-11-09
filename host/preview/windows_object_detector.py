import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np


MODEL_URL = "https://github.com/ultralytics/yolov5/releases/download/v7.0/yolov5s.onnx"
MODEL_API_URL = "https://api.github.com/repos/ultralytics/yolov5/releases/assets/91195037"
MODEL_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_MODEL = Path(__file__).resolve().parent / "models" / "yolov5s.onnx"
COCO_LABELS = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe",
    "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
    "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl",
    "banana", "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza",
    "donut", "cake", "chair", "couch", "potted plant", "bed", "dining table", "toilet",
    "tv", "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave", "oven",
    "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def output_layout(outputs):
    shapes = [list(output.shape) for output in outputs]
    if len(outputs) == 1:
        output = outputs[0]
        if output.ndim == 3 and output.shape[0] == 1:
            if output.shape[2] == 85:
                return "decoded_Nx85", shapes
            if output.shape[1] == 85:
                return "decoded_85xN", shapes
    if len(outputs) == 3:
        expected = [[1, 255, size, size] for size in (80, 40, 20)]
        if sorted(shapes, key=lambda shape: shape[-1], reverse=True) == expected:
            return "post_sigmoid_NCHW_heads", shapes
    raise ValueError(f"不支持的检测输出 {shapes}；需要 COCO80 YOLOv5 解码输出或三路 NCHW 检测头")


def load_network(model_path, threads):
    cv2.setNumThreads(threads)
    network = cv2.dnn.readNetFromONNX(str(model_path))
    network.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
    network.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
    return network


def probe_network(network):
    network.setInput(np.zeros((1, 3, 640, 640), dtype=np.float32))
    outputs = list(network.forward(network.getUnconnectedOutLayersNames()))
    layout, shapes = output_layout(outputs)
    if any(not np.isfinite(output).all() for output in outputs):
        raise ValueError("模型校验推理出现非有限数值")
    return layout, shapes


class WindowsObjectDetector:
    def __init__(self, model_path=None, confidence=0.25, nms_threshold=0.45, threads=4, backend="cpu"):
        self.model_path = DEFAULT_MODEL if model_path is None else Path(model_path).resolve()
        if not self.model_path.is_file():
            raise FileNotFoundError(f"找不到检测模型：{self.model_path}。请先运行 python windows_object_detector.py --download")
        if not 0 < confidence < 1 or not 0 < nms_threshold < 1:
            raise ValueError("confidence 和 nms_threshold 必须介于 0 和 1 之间")
        if type(threads) is not int or threads < 1:
            raise ValueError("threads 必须为正整数")
        self.confidence = float(confidence)
        self.nms_threshold = float(nms_threshold)
        self.labels = COCO_LABELS
        self.manifest = {}
        manifest_path = Path(str(self.model_path) + ".json")
        if manifest_path.is_file():
            self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if self.manifest.get("sha256") != sha256_file(self.model_path):
                raise ValueError("ONNX 模型 SHA256 与旁侧模型清单不一致")
            if self.manifest.get("labels") != list(COCO_LABELS):
                raise ValueError("检测器需要固定的 COCO80 类别顺序")
        from windows_inference import load_inference_network
        self.network, self.runtime = load_inference_network(self.model_path, threads, backend)
        self.layout, self.output_shapes = probe_network(self.network)
        if self.layout == "post_sigmoid_NCHW_heads":
            if self.manifest.get("activation") != "post_sigmoid" or "anchors" not in self.manifest:
                raise ValueError("三路检测头需要模型清单明确 activation=post_sigmoid 与实际 anchors")
            self.anchors = np.asarray(self.manifest["anchors"], dtype=np.float32)
            if self.anchors.shape != (3, 3, 2) or not np.isfinite(self.anchors).all() or (self.anchors <= 0).any():
                raise ValueError("anchors 必须为三组正数 [3,3,2]")
        self.last_inference_ms = None

    def _predictions(self, outputs):
        if self.layout == "decoded_Nx85":
            return outputs[0][0]
        if self.layout == "decoded_85xN":
            return outputs[0][0].T
        predictions = []
        ordered = sorted(outputs, key=lambda output: output.shape[-1], reverse=True)
        for head_index, output in enumerate(ordered):
            size = output.shape[-1]
            stride = 640 / size
            head = output.reshape(3, 85, size, size).transpose(0, 2, 3, 1).copy()
            if head.min() < -0.001 or head.max() > 1.001:
                raise ValueError("模型清单声称 post_sigmoid，但输出超出概率范围")
            rows, columns = np.meshgrid(np.arange(size), np.arange(size), indexing="ij")
            grid = np.stack((columns, rows), axis=-1)
            head[..., :2] = (head[..., :2] * 2 - 0.5 + grid) * stride
            head[..., 2:4] = np.square(head[..., 2:4] * 2) * self.anchors[head_index, :, None, None, :]
            predictions.append(head.reshape(-1, 85))
        return np.concatenate(predictions, axis=0)

    def detect(self, image):
        if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("detect 输入必须是 OpenCV BGR uint8 三通道图像")
        height, width = image.shape[:2]
        if height < 1 or width < 1:
            raise ValueError("输入图像为空")
        ratio = min(640 / width, 640 / height)
        resized_width = max(1, min(640, round(width * ratio)))
        resized_height = max(1, min(640, round(height * ratio)))
        left = (640 - resized_width) // 2
        top = (640 - resized_height) // 2
        resized = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((640, 640, 3), 114, dtype=np.uint8)
        canvas[top:top + resized_height, left:left + resized_width] = resized
        blob = cv2.dnn.blobFromImage(canvas, scalefactor=1 / 255, swapRB=True, crop=False)
        started = time.perf_counter()
        self.network.setInput(blob)
        outputs = list(self.network.forward(self.network.getUnconnectedOutLayersNames()))
        self.last_inference_ms = (time.perf_counter() - started) * 1000
        predictions = self._predictions(outputs).astype(np.float32, copy=False)
        finite = np.isfinite(predictions).all(axis=1)
        predictions = predictions[finite & (predictions[:, 4] >= self.confidence)]
        if len(predictions) == 0:
            return []
        classes = predictions[:, 5:].argmax(axis=1)
        scores = predictions[:, 4] * predictions[np.arange(len(predictions)), classes + 5]
        selected = scores >= self.confidence
        predictions, classes, scores = predictions[selected], classes[selected], scores[selected]
        boxes = np.column_stack((predictions[:, 0] - predictions[:, 2] / 2,
                                 predictions[:, 1] - predictions[:, 3] / 2,
                                 predictions[:, 2], predictions[:, 3]))
        kept = []
        for class_id in np.unique(classes):
            candidates = np.flatnonzero(classes == class_id)
            indices = cv2.dnn.NMSBoxes(boxes[candidates].tolist(), scores[candidates].tolist(),
                                       self.confidence, self.nms_threshold)
            kept.extend(candidates[np.asarray(indices, dtype=np.int64).reshape(-1)].tolist())
        detections = []
        scale_x, scale_y = resized_width / width, resized_height / height
        for index in sorted(kept, key=lambda candidate: float(scores[candidate]), reverse=True):
            box_left, box_top, box_width, box_height = boxes[index]
            first_x = max(0, min(width, round((float(box_left) - left) / scale_x)))
            first_y = max(0, min(height, round((float(box_top) - top) / scale_y)))
            last_x = max(0, min(width, round((float(box_left + box_width) - left) / scale_x)))
            last_y = max(0, min(height, round((float(box_top + box_height) - top) / scale_y)))
            if last_x <= first_x or last_y <= first_y:
                continue
            class_id = int(classes[index])
            detections.append({"class_id": class_id, "label": self.labels[class_id],
                               "score": float(scores[index]),
                               "box": (first_x, first_y, last_x, last_y)})
        return detections


def download_model(destination):
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".download.part")
    if temporary.exists():
        raise FileExistsError(f"发现未完成下载文件，请检查后移除：{temporary}")
    downloaded_from = None
    errors = []
    try:
        for url in (MODEL_URL, MODEL_API_URL):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "pcie-host-windows-detector/1.0",
                                                               "Accept": "application/octet-stream"})
                with urllib.request.urlopen(request, timeout=30) as response:
                    advertised = int(response.headers.get("Content-Length", "0"))
                    if advertised > MODEL_MAX_BYTES:
                        raise ValueError("下载内容超过 100 MiB 上限")
                    downloaded = 0
                    reported = 0.0
                    with temporary.open("xb") as stream:
                        while True:
                            block = response.read(1024 * 1024)
                            if not block:
                                break
                            downloaded += len(block)
                            if downloaded > MODEL_MAX_BYTES:
                                raise ValueError("下载内容超过 100 MiB 上限")
                            stream.write(block)
                            if time.monotonic() - reported >= 1:
                                print(f"下载官方 YOLOv5s：{downloaded / 1048576:.1f} MiB", flush=True)
                                reported = time.monotonic()
                    if downloaded == 0 or (advertised and downloaded != advertised):
                        raise ValueError("模型下载内容为空或长度不完整")
                downloaded_from = url
                break
            except (OSError, ValueError) as error:
                errors.append(f"{url}: {error}")
                temporary.unlink(missing_ok=True)
        if downloaded_from is None:
            raise RuntimeError("官方模型下载失败：\n" + "\n".join(errors))
        print("校验下载模型并执行一次 OpenCV CPU 推理…", flush=True)
        layout, shapes = probe_network(load_network(temporary, 4))
        if layout not in ("decoded_Nx85", "decoded_85xN"):
            raise ValueError(f"官方 YOLOv5s 资源输出不符合预期：{layout}")
        manifest = {
            "schema_version": "windows_detector_1.0", "model_version": "ultralytics-yolov5s-v7.0",
            "family": "yolov5", "origin": "Ultralytics official GitHub release v7.0",
            "url": MODEL_URL, "downloaded_from": downloaded_from,
            "filename": destination.name, "sha256": sha256_file(temporary),
            "size_bytes": temporary.stat().st_size,
            "verified_utc": datetime.now(timezone.utc).isoformat(),
            "labels": list(COCO_LABELS), "output_layout": layout, "output_shapes": shapes,
            "input": {"shape": [1, 3, 640, 640], "layout": "NCHW", "dtype": "float16",
                      "opencv_blob_dtype": "float32",
                      "color_order": "RGB", "normalization": "divide_255",
                      "resize": "bilinear_letterbox", "padding_value": 114},
            "decoder": {"type": "yolov5_decoded", "score": "objectness_times_class_probability",
                        "nms": "per_class", "box_output": "original_image_xyxy_integer"},
            "quantization": {"method": "none", "precision": "float16"},
            "verification": {"backend": "OpenCV DNN CPU", "opencv_version": cv2.__version__,
                             "actual_forward_verified": True, "all_outputs_finite": True},
        }
        manifest_path = Path(str(destination) + ".json")
        manifest_temporary = manifest_path.with_name(manifest_path.name + ".download.part")
        with manifest_temporary.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, destination)
        os.replace(manifest_temporary, manifest_path)
        print(f"模型已下载并验证：{destination}\nSHA256：{manifest['sha256']}\n输出：{shapes}")
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Windows 本地 OpenCV DNN YOLOv5s COCO80 检测")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--download", action="store_true", help="下载并验证官方模型；构造检测器不会自动下载")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--seconds", type=float, default=0)
    parser.add_argument("--output", type=Path, help="保存检测叠加图片")
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--threads", type=int, default=4)
    arguments = parser.parse_args()
    try:
        if arguments.download:
            download_model(arguments.model)
        if arguments.image is None and arguments.video is None:
            if not arguments.download:
                parser.error("请指定 --download、--image 或 --video")
            return 0
        if arguments.image is not None and arguments.video is not None:
            parser.error("--image 与 --video 只能选择一个")
        if arguments.seconds < 0:
            parser.error("--seconds 不能小于 0")
        if arguments.image is not None:
            image = cv2.imread(str(arguments.image))
        else:
            capture = cv2.VideoCapture(str(arguments.video))
            try:
                capture.set(cv2.CAP_PROP_POS_MSEC, arguments.seconds * 1000)
                success, image = capture.read()
                if not success:
                    image = None
            finally:
                capture.release()
        if image is None:
            raise ValueError("无法读取所选图像或视频帧")
        detector = WindowsObjectDetector(arguments.model, arguments.confidence, threads=arguments.threads)
        started = time.perf_counter()
        detections = detector.detect(image)
        print(json.dumps({"model": str(arguments.model), "layout": detector.layout,
                          "elapsed_ms": (time.perf_counter() - started) * 1000,
                          "inference_ms": detector.last_inference_ms,
                          "image_shape": list(image.shape), "detections": detections}, ensure_ascii=False, indent=2))
        if arguments.output is not None:
            for detection in detections:
                first_x, first_y, last_x, last_y = detection["box"]
                cv2.rectangle(image, (first_x, first_y), (last_x, last_y), (0, 220, 0), 2)
                text = f"{detection['label']} {detection['score']:.2f}"
                cv2.putText(image, text, (first_x, max(16, first_y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 0), 2)
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(arguments.output), image):
                raise OSError("检测叠加图片保存失败")
        return 0
    except (OSError, ValueError, RuntimeError, cv2.error) as error:
        print(f"目标检测失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
