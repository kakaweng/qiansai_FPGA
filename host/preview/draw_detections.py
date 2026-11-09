import argparse
from pathlib import Path
import re

from frame_to_png import FRAME_BYTES, FRAME_HEIGHT, FRAME_WIDTH, frame_checksum, read_frame
from frame_format import RGB565_BYTES, to_bgr


FRAME_PATTERN = re.compile(
    r"PCIe frame verified: sequence=(\d+) bytes=(\d+) checksum=0x([0-9a-fA-F]{8})"
)
SUMMARY_PATTERN = re.compile(
    r"YOLO detections: raw=(\d+) kept=(\d+) confidence=[0-9.]+ nms=[0-9.]+"
)
DETECTION_PATTERN = re.compile(
    r"Detection\[(\d+)\]: class=(\d+) label=(.+?) score=([0-9.]+) "
    r"box=(-?\d+),(-?\d+),(-?\d+),(-?\d+)"
)
SUCCESS_PATTERN = re.compile(
    r"PCIe-to-RKNN single-frame inference passed: sequence=(\d+)"
)


def unique_match(pattern, lines, description):
    matches = [match for line in lines if (match := pattern.fullmatch(line))]
    if len(matches) != 1:
        raise ValueError(f"日志必须包含一次完整推理的{description}，实际找到 {len(matches)} 条")
    return matches[0]


def read_detections(path):
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    frame = unique_match(FRAME_PATTERN, lines, "帧校验信息")
    summary = unique_match(SUMMARY_PATTERN, lines, "检测数量信息")
    success = unique_match(SUCCESS_PATTERN, lines, "成功信息")
    sequence = int(frame[1])
    if int(frame[2]) not in (FRAME_BYTES, RGB565_BYTES) or int(success[1]) != sequence:
        raise ValueError("日志中的帧大小或推理序号不匹配")

    detections = []
    for line in lines:
        if not line.startswith("Detection["):
            continue
        match = DETECTION_PATTERN.fullmatch(line)
        if match is None:
            raise ValueError(f"检测结果格式错误：{line}")
        detection_index = int(match[1])
        class_id = int(match[2])
        score = float(match[4])
        box = tuple(int(match[index]) for index in range(5, 9))
        left, top, right, bottom = box
        if detection_index != len(detections) or not 0 <= class_id < 80:
            raise ValueError("检测序号不连续或类别超出 COCO 80 类范围")
        if not 0 <= score <= 1:
            raise ValueError("检测置信度超出 0～1 范围")
        if not (0 <= left <= right <= FRAME_WIDTH and 0 <= top <= bottom <= FRAME_HEIGHT):
            raise ValueError(f"检测框超出原图范围：{box}")
        detections.append({"class_id": class_id, "label": match[3], "score": score, "box": box})

    if len(detections) != int(summary[2]) or int(summary[1]) < len(detections):
        raise ValueError("日志中的检测框数量不匹配，可能没有保存完整日志")
    return sequence, int(frame[3], 16), detections


def draw_boxes(image, detections, cv2, scale=1.0, title=None):
    image_height, image_width = image.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX
    colors = {0: (255, 160, 0), 2: (0, 255, 0), 24: (0, 180, 255)}
    label_regions = []
    if title:
        (title_width, title_height), baseline = cv2.getTextSize(title, font, 0.8, 2)
        title_bottom = title_height + baseline + 20
        label_regions.append((0, 0, title_width + 20, title_bottom))
    font_scale = max(0.5, 0.75 * scale)

    for detection in detections:
        left, top, right, bottom = (round(value * scale) for value in detection["box"])
        left = min(left, image_width - 1)
        top = min(top, image_height - 1)
        right = min(right, image_width - 1)
        bottom = min(bottom, image_height - 1)
        color = colors.get(detection["class_id"], (255, 0, 255))
        cv2.rectangle(image, (left, top), (right, bottom), color, max(1, round(3 * scale)))

    for detection in detections:
        left, top, right, bottom = (round(value * scale) for value in detection["box"])
        color = colors.get(detection["class_id"], (255, 0, 255))
        text = f"{detection['label']} {detection['score']:.1%}"
        (text_width, text_height), baseline = cv2.getTextSize(text, font, font_scale, 2)
        label_width = text_width + 8
        label_height = text_height + baseline + 8
        label_left = min(left, max(0, image_width - label_width))
        label_top = max(0, min(top - label_height, image_height - label_height))
        candidate_tops = list(range(label_top, -1, -(label_height + 4)))
        candidate_tops += list(range(top, image_height - label_height + 1, label_height + 4))
        for candidate_top in candidate_tops:
            region = (label_left, candidate_top, label_left + label_width, candidate_top + label_height)
            if all(
                region[2] < previous[0] or region[0] > previous[2]
                or region[3] < previous[1] or region[1] > previous[3]
                for previous in label_regions
            ):
                label_top = candidate_top
                break
        label_regions.append((label_left, label_top, label_left + label_width, label_top + label_height))
        cv2.rectangle(
            image, (label_left, label_top),
            (label_left + label_width, label_top + label_height), color, -1
        )
        cv2.putText(
            image, text, (label_left + 4, label_top + text_height + 4),
            font, font_scale, (0, 0, 0), 2, cv2.LINE_AA
        )

    if title:
        cv2.rectangle(image, (0, 0), (title_width + 20, title_bottom), (0, 0, 0), -1)
        cv2.putText(image, title, (10, title_height + 10), font, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
    return image


def draw_detections(frame, sequence, detections, cv2, numpy):
    image = to_bgr(frame, numpy)
    return draw_boxes(image, detections, cv2, title=f"Sequence {sequence} | detections {len(detections)}")


def main():
    parser = argparse.ArgumentParser(description="将单帧 RKNN 日志中的检测框绘制到对应原图")
    parser.add_argument("input", type=Path, help="本次推理保存的 PGM、PPM 或 RGB565 原图")
    parser.add_argument("log", type=Path, help="同一次 pcie_rknn_probe 运行的完整日志")
    parser.add_argument("output", type=Path, help="带框 PNG 的保存路径")
    arguments = parser.parse_args()

    try:
        import cv2
        import numpy
    except ImportError:
        parser.exit(1, "缺少绘框依赖，请运行：sudo apt-get install python3-opencv python3-numpy\n")

    try:
        if arguments.output.suffix.lower() != ".png":
            raise ValueError("输出文件必须使用 .png 扩展名")
        frame = read_frame(arguments.input)
        sequence, expected_checksum, detections = read_detections(arguments.log)
        actual_checksum = frame_checksum(frame)
        if actual_checksum != expected_checksum:
            raise ValueError(
                f"图像与检测日志不是同一帧：图像校验和 0x{actual_checksum:08x}，"
                f"日志校验和 0x{expected_checksum:08x}"
            )
        image = draw_detections(frame, sequence, detections, cv2, numpy)
        encoded_ok, encoded = cv2.imencode(".png", image)
        if not encoded_ok:
            raise ValueError("PNG 编码失败")
        arguments.output.write_bytes(encoded.tobytes())
    except (OSError, ValueError, cv2.error) as error:
        parser.exit(1, f"绘框失败：{error}\n")

    print(f"绘框完成：sequence={sequence}，目标数量={len(detections)}，checksum=0x{actual_checksum:08x}")
    print(f"带框 PNG 已保存：{arguments.output}")


if __name__ == "__main__":
    main()
