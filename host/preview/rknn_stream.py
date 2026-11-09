import struct


RESULT_VERSION = 2
RESULT_VERSION_SPLIT = 3
RESULT_VERSION_COLOR = 4
RESULT_HEADER_BYTES = 80
RESULT_HEADER_BYTES_SPLIT = 84
RESULT_HEADER_BYTES_COLOR = 88
RESULT_EXTENSION = struct.Struct("<II6IQ")
RESULT_EXTENSION_SPLIT = struct.Struct("<II6IQI")
RESULT_EXTENSION_COLOR = struct.Struct("<II6IQII")
RESULT_FLAG_DETECTION_ONLY = 1
RESULT_FLAG_MATCHED = 2
DETECTION_ENTRY = struct.Struct("<6I")
MAX_DETECTIONS = 4096
TIMING_NAMES = ("read_us", "preprocess_us", "inputs_us", "run_us", "outputs_us", "postprocess_us")
COCO_LABELS = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
    "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange", "broccoli",
    "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard",
    "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book",
    "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
)


def decode_extension(data, timestamp_ns, flags=0):
    pixel_format = 1
    if len(data) == RESULT_EXTENSION_COLOR.size:
        count, entry_bytes, *values, flags, pixel_format = RESULT_EXTENSION_COLOR.unpack(data)
        if pixel_format not in (1, 2):
            raise RuntimeError(f"不支持的像素格式：{pixel_format}")
    elif len(data) == RESULT_EXTENSION_SPLIT.size:
        count, entry_bytes, *values, encoded_flags = RESULT_EXTENSION_SPLIT.unpack(data)
        flags = encoded_flags
    else:
        count, entry_bytes, *values = RESULT_EXTENSION.unpack(data)
    if count > MAX_DETECTIONS or entry_bytes != DETECTION_ENTRY.size:
        raise RuntimeError(f"检测元数据错误：count={count}, entry_bytes={entry_bytes}")
    completed_ns = values[-1]
    if not (flags & RESULT_FLAG_DETECTION_ONLY) and completed_ns < timestamp_ns:
        raise RuntimeError("检测完成时间早于帧采集时间")
    return count, dict(zip(TIMING_NAMES, values[:-1]), completed_ns=completed_ns,
                        flags=flags, pixel_format=pixel_format)


def decode_detections(data, width, height):
    detections = []
    for class_id, score, left, top, right, bottom in DETECTION_ENTRY.iter_unpack(data):
        if class_id >= len(COCO_LABELS) or score > 1000000:
            raise RuntimeError("检测类别或置信度无效")
        if not (left <= right <= width and top <= bottom <= height):
            raise RuntimeError("检测框坐标超出原图范围")
        detections.append({
            "class_id": class_id, "label": COCO_LABELS[class_id],
            "score": score / 1000000.0, "box": (left, top, right, bottom),
        })
    return detections
