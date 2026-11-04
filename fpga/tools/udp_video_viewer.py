import socket
import time
from pathlib import Path


UDP_IP = "0.0.0.0"
UDP_PORT = 1234
RECV_BUFFER_BYTES = 16 * 1024 * 1024
FRAME_TIMEOUT_S = 0.5
WINDOW_NAME = "FPGA UDP Video"
SAVE_DIR = Path("udp_video_captures")
DISPLAY_SCALE = 1
ENHANCE_LOW_LIGHT_DEFAULT = False
GAMMA_DEFAULT = 0.65
CLAHE_CLIP_LIMIT = 2.5
CLAHE_TILE_GRID_SIZE = (8, 8)
SHARPEN_DEFAULT = 0.25


def parse_packet(data):
    if len(data) >= 14 and data[0:2] == b"IM" and data[3] == 3:
        frame_id = data[2]
        row_id = data[4] | (data[5] << 8)
        width = data[6] | (data[7] << 8)
        height = data[8] | (data[9] << 8)
        x_offset = data[10] | (data[11] << 8)
        payload_len = data[12] | (data[13] << 8)
        payload = data[14:14 + payload_len]
        return frame_id, row_id, width, height, x_offset, payload, 3

    if len(data) >= 14 and data[0:2] == b"IM" and data[3] == 2:
        frame_id = data[2]
        row_id = data[4] | (data[5] << 8)
        width = data[6] | (data[7] << 8)
        height = data[8] | (data[9] << 8)
        x_offset = data[10] | (data[11] << 8)
        payload_len = data[12] | (data[13] << 8)
        payload = data[14:14 + payload_len]
        return frame_id, row_id, width, height, x_offset, payload, 2

    if len(data) >= 10 and data[0:2] == b"IM" and data[3] == 1:
        frame_id = data[2]
        row_id = data[4] | (data[5] << 8)
        width = data[6] | (data[7] << 8)
        height = data[8] | (data[9] << 8)
        payload = data[10:]
        return frame_id, row_id, width, height, 0, payload, 1

    if len(data) >= 8 and data[0:2] == b"IM":
        frame_id = data[2]
        row_id = data[3]
        width = data[4] | (data[5] << 8)
        height = data[6] | (data[7] << 8)
        payload = data[8:]
        return frame_id, row_id, width, height, 0, payload, 0

    return None


def row_width_bytes(width, pixel_bytes):
    return width * pixel_bytes


def is_row_complete(ranges, width_bytes):
    merged_end = 0
    for start, end in sorted(ranges):
        if start > merged_end:
            return False
        if end > merged_end:
            merged_end = end
        if merged_end >= width_bytes:
            return True
    return False


def complete_row_count(row_received, width_bytes, height):
    count = 0
    for row in range(height):
        ranges = row_received.get(row)
        if ranges is not None and is_row_complete(ranges, width_bytes):
            count += 1
    return count


def main():
    try:
        import cv2
        import numpy as np
    except ImportError:
        print("This viewer needs opencv-python and numpy.")
        print("Install with: pip install opencv-python numpy")
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RECV_BUFFER_BYTES)
    sock.bind((UDP_IP, UDP_PORT))
    sock.settimeout(0.2)
    actual_recv_buffer = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)

    current_frame = None
    width = 0
    height = 0
    pixel_bytes = 1
    rows = {}
    row_received = {}
    complete_rows = set()
    last_raw_rows = None
    enhance_low_light = ENHANCE_LOW_LIGHT_DEFAULT
    gamma_value = GAMMA_DEFAULT
    sharpen_amount = SHARPEN_DEFAULT
    clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP_LIMIT, tileGridSize=CLAHE_TILE_GRID_SIZE)
    frame_start = time.time()
    shown_count = 0
    last_stat_time = time.time()
    last_shown_count = 0
    window_width = 640 * DISPLAY_SCALE
    window_height = 480 * DISPLAY_SCALE

    def build_frame(raw_rows, frame_width, frame_height, frame_pixel_bytes):
        if frame_pixel_bytes == 3:
            frame_rgb = np.empty((frame_height, frame_width, 3), dtype=np.uint8)
            for row in range(frame_height):
                frame_rgb[row, :, :] = np.frombuffer(raw_rows[row], dtype=np.uint8).reshape(frame_width, 3)
            return cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

        frame = np.empty((frame_height, frame_width), dtype=np.uint8)
        for row in range(frame_height):
            frame[row, :] = np.frombuffer(raw_rows[row], dtype=np.uint8)
        return frame

    def enhance_frame(frame):
        if not enhance_low_light:
            return frame

        inv_gamma = 1.0 / gamma_value
        gamma_lut = np.array([((i / 255.0) ** inv_gamma) * 255 for i in range(256)], dtype=np.uint8)

        if frame.ndim == 2:
            enhanced = clahe.apply(frame)
            enhanced = cv2.LUT(enhanced, gamma_lut)
        else:
            lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
            lab[:, :, 0] = clahe.apply(lab[:, :, 0])
            enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
            enhanced = cv2.LUT(enhanced, gamma_lut)

        if sharpen_amount > 0:
            blurred = cv2.GaussianBlur(enhanced, (0, 0), 1.0)
            enhanced = cv2.addWeighted(enhanced, 1.0 + sharpen_amount, blurred, -sharpen_amount, 0)

        return enhanced

    def show_current_frame(partial):
        nonlocal shown_count
        nonlocal last_stat_time
        nonlocal last_shown_count
        nonlocal last_raw_rows
        nonlocal enhance_low_light
        nonlocal gamma_value
        nonlocal sharpen_amount

        if current_frame is None or width <= 0 or height <= 0:
            return False
        if partial and not complete_rows and last_raw_rows is None:
            return False

        row_bytes = row_width_bytes(width, pixel_bytes)
        raw_rows = []
        zero_row = bytes(row_bytes)

        for row in range(height):
            row_data = rows.get(row)
            if row_data is None:
                if last_raw_rows is not None and row < len(last_raw_rows) and len(last_raw_rows[row]) == row_bytes:
                    row_data = last_raw_rows[row]
                else:
                    row_data = zero_row
            raw_rows.append(bytearray(row_data))

        frame = build_frame(raw_rows, width, height, pixel_bytes)
        display_frame = enhance_frame(frame)

        if DISPLAY_SCALE > 1:
            view = cv2.resize(
                display_frame,
                (width * DISPLAY_SCALE, height * DISPLAY_SCALE),
                interpolation=cv2.INTER_NEAREST,
            )
        else:
            view = display_frame

        cv2.imshow(WINDOW_NAME, view)
        shown_count += 1
        last_raw_rows = raw_rows

        now = time.time()
        if now - last_stat_time >= 1.0:
            fps = (shown_count - last_shown_count) / (now - last_stat_time)
            mode_name = "RGB" if pixel_bytes == 3 else "GRAY"
            partial_mark = " partial" if partial and len(complete_rows) < height else ""
            enhance_mark = f", enhance on gamma {gamma_value:.2f}" if enhance_low_light else ", enhance off"
            print(f"Display {width}x{height} {mode_name}, fps {fps:.1f}, rows {len(complete_rows)}/{height}{partial_mark}{enhance_mark}")
            last_stat_time = now
            last_shown_count = shown_count

        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord("q")):
            return None
        if key == ord("e"):
            enhance_low_light = not enhance_low_light
            print(f"Low-light enhancement {'on' if enhance_low_light else 'off'}")
        elif key == ord("["):
            gamma_value = max(0.35, gamma_value - 0.05)
            print(f"Gamma {gamma_value:.2f}")
        elif key == ord("]"):
            gamma_value = min(1.20, gamma_value + 0.05)
            print(f"Gamma {gamma_value:.2f}")
        elif key == ord("-"):
            sharpen_amount = max(0.0, sharpen_amount - 0.05)
            print(f"Sharpen {sharpen_amount:.2f}")
        elif key in (ord("="), ord("+")):
            sharpen_amount = min(0.8, sharpen_amount + 0.05)
            print(f"Sharpen {sharpen_amount:.2f}")
        if key == ord("s"):
            SAVE_DIR.mkdir(exist_ok=True)
            out_path = SAVE_DIR / f"video_frame_{shown_count:04d}_id_{current_frame:03d}.png"
            cv2.imwrite(str(out_path), display_frame)
            print(f"Saved {out_path}")

        return True

    def reset_frame():
        nonlocal current_frame
        nonlocal rows
        nonlocal row_received
        nonlocal complete_rows

        current_frame = None
        rows = {}
        row_received = {}
        complete_rows = set()

    print(f"Listening UDP {UDP_PORT} ...")
    print(f"UDP receive buffer: {actual_recv_buffer} bytes")
    print("Press KEY4 on FPGA to start/stop stream. Press e to toggle enhancement, [/] gamma, -/= sharpen, s to save, q or Esc to quit.")
    print("If you see Saved udp_frames\\frame_xxxx.bmp, you are running udp_image_receiver.py, not this viewer.")

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, 640 * DISPLAY_SCALE, 480 * DISPLAY_SCALE)

    while True:
        try:
            data, _addr = sock.recvfrom(2048)
        except socket.timeout:
            if current_frame is not None and time.time() - frame_start > FRAME_TIMEOUT_S:
                show_result = show_current_frame(partial=True)
                if show_result is None:
                    break
                reset_frame()

            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                break
            continue

        parsed = parse_packet(data)
        if parsed is None:
            continue

        frame_id, row_id, pkt_width, pkt_height, x_offset, payload, packet_version = parsed
        pkt_pixel_bytes = 3 if packet_version == 3 else 1
        pkt_row_bytes = row_width_bytes(pkt_width, pkt_pixel_bytes)
        x_byte_offset = x_offset * pkt_pixel_bytes

        if row_id >= pkt_height:
            continue
        if x_offset >= pkt_width or x_byte_offset + len(payload) > pkt_row_bytes:
            continue

        if current_frame != frame_id:
            if current_frame is not None:
                show_result = show_current_frame(partial=True)
                if show_result is None:
                    break
            current_frame = frame_id
            width = pkt_width
            height = pkt_height
            pixel_bytes = pkt_pixel_bytes
            new_window_width = width * DISPLAY_SCALE
            new_window_height = height * DISPLAY_SCALE
            if new_window_width != window_width or new_window_height != window_height:
                cv2.resizeWindow(WINDOW_NAME, new_window_width, new_window_height)
                window_width = new_window_width
                window_height = new_window_height
            rows = {}
            row_received = {}
            complete_rows = set()
            frame_start = time.time()

        if pkt_width != width or pkt_height != height or pkt_pixel_bytes != pixel_bytes:
            continue

        if row_id not in rows:
            rows[row_id] = bytearray(row_width_bytes(width, pixel_bytes))
            row_received[row_id] = set()

        rows[row_id][x_byte_offset:x_byte_offset + len(payload)] = payload
        row_received[row_id].add((x_byte_offset, x_byte_offset + len(payload)))
        if row_id not in complete_rows and is_row_complete(row_received[row_id], row_width_bytes(width, pixel_bytes)):
            complete_rows.add(row_id)

        if len(complete_rows) == height:
            show_result = show_current_frame(partial=False)
            if show_result is None:
                break
            reset_frame()

    sock.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
