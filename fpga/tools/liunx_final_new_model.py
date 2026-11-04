#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
整合版：YOLO11-OBB 车牌定位 + LPRNet 车牌识别 + FPGA UDP 通信
支持图片、本地视频、FPGA 视频流（Camera / HDMI）
GUI 布局：左侧控制区（含 FPGA 按钮），右侧显示原始图和结果图，中间显示裁剪车牌
"""

import os
import sys
import io
import math
import time
import threading
import queue
import socket
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageTk, ImageDraw, ImageFont

# ===================== 配置 =========================
# ---------- 文件夹及模型路径（按树莓派环境修改） ----------
BASE_DIR = "/usr/local/lprnet/combine"          # 模型存放目录
YOLO_ONNX_PATH = os.path.join(BASE_DIR, "plate_obb.onnx")
LPR_ONNX_PATH  = os.path.join(BASE_DIR, "lpr_model.onnx")   # 通用 LPRNet ONNX
YOLO_INPUT_W = 640
YOLO_INPUT_H = 640
YOLO_HAS_OBJECTNESS = False
YOLO_CLASS_NAMES = ["plate"]
CONF_THRES = 0.25
IOU_THRES = 0.45

# ---------- LPRNet 字符集（与训练一致） ----------
PROVINCES = '京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼'
ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
DIGITS = '0123456789'
SPECIALS = '挂学使领港澳'
CHARS = PROVINCES + ALPHABET + DIGITS + SPECIALS
CHAR2IDX = {ch: i+1 for i, ch in enumerate(CHARS)}
IDX2CHAR = {i+1: ch for i, ch in enumerate(CHARS)}
NUM_CLASSES = len(CHARS) + 1
TYPE2IDX = {
    '普通蓝牌': 0, '单层黄牌': 1, '双层黄牌': 2,
    '新能源小型车': 3, '新能源大型车': 4,
    '黑色车牌': 5, '其他': 6
}
IDX2TYPE = {v: k for k, v in TYPE2IDX.items()}
NUM_TYPES = len(TYPE2IDX)

# ---------- FPGA 通信参数 ----------
FPGA_IP = "192.168.1.10"
FPGA_PORT = 8080
LOCAL_IP = "0.0.0.0"
LOCAL_PORT = 1234
FRAME_HEADER = b"\xF0\x5A\xA5\x0F"
HDMI_MAGIC = b"IM"
HDMI_HEADER_LEN = 14
DEFAULT_FRAME_WIDTH = 640
DEFAULT_FRAME_HEIGHT = 480
CAMERA_PIXEL_BYTES = 2
SOCKET_TIMEOUT = 0.02
SOCKET_RECV_SIZE = 65535
SOCKET_RCVBUF = 16 * 1024 * 1024
FRAME_TIMEOUT = 0.30
RESTART_INTERVAL = 1.0

# ---------- GUI 字体（树莓派常用中文字体） ----------
FONT_PATH = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
if not os.path.exists(FONT_PATH):
    FONT_PATH = None
    print("警告：未找到中文字体，将使用系统默认字体")

# ===================== YOLO OBB 检测器（来自 plate_detect_recog_gui） ========================
def imread_unicode(path: str) -> Optional[np.ndarray]:
    try:
        data = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        return img
    except:
        return None

def letterbox(img, new_shape=(640,640), color=(114,114,114)):
    shape = img.shape[:2]
    new_h, new_w = new_shape
    r = min(new_h/shape[0], new_w/shape[1])
    resized_w = int(round(shape[1]*r))
    resized_h = int(round(shape[0]*r))
    dw = (new_w - resized_w)/2
    dh = (new_h - resized_h)/2
    if shape[::-1] != (resized_w, resized_h):
        img = cv2.resize(img, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
    top = int(round(dh-0.1)); bottom = int(round(dh+0.1))
    left = int(round(dw-0.1)); right = int(round(dw+0.1))
    img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)
    return img, r, dw, dh

def clip_points(points, w, h):
    points[:,0] = np.clip(points[:,0], 0, w-1)
    points[:,1] = np.clip(points[:,1], 0, h-1)
    return points

def xywhr_to_corners(cx, cy, w, h, angle_rad):
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    dx = w/2.0; dy = h/2.0
    pts = np.array([[-dx,-dy],[dx,-dy],[dx,dy],[-dx,dy]], dtype=np.float32)
    rot = np.array([[cos_a,-sin_a],[sin_a,cos_a]], dtype=np.float32)
    pts = pts @ rot.T
    pts[:,0] += cx; pts[:,1] += cy
    return pts.astype(np.float32)

class YoloObbOnnxDetector:
    def __init__(self, model_path, conf_thres=0.25, iou_thres=0.45,
                 use_cuda=False, has_objectness=False):
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"YOLO ONNX 不存在: {model_path}")
        self.model_path = model_path
        self.conf_thres = conf_thres
        self.iou_thres = iou_thres
        self.has_objectness = has_objectness
        providers = ["CUDAExecutionProvider","CPUExecutionProvider"] if use_cuda else ["CPUExecutionProvider"]
        self.session = ort.InferenceSession(model_path, providers=providers)
        self.input = self.session.get_inputs()[0]
        self.input_name = self.input.name
        self.input_h = YOLO_INPUT_H
        self.input_w = YOLO_INPUT_W
        shape = self.input.shape
        if len(shape)==4 and isinstance(shape[2],int) and isinstance(shape[3],int):
            self.input_h, self.input_w = shape[2], shape[3]

    def preprocess(self, img_bgr):
        img_lb, ratio, dw, dh = letterbox(img_bgr, (self.input_h,self.input_w))
        img_rgb = cv2.cvtColor(img_lb, cv2.COLOR_BGR2RGB)
        x = img_rgb.astype(np.float32)/255.0
        x = np.transpose(x, (2,0,1))
        x = np.expand_dims(x, axis=0)
        return x, ratio, dw, dh

    def infer_raw(self, img_bgr):
        x, ratio, dw, dh = self.preprocess(img_bgr)
        outputs = self.session.run(None, {self.input_name: x})
        return outputs, ratio, dw, dh

    def _normalize_output(self, outputs):
        out = outputs[0]
        if out.ndim==3 and out.shape[0]==1:
            out = out[0]
        if out.ndim != 2:
            raise RuntimeError(f"YOLO 输出维度异常: {out.shape}")
        if out.shape[0] < out.shape[1] and out.shape[0] <= 200:
            out = out.T
        return out.astype(np.float32)

    def _parse_predictions(self, pred, orig_w, orig_h, ratio, dw, dh):
        if pred.shape[1] < 6:
            raise RuntimeError(f"YOLO OBB 输出列数太少: {pred.shape}")
        boxes = pred[:, 0:4]
        if self.has_objectness:
            obj = pred[:, 4:5]
            cls_scores = pred[:, 5:-1]
            scores_matrix = obj * cls_scores
        else:
            cls_scores = pred[:, 4:-1]
            scores_matrix = cls_scores
        angles = pred[:, -1]

        class_ids = np.argmax(scores_matrix, axis=1)
        scores = scores_matrix[np.arange(scores_matrix.shape[0]), class_ids]
        keep = scores >= self.conf_thres
        boxes, scores, class_ids, angles = boxes[keep], scores[keep], class_ids[keep], angles[keep]
        if boxes.shape[0] == 0:
            return []

        if np.max(boxes[:, 0:4]) <= 2.0:
            boxes[:, [0,2]] *= self.input_w
            boxes[:, [1,3]] *= self.input_h

        if np.max(np.abs(angles)) > math.pi*2:
            angles_rad = angles * math.pi / 180.0
        else:
            angles_rad = angles

        dets = []
        rects_for_nms = []
        scores_for_nms = []
        for box, score, class_id, angle_rad in zip(boxes, scores, class_ids, angles_rad):
            cx, cy, bw, bh = [float(v) for v in box]
            cx0 = (cx - dw)/ratio
            cy0 = (cy - dh)/ratio
            bw0 = bw/ratio
            bh0 = bh/ratio
            pts = xywhr_to_corners(cx0, cy0, bw0, bh0, float(angle_rad))
            pts = clip_points(pts, orig_w, orig_h)
            angle_deg = float(angle_rad * 180.0 / math.pi)
            rect = ((float(cx0), float(cy0)), (float(bw0), float(bh0)), angle_deg)
            rects_for_nms.append(rect)
            scores_for_nms.append(float(score))
            cls_id = int(class_id)
            cls_name = YOLO_CLASS_NAMES[cls_id] if 0 <= cls_id < len(YOLO_CLASS_NAMES) else str(cls_id)
            dets.append({
                "class_id": cls_id,
                "class_name": cls_name,
                "score": float(score),
                "points": pts,
                "rect": rect
            })

        keep_indices = []
        try:
            idxs = cv2.dnn.NMSBoxesRotated(rects_for_nms, scores_for_nms, self.conf_thres, self.iou_thres)
            if idxs is not None and len(idxs)>0:
                keep_indices = np.array(idxs).reshape(-1).tolist()
        except:
            order = np.argsort(-np.array(scores_for_nms))
            keep_indices = order[:50].tolist()
        return [dets[i] for i in keep_indices]

    def detect(self, img_bgr):
        orig_h, orig_w = img_bgr.shape[:2]
        outputs, ratio, dw, dh = self.infer_raw(img_bgr)
        pred = self._normalize_output(outputs)
        return self._parse_predictions(pred, orig_w, orig_h, ratio, dw, dh)

# ===================== LPRNet 识别器（来自 plate_detect_recog_gui） ========================
class LprRecognizer:
    def __init__(self, onnx_path, char_conf_thres=0.5):
        if not os.path.exists(onnx_path):
            raise FileNotFoundError(f"LPR ONNX 不存在: {onnx_path}")
        self.session = ort.InferenceSession(onnx_path, providers=['CPUExecutionProvider'])
        self.input_name = self.session.get_inputs()[0].name
        self.type_name = self.session.get_outputs()[0].name
        self.seq_name = self.session.get_outputs()[1].name
        self.char_conf_thres = char_conf_thres

    def _softmax(self, x, axis=-1):
        e_x = np.exp(x - np.max(x, axis=axis, keepdims=True))
        return e_x / np.sum(e_x, axis=axis, keepdims=True)

    def recognize(self, plate_img_bgr):
        img = cv2.cvtColor(plate_img_bgr, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (128, 48), interpolation=cv2.INTER_LINEAR)
        img = img.astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1,1,3)
        std  = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1,1,3)
        img = (img - mean) / std
        img = np.transpose(img, (2,0,1))
        img = np.expand_dims(img, axis=0)

        type_logits, seq_logits = self.session.run(
            [self.type_name, self.seq_name],
            {self.input_name: img}
        )

        type_probs = self._softmax(type_logits, axis=1)
        type_idx = np.argmax(type_probs, axis=1)[0]
        type_conf = type_probs[0, type_idx]
        type_name = IDX2TYPE.get(type_idx, "未知")

        seq_probs = self._softmax(seq_logits, axis=2)
        seq_preds = np.argmax(seq_logits, axis=2)
        plate_chars = []
        char_confs = []
        for t, idx in enumerate(seq_preds[0]):
            if idx != 0:
                prob = seq_probs[0, t, idx]
                if prob >= self.char_conf_thres:
                    plate_chars.append(IDX2CHAR.get(idx, ''))
                    char_confs.append(prob)
        plate_str = ''.join(plate_chars)
        avg_conf = np.mean(char_confs) if char_confs else 0.0
        return type_name, plate_str, type_conf, avg_conf

# ===================== 角点排序 & 扩展（来自 plate_detect_recog_gui） ========================
def order_points(pts):
    rect = np.zeros((4,2), dtype=np.float32)
    s = pts[np.argsort(pts[:,0]), :]
    left = s[:2, :]
    right = s[2:, :]
    left = left[np.argsort(left[:,1]), :]
    rect[0] = left[0]
    rect[3] = left[1]
    right = right[np.argsort(right[:,1]), :]
    rect[1] = right[0]
    rect[2] = right[1]
    return rect

def expand_points(pts, expand_pixels, img_h, img_w):
    center = np.mean(pts, axis=0)
    expanded = np.zeros_like(pts)
    for i in range(4):
        vec = pts[i] - center
        norm = np.linalg.norm(vec)
        if norm < 1e-6:
            expanded[i] = pts[i]
        else:
            vec_norm = vec / norm
            expanded[i] = pts[i] + vec_norm * expand_pixels
    expanded[:,0] = np.clip(expanded[:,0], 0, img_w-1)
    expanded[:,1] = np.clip(expanded[:,1], 0, img_h-1)
    return expanded

# ===================== 整合流水线 ========================
@dataclass
class PlateResult:
    yolo_conf: float
    type_name: str
    plate_str: str
    type_conf: float
    char_conf: float
    points: np.ndarray
    rect_img: np.ndarray

class PlateDetectRecogPipeline:
    def __init__(self, yolo_path, lpr_path, conf_thres=0.25, iou_thres=0.45,
                 use_cuda=False, char_conf_thres=0.5, expand_pixels=5):
        self.yolo = YoloObbOnnxDetector(yolo_path, conf_thres, iou_thres, use_cuda)
        self.lpr = LprRecognizer(lpr_path, char_conf_thres)
        self.expand_pixels = expand_pixels

    def process_frame(self, img_bgr):
        h, w = img_bgr.shape[:2]
        dets = self.yolo.detect(img_bgr)
        results = []
        for det in dets:
            pts = det["points"].astype(np.float32)
            ordered_pts = order_points(pts)
            expanded_pts = expand_points(ordered_pts, self.expand_pixels, h, w)
            dst_pts = np.array([[0, 0], [128, 0], [128, 48], [0, 48]], dtype=np.float32)
            M = cv2.getPerspectiveTransform(expanded_pts, dst_pts)
            plate_crop = cv2.warpPerspective(img_bgr, M, (128, 48))
            type_name, plate_str, type_conf, char_conf = self.lpr.recognize(plate_crop)
            res = PlateResult(
                yolo_conf=det["score"],
                type_name=type_name,
                plate_str=plate_str,
                type_conf=type_conf,
                char_conf=char_conf,
                points=pts.astype(np.int32),
                rect_img=plate_crop
            )
            results.append(res)

        draw_img = img_bgr.copy()
        for res in results:
            pts = res.points
            cv2.polylines(draw_img, [pts], isClosed=True, color=(0,220,0), thickness=2)
            for p in pts:
                cv2.circle(draw_img, (int(p[0]), int(p[1])), 3, (0,0,255), -1)
            label = f"{res.plate_str} ({res.type_name}) conf:{res.yolo_conf:.2f}"
            x = int(np.min(pts[:,0]))
            y = int(np.max(pts[:,1])) + 20
            draw_img = draw_text_cn(draw_img, label, (x, y), color=(0,0,255), font_size=18)
        return img_bgr, draw_img, results

# ===================== 中文绘制 ========================
def get_chinese_font(font_size=20):
    if FONT_PATH and os.path.exists(FONT_PATH):
        try:
            return ImageFont.truetype(FONT_PATH, font_size)
        except:
            pass
    return ImageFont.load_default()

def draw_text_cn(img_bgr, text, pos, color=(0,0,255), font_size=20):
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(img_rgb)
    draw = ImageDraw.Draw(pil_img)
    font = get_chinese_font(font_size)
    rgb_color = (color[2], color[1], color[0])
    draw.text(pos, text, fill=rgb_color, font=font)
    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

# ===================== FPGA UDP 通信模块（来自 liunx_final） ========================
def rgb565_to_bgr(frame_bytes, width, height):
    expected = width * height * CAMERA_PIXEL_BYTES
    raw = np.frombuffer(frame_bytes[:expected], dtype=np.uint8)
    if raw.size < expected:
        raise ValueError("incomplete RGB565 frame")
    raw = raw.reshape(-1, 2)
    rgb565 = (raw[:, 0].astype(np.uint16) << 8) | raw[:, 1].astype(np.uint16)
    r = ((rgb565 >> 11) & 0x1F) * 255 // 31
    g = ((rgb565 >> 5) & 0x3F) * 255 // 63
    b = (rgb565 & 0x1F) * 255 // 31
    return np.stack([b, g, r], axis=1).astype(np.uint8).reshape(height, width, 3)

def rgb888_to_bgr(frame_bytes, width, height):
    expected = width * height * 3
    raw = np.frombuffer(frame_bytes[:expected], dtype=np.uint8)
    if raw.size < expected:
        raise ValueError("incomplete RGB888 frame")
    rgb = raw.reshape(height, width, 3)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

def gray8_to_bgr(frame_bytes, width, height):
    expected = width * height
    raw = np.frombuffer(frame_bytes[:expected], dtype=np.uint8)
    if raw.size < expected:
        raise ValueError("incomplete gray frame")
    return cv2.cvtColor(raw.reshape(height, width), cv2.COLOR_GRAY2BGR)

def _le16(data, offset):
    return data[offset] | (data[offset + 1] << 8)

class CameraFrameAssembler:
    def __init__(self):
        self.width = DEFAULT_FRAME_WIDTH
        self.height = DEFAULT_FRAME_HEIGHT
        self.expected_bytes = self.width * self.height * CAMERA_PIXEL_BYTES
        self.frame_active = False
        self.sync_time = 0.0
        self.write_offset = 0
        self.last_display_raw = bytes(self.expected_bytes)
        self.current_raw = bytearray(self.last_display_raw)

    def _resize_buffers(self, width, height):
        self.width = width
        self.height = height
        self.expected_bytes = width * height * CAMERA_PIXEL_BYTES
        if len(self.last_display_raw) != self.expected_bytes:
            self.last_display_raw = bytes(self.expected_bytes)
        self.current_raw = bytearray(self.last_display_raw)

    def _begin_frame(self, width, height, now):
        self._resize_buffers(width, height)
        self.current_raw = bytearray(self.last_display_raw)
        self.frame_active = True
        self.sync_time = now
        self.write_offset = 0

    def _append_payload(self, payload):
        if not payload or self.write_offset >= self.expected_bytes:
            return
        remain = self.expected_bytes - self.write_offset
        valid = payload[:remain]
        end = self.write_offset + len(valid)
        self.current_raw[self.write_offset:end] = valid
        self.write_offset = end

    def _emit_frame(self, complete):
        raw = bytes(self.current_raw)
        self.last_display_raw = raw
        self.frame_active = False
        self.sync_time = 0.0
        self.write_offset = 0
        return self.width, self.height, raw, complete, "camera_rgb565"

    def feed(self, packet):
        now = time.monotonic()
        header_pos = packet.find(FRAME_HEADER)
        output = None
        if header_pos != -1:
            if len(packet) < header_pos + 8:
                return None
            width = (packet[header_pos + 4] << 8) | packet[header_pos + 5]
            height = (packet[header_pos + 6] << 8) | packet[header_pos + 7]
            if width <= 0 or height <= 0:
                return None
            if self.frame_active and self.write_offset > 0:
                output = self._emit_frame(complete=False)
            self._begin_frame(width, height, now)
            payload = packet[header_pos + 8:]
            self._append_payload(payload)
        elif self.frame_active:
            self._append_payload(packet)
            self.sync_time = now
        else:
            return None
        if self.frame_active and self.write_offset >= self.expected_bytes:
            output = self._emit_frame(complete=True)
        return output

    def check_timeout(self):
        if self.frame_active and self.write_offset > 0:
            if (time.monotonic() - self.sync_time) > FRAME_TIMEOUT:
                return self._emit_frame(complete=False)
        return None

class HdmiFrameAssembler:
    def __init__(self):
        self.current = None
        self.last_raw = None
        self.last_shape = None

    def _parse_packet(self, data):
        if len(data) < HDMI_HEADER_LEN or data[0:2] != HDMI_MAGIC:
            return None
        packet_type = data[3]
        if packet_type not in (2, 3):
            return None
        width = _le16(data, 6)
        height = _le16(data, 8)
        payload_len = _le16(data, 12)
        if width <= 0 or height <= 0:
            return None
        if len(data) < HDMI_HEADER_LEN + payload_len:
            return None
        pixel_bytes = 3 if packet_type == 3 else 1
        if payload_len % pixel_bytes != 0:
            return None
        return {
            "frame_id": data[2],
            "row_id": _le16(data, 4),
            "width": width,
            "height": height,
            "x_offset": _le16(data, 10),
            "payload": data[HDMI_HEADER_LEN:HDMI_HEADER_LEN + payload_len],
            "pixel_bytes": pixel_bytes,
        }

    def _start_frame(self, packet):
        width = packet["width"]
        height = packet["height"]
        pixel_bytes = packet["pixel_bytes"]
        row_bytes = width * pixel_bytes
        frame_bytes = row_bytes * height
        if self.last_shape == (width, height, pixel_bytes) and self.last_raw is not None:
            buffer = bytearray(self.last_raw)
        else:
            buffer = bytearray(frame_bytes)
        self.current = {
            "frame_id": packet["frame_id"],
            "width": width,
            "height": height,
            "pixel_bytes": pixel_bytes,
            "row_bytes": row_bytes,
            "buffer": buffer,
            "row_received": [list() for _ in range(height)],
            "complete_rows": set(),
            "start_time": time.monotonic(),
        }

    @staticmethod
    def _row_complete(intervals, row_bytes):
        merged_end = 0
        for start, end in sorted(intervals):
            if start > merged_end:
                return False
            if end > merged_end:
                merged_end = end
            if merged_end >= row_bytes:
                return True
        return merged_end >= row_bytes

    def _finish_frame(self, complete=True):
        current = self.current
        raw = bytes(current["buffer"])
        source = "hdmi_rgb888" if current["pixel_bytes"] == 3 else "hdmi_gray8"
        self.last_raw = raw
        self.last_shape = (current["width"], current["height"], current["pixel_bytes"])
        result = current["width"], current["height"], raw, complete, source
        self.current = None
        return result

    def feed(self, data):
        packet = self._parse_packet(data)
        if packet is None:
            return None
        output = None
        if (
            self.current is None
            or self.current["frame_id"] != packet["frame_id"]
            or self.current["width"] != packet["width"]
            or self.current["height"] != packet["height"]
            or self.current["pixel_bytes"] != packet["pixel_bytes"]
        ):
            if self.current is not None and self.current["complete_rows"]:
                output = self._finish_frame(complete=False)
            self._start_frame(packet)

        current = self.current
        row_id = packet["row_id"]
        if row_id >= current["height"]:
            return None
        pixel_bytes = current["pixel_bytes"]
        row_bytes = current["row_bytes"]
        x_byte_offset = packet["x_offset"] * pixel_bytes
        if x_byte_offset >= row_bytes:
            return None

        end = min(row_bytes, x_byte_offset + len(packet["payload"]))
        valid = packet["payload"][:end - x_byte_offset]
        frame_offset = row_id * row_bytes + x_byte_offset
        current["buffer"][frame_offset:frame_offset + len(valid)] = valid
        current["row_received"][row_id].append((x_byte_offset, end))
        if row_id not in current["complete_rows"]:
            if self._row_complete(current["row_received"][row_id], row_bytes):
                current["complete_rows"].add(row_id)
        if len(current["complete_rows"]) == current["height"]:
            return self._finish_frame()
        return output

    def check_timeout(self):
        if self.current is not None:
            if time.monotonic() - self.current["start_time"] > FRAME_TIMEOUT:
                if self.current["complete_rows"]:
                    return self._finish_frame(complete=False)
                self.current = None
        return None

class UdpDualFrameReceiver(threading.Thread):
    def __init__(self, sock, fpga_ip, fpga_port, frame_queue, stream_mode='hdmi'):
        super().__init__(daemon=True)
        self.sock = sock
        self.fpga_ip = fpga_ip
        self.fpga_port = fpga_port
        self.frame_queue = frame_queue
        self.stream_mode = stream_mode
        self.camera_assembler = CameraFrameAssembler()
        self.hdmi_assembler = HdmiFrameAssembler()
        self.running = threading.Event()
        self.running.set()
        self.send_lock = threading.Lock()
        self.last_packet_time = 0.0
        self.last_start_time = 0.0

    def set_stream_mode(self, mode):
        if mode in ("hdmi", "camera"):
            self.stream_mode = mode

    def send_string(self, cmd: str):
        with self.send_lock:
            self.sock.sendto(cmd.encode("ascii"), (self.fpga_ip, self.fpga_port))

    def send_bytes(self, data: bytes):
        with self.send_lock:
            self.sock.sendto(data, (self.fpga_ip, self.fpga_port))

    def stop(self):
        self.running.clear()
        try:
            self.send_string("0")
        except Exception:
            pass

    def run(self):
        self.last_start_time = time.monotonic()
        while self.running.is_set():
            try:
                packet, addr = self.sock.recvfrom(SOCKET_RECV_SIZE)
            except socket.timeout:
                # 检查超时
                frame_info = self.hdmi_assembler.check_timeout()
                if frame_info is not None:
                    width, height, raw, complete, source = frame_info
                    self._queue_frame(width, height, raw, source)
                frame_info = self.camera_assembler.check_timeout()
                if frame_info is not None:
                    width, height, raw, complete, source = frame_info
                    if complete:
                        self._queue_frame(width, height, raw, source)
                now = time.monotonic()
                if (self.stream_mode == "camera" and
                    now - self.last_packet_time > RESTART_INTERVAL and
                    now - self.last_start_time > RESTART_INTERVAL):
                    try:
                        self.send_string("1")
                        self.last_start_time = now
                        print("Resent camera start command '1'")
                    except Exception:
                        pass
                continue
            except OSError:
                break

            if addr[0] != self.fpga_ip:
                continue
            self.last_packet_time = time.monotonic()

            if packet.startswith(HDMI_MAGIC):
                frame_info = self.hdmi_assembler.feed(packet)
            elif packet.find(FRAME_HEADER) != -1 or self.stream_mode == "camera":
                frame_info = self.camera_assembler.feed(packet)
            else:
                frame_info = None

            if frame_info is not None:
                width, height, raw, complete, source = frame_info
                if complete or source.startswith("hdmi_"):
                    self._queue_frame(width, height, raw, source)

    def _queue_frame(self, width, height, raw, source):
        try:
            if source == "camera_rgb565":
                frame = rgb565_to_bgr(raw, width, height)
            elif source == "hdmi_rgb888":
                frame = rgb888_to_bgr(raw, width, height)
            elif source == "hdmi_gray8":
                frame = gray8_to_bgr(raw, width, height)
            else:
                return
            try:
                self.frame_queue.put(frame, block=False)
            except queue.Full:
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    pass
                self.frame_queue.put(frame, block=False)
        except Exception as e:
            print(f"Frame decode/queue error: {e}")

# ===================== 主应用程序（整合 GUI） ========================
class PlateApp:
    def __init__(self, root):
        self.root = root
        self.root.title("车牌检测与识别系统 (FPGA集成版)")
        self.root.update_idletasks()
        self.root.geometry("1800x800")
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.update()
        print("窗口尺寸:", self.root.winfo_width(), self.root.winfo_height())
        # 模型流水线
        try:
            self.pipeline = PlateDetectRecogPipeline(
                YOLO_ONNX_PATH, LPR_ONNX_PATH,
                conf_thres=CONF_THRES,
                iou_thres=IOU_THRES,
                char_conf_thres=0.5,
                expand_pixels=5
            )
        except Exception as e:
            messagebox.showerror("模型加载失败", str(e))
            self.root.destroy()
            return

        # 状态变量
        self.video_cap = None
        self.video_path = None
        self.is_playing = False
        self.pause = False
        self.video_thread = None

        # FPGA 相关
        self.udp_sock = None
        self.udp_receiver = None

        # 帧队列和处理线程
        self.frame_queue = queue.Queue(maxsize=2)
        self.current_frame = None
        self.result_frame = None
        self.frame_lock = threading.Lock()
        self.result_lock = threading.Lock()

        self.is_recognizing = False
        self.recognize_lock = threading.Lock()
        self.frame_counter = 0

        self.fps_counter = 0
        self.fps_timer = time.time()
        self.current_fps = 0.0

        # GUI 尺寸
        self.display_width = 0
        self.display_height = 0
        self.display_initialized = False

        self.create_widgets()
        self.update_display()

        # 启动帧处理线程
        self.processor_thread = threading.Thread(target=self.frame_processor, daemon=True)
        self.processor_thread.start()

    def create_widgets(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TButton', font=('微软雅黑', 10), padding=6)
        style.configure('TLabel', font=('微软雅黑', 10))

        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True)

        # ---------- 左侧面板 ----------
        left_panel = tk.Frame(main_paned, relief=tk.RAISED, padx=5, pady=5, width=200, bg='#f0f0f0')
        left_panel.pack_propagate(False)   # 固定宽度不随内容伸缩
        main_paned.add(left_panel, weight=0)

        # FPGA 控制按钮组
        fpga_btn_frame = ttk.LabelFrame(left_panel, text="FPGA控制", padding=5)
        fpga_btn_frame.pack(side=tk.TOP, fill=tk.X, pady=2)

        self.btn_fpga = ttk.Button(fpga_btn_frame, text="FPGA视频流", command=self.start_fpga_stream, width=14)
        self.btn_fpga.pack(pady=2)

        self.btn_cam = ttk.Button(fpga_btn_frame, text="摄像头推流", command=self.send_fpga_cam, width=14, state=tk.DISABLED)
        self.btn_cam.pack(pady=2)

        self.btn_hdmi = ttk.Button(fpga_btn_frame, text="HDMI推流", command=self.send_fpga_hdmi, width=14, state=tk.DISABLED)
        self.btn_hdmi.pack(pady=2)

        self.btn_stop = ttk.Button(fpga_btn_frame, text="全部停止", command=self.send_fpga_stop, width=14, state=tk.DISABLED)
        self.btn_stop.pack(pady=2)

        # 本地文件控制
        local_btn_frame = ttk.LabelFrame(left_panel, text="本地输入", padding=5)
        local_btn_frame.pack(side=tk.TOP, fill=tk.X, pady=5)

        self.btn_image = ttk.Button(local_btn_frame, text="选择图片", command=self.load_image, width=14)
        self.btn_image.pack(pady=2)

        self.btn_video = ttk.Button(local_btn_frame, text="选择视频", command=self.load_video, width=14)
        self.btn_video.pack(pady=2)

        self.start_btn = ttk.Button(local_btn_frame, text="开始", command=self.start_video, state=tk.DISABLED, width=14)
        self.start_btn.pack(pady=2)

        self.pause_btn = ttk.Button(local_btn_frame, text="暂停", command=self.pause_video, state=tk.DISABLED, width=14)
        self.pause_btn.pack(pady=2)

        self.btn_stop_local = ttk.Button(local_btn_frame, text="停止", command=self.stop_video, width=14)
        self.btn_stop_local.pack(pady=2)

        # 识别一次
        self.btn_detect_once = ttk.Button(left_panel, text="识别一次", command=self.recognize_once, width=14)
        self.btn_detect_once.pack(pady=5)

        # 裁剪车牌显示
        crop_frame = ttk.LabelFrame(left_panel, text="裁剪车牌", padding=5)
        crop_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.img_crop_label = ttk.Label(crop_frame, background="gray", anchor=tk.CENTER)
        self.img_crop_label.pack(fill=tk.BOTH, expand=True)

        # 状态信息
        info_frame = ttk.LabelFrame(left_panel, text="识别信息", padding=5)
        info_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.info_text = tk.Text(info_frame, height=8, state=tk.DISABLED, wrap=tk.WORD,
                                 font=('微软雅黑', 10), relief=tk.FLAT, bg='#f0f0f0')
        self.info_text.pack(fill=tk.BOTH, expand=True)

        # ---------- 右侧图像显示 ----------
        images_paned = ttk.PanedWindow(main_paned, orient=tk.HORIZONTAL)
        main_paned.add(images_paned, weight=1)

        left_img_frame = ttk.LabelFrame(images_paned, text="原始画面", padding=5)
        images_paned.add(left_img_frame, weight=1)
        self.original_label = ttk.Label(left_img_frame, background="black")
        self.original_label.pack(fill=tk.BOTH, expand=True)

        right_img_frame = ttk.LabelFrame(images_paned, text="识别结果", padding=5)
        images_paned.add(right_img_frame, weight=1)
        self.result_label = ttk.Label(right_img_frame, background="black")
        self.result_label.pack(fill=tk.BOTH, expand=True)

    # ---------- 帧处理线程 ----------
    def frame_processor(self):
        while True:
            try:
                frame = self.frame_queue.get(timeout=1)
                if frame is None:
                    continue
                with self.frame_lock:
                    self.current_frame = frame
                self.async_recognize(frame, is_stream=True)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"帧处理器异常: {e}")

    def async_recognize(self, frame_bgr, is_stream=False):
        self.frame_counter += 1
        if self.frame_counter % 3 != 0:  # 简单跳帧，可调
            return
        with self.recognize_lock:
            if self.is_recognizing:
                return
            self.is_recognizing = True

        def recognize_task():
            try:
                start_time = time.time()
                original_img, result_img, results = self.pipeline.process_frame(frame_bgr)
                elapsed = (time.time() - start_time) * 1000

                fps = 0.0
                if is_stream:
                    self.fps_counter += 1
                    now = time.time()
                    if now - self.fps_timer >= 1.0:
                        self.current_fps = self.fps_counter / (now - self.fps_timer)
                        self.fps_counter = 0
                        self.fps_timer = now
                    fps = self.current_fps

                with self.result_lock:
                    self.result_frame = result_img

                # 更新信息
                if results:
                    res = results[0]
                    info = f"车牌: {res.plate_str}  类型: {res.type_name}\n"
                    info += f"定位置信度: {res.yolo_conf:.4f}\n"
                    info += f"类型置信度: {res.type_conf:.4f}\n"
                    info += f"字符平均置信度: {res.char_conf:.4f}\n"
                    info += f"耗时: {elapsed:.0f} ms"
                    if fps > 0:
                        info += f"  帧率: {fps:.1f} FPS"
                    # 更新裁剪图
                    self.root.after(0, lambda: self.display_image(res.rect_img, self.img_crop_label, fixed_size=(160,60)))
                else:
                    info = "未检测到车牌"
                    self.root.after(0, lambda: self.display_image(None, self.img_crop_label))

                self.root.after(0, lambda: self.update_info(info))
            except Exception as e:
                print(f"识别出错: {e}")
                self.root.after(0, lambda: self.update_info("识别错误"))
            finally:
                with self.recognize_lock:
                    self.is_recognizing = False

        threading.Thread(target=recognize_task, daemon=True).start()

    # ---------- 显示更新循环 ----------
    def update_display(self):
        if not self.display_initialized:
            # 获取标签尺寸
            w = self.original_label.winfo_width()
            h = self.original_label.winfo_height()
            if w > 1 and h > 1:
                self.display_width = w
                self.display_height = h
                self.display_initialized = True
            else:
                self.root.after(30, self.update_display)
                return

        with self.frame_lock:
            if self.current_frame is not None:
                canvas = self.resize_and_center(self.current_frame, self.display_width, self.display_height)
                if canvas is not None:
                    self._show_on_label(canvas, self.original_label)

        with self.result_lock:
            if self.result_frame is not None:
                canvas = self.resize_and_center(self.result_frame, self.display_width, self.display_height)
                if canvas is not None:
                    self._show_on_label(canvas, self.result_label)

        self.root.after(30, self.update_display)

    def resize_and_center(self, img, target_w, target_h):
        if img is None or target_w <= 0 or target_h <= 0:
            return None
        h, w = img.shape[:2]
        scale = min(target_w / w, target_h / h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        x_offset = (target_w - new_w) // 2
        y_offset = (target_h - new_h) // 2
        canvas[y_offset:y_offset+new_h, x_offset:x_offset+new_w] = resized
        return canvas

    def _show_on_label(self, img_bgr, label):
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        tk_img = ImageTk.PhotoImage(pil_img)
        label.config(image=tk_img)
        label.image = tk_img

    def display_image(self, img_bgr, label, fixed_size=None):
        """通用显示，支持固定尺寸"""
        if img_bgr is None:
            label.config(image='')
            label.image = None
            return
        h, w = img_bgr.shape[:2]
        if fixed_size:
            max_w, max_h = fixed_size
            scale = min(max_w / w, max_h / h, 1.0)
        else:
            scale = 1.0
        if scale < 1.0:
            new_w = int(w * scale)
            new_h = int(h * scale)
            img = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        else:
            img = img_bgr
        self._show_on_label(img, label)

    def update_info(self, text):
        self.info_text.config(state=tk.NORMAL)
        self.info_text.delete(1.0, tk.END)
        self.info_text.insert(tk.END, text)
        self.info_text.config(state=tk.DISABLED)

    # ---------- FPGA 控制 ----------
    def start_fpga_stream(self):
        self.close_source()
        self.reset_display_size()
        if self.udp_sock is None:
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_RCVBUF)
            self.udp_sock.bind((LOCAL_IP, LOCAL_PORT))
            self.udp_sock.settimeout(SOCKET_TIMEOUT)

        self.udp_receiver = UdpDualFrameReceiver(self.udp_sock, FPGA_IP, FPGA_PORT, self.frame_queue, stream_mode='hdmi')
        self.udp_receiver.start()
        self.input_source = 'fpga'

        # 启用相关按钮
        self.btn_cam.config(state=tk.NORMAL)
        self.btn_hdmi.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.NORMAL)

        self.update_info("FPGA 视频流已启动")

    def send_fpga_cmd(self, cmd_str):
        if self.udp_receiver is None:
            messagebox.showwarning("提示", "FPGA 流未启动")
            return
        try:
            self.udp_receiver.send_string(cmd_str)
            self.update_info(f"发送命令: {cmd_str}")
        except Exception as e:
            messagebox.showerror("发送失败", str(e))

    def send_fpga_cam(self):
        if self.udp_receiver is not None:
            self.udp_receiver.set_stream_mode("camera")
        self.send_fpga_cmd("1")

    def send_fpga_hdmi(self):
        if self.udp_receiver is not None:
            self.udp_receiver.set_stream_mode("hdmi")
        self.send_fpga_cmd("0")

    def send_fpga_stop(self):
        if self.udp_receiver is not None:
            self.udp_receiver.set_stream_mode("hdmi")
        self.send_fpga_cmd("0")

    # ---------- 本地图片 / 视频 ----------
    def load_image(self):
        self.close_source()
        self.reset_display_size()
        file_path = filedialog.askopenfilename(filetypes=[("Image files", "*.jpg *.jpeg *.png *.bmp")])
        if not file_path:
            return
        img = imread_unicode(file_path)
        if img is None:
            messagebox.showerror("错误", "无法读取图片")
            return
        self.frame_queue.put(img)  # 直接放入队列处理
        self.input_source = 'image'

    def load_video(self):
        self.close_source()
        self.reset_display_size()
        file_path = filedialog.askopenfilename(filetypes=[("Video files", "*.mp4 *.avi *.mov *.mkv")])
        if not file_path:
            return
        self.video_path = file_path
        self.video_cap = cv2.VideoCapture(file_path)
        if not self.video_cap.isOpened():
            messagebox.showerror("错误", "无法打开视频")
            self.video_cap = None
            return
        self.input_source = 'video'
        self.is_playing = True
        self.pause = False
        self.start_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.NORMAL, text="暂停")
        if self.video_thread is None or not self.video_thread.is_alive():
            self.video_thread = threading.Thread(target=self.video_loop, daemon=True)
            self.video_thread.start()

    def start_video(self):
        if self.video_cap is None:
            return
        self.is_playing = True
        self.pause = False
        self.start_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.NORMAL, text="暂停")

    def pause_video(self):
        if self.is_playing:
            self.pause = not self.pause
            self.pause_btn.config(text="继续" if self.pause else "暂停")

    def stop_video(self):
        self.is_playing = False
        self.pause = False
        if self.video_cap:
            self.video_cap.release()
            self.video_cap = None
        self.start_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.DISABLED, text="暂停")
        self.video_thread = None
        self.input_source = None

    def video_loop(self):
        while self.input_source == 'video' and self.video_cap and self.video_cap.isOpened():
            if self.pause:
                time.sleep(0.05)
                continue
            ret, frame = self.video_cap.read()
            if not ret:
                # 循环播放
                self.video_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            self.frame_queue.put(frame)
            fps = self.video_cap.get(cv2.CAP_PROP_FPS)
            delay = 1.0 / fps if fps > 0 else 0.033
            time.sleep(delay)
        if self.video_cap:
            self.video_cap.release()
            self.video_cap = None
        if self.input_source == 'video':
            self.input_source = None

    def recognize_once(self):
        with self.frame_lock:
            if self.current_frame is None:
                self.update_info("无图像，请先加载图像或视频")
                return
            frame = self.current_frame.copy()
        # 直接放入队列处理（会进行识别）
        self.frame_queue.put(frame)

    # ---------- 辅助 ----------
    def reset_display_size(self):
        self.display_initialized = False
        self.display_width = 0
        self.display_height = 0

    def close_source(self):
        self.stop_video()
        if self.udp_receiver is not None:
            self.udp_receiver.stop()
            self.udp_receiver.join(timeout=1)
            self.udp_receiver = None
        if self.udp_sock is not None:
            self.udp_sock.close()
            self.udp_sock = None
        self.input_source = None
        self.pause = False
        with self.frame_lock:
            self.current_frame = None
        with self.result_lock:
            self.result_frame = None
        # 禁用 FPGA 按钮
        self.btn_cam.config(state=tk.DISABLED)
        self.btn_hdmi.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.DISABLED)
        self.start_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.DISABLED, text="暂停")
        self.update_info("已关闭所有输入")

    def on_close(self):
        self.close_source()
        self.root.destroy()

# ===================== 入口 ========================
if __name__ == "__main__":
    # 检查模型文件
    if not os.path.exists(YOLO_ONNX_PATH):
        print(f"错误: YOLO 模型文件不存在: {YOLO_ONNX_PATH}")
        sys.exit(1)
    if not os.path.exists(LPR_ONNX_PATH):
        print(f"错误: LPR 模型文件不存在: {LPR_ONNX_PATH}")
        sys.exit(1)

    root = tk.Tk()
    app = PlateApp(root)
    root.mainloop()