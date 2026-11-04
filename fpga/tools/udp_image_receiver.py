import socket
import time
from pathlib import Path


UDP_IP = "0.0.0.0"
UDP_PORT = 1234
OUT_DIR = Path("udp_frames")
FRAME_TIMEOUT_S = 2.0
RECV_BUFFER_BYTES = 16 * 1024 * 1024
SAVE_PGM_COPY = False


def is_row_complete(ranges, width):
    merged_end = 0
    for start, end in sorted(ranges):
        if start > merged_end:
            break
        if end > merged_end:
            merged_end = end
        if merged_end >= width:
            return True
    return False


def get_frame_status(row_received, width, height):
    complete_rows = 0
    missing_rows = []
    max_row = max(row_received.keys()) if row_received else -1

    for row in range(height):
        ranges = row_received.get(row)
        if ranges is not None and is_row_complete(ranges, width):
            complete_rows += 1
        else:
            missing_rows.append(row)

    return complete_rows, max_row, missing_rows


def save_pgm(path, width, height, rows):
    with path.open("wb") as f:
        f.write(f"P5\n{width} {height}\n255\n".encode("ascii"))
        for row in rows:
            f.write(row)


def save_bmp(path, width, height, rows):
    row_stride = (width + 3) & ~3
    pixel_size = row_stride * height
    palette_size = 256 * 4
    file_header_size = 14
    dib_header_size = 40
    pixel_offset = file_header_size + dib_header_size + palette_size
    file_size = pixel_offset + pixel_size

    with path.open("wb") as f:
        f.write(b"BM")
        f.write(file_size.to_bytes(4, "little"))
        f.write((0).to_bytes(2, "little"))
        f.write((0).to_bytes(2, "little"))
        f.write(pixel_offset.to_bytes(4, "little"))

        f.write(dib_header_size.to_bytes(4, "little"))
        f.write(width.to_bytes(4, "little", signed=True))
        f.write(height.to_bytes(4, "little", signed=True))
        f.write((1).to_bytes(2, "little"))
        f.write((8).to_bytes(2, "little"))
        f.write((0).to_bytes(4, "little"))
        f.write(pixel_size.to_bytes(4, "little"))
        f.write((2835).to_bytes(4, "little", signed=True))
        f.write((2835).to_bytes(4, "little", signed=True))
        f.write((256).to_bytes(4, "little"))
        f.write((256).to_bytes(4, "little"))

        for value in range(256):
            f.write(bytes((value, value, value, 0)))

        pad = bytes(row_stride - width)
        for row in reversed(rows):
            f.write(row)
            f.write(pad)


def parse_packet(data):
    if len(data) >= 14 and data[0:2] == b"IM" and data[3] == 2:
        frame_id = data[2]
        row_id = data[4] | (data[5] << 8)
        width = data[6] | (data[7] << 8)
        height = data[8] | (data[9] << 8)
        x_offset = data[10] | (data[11] << 8)
        payload_len = data[12] | (data[13] << 8)
        payload = data[14:14 + payload_len]
        return frame_id, row_id, width, height, x_offset, payload

    if len(data) >= 10 and data[0:2] == b"IM" and data[3] == 1:
        frame_id = data[2]
        row_id = data[4] | (data[5] << 8)
        width = data[6] | (data[7] << 8)
        height = data[8] | (data[9] << 8)
        payload = data[10:]
        return frame_id, row_id, width, height, 0, payload

    if len(data) >= 8 and data[0:2] == b"IM":
        frame_id = data[2]
        row_id = data[3]
        width = data[4] | (data[5] << 8)
        height = data[6] | (data[7] << 8)
        payload = data[8:]
        return frame_id, row_id, width, height, 0, payload

    return None


def main():
    OUT_DIR.mkdir(exist_ok=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RECV_BUFFER_BYTES)
    sock.bind((UDP_IP, UDP_PORT))
    sock.settimeout(0.5)
    actual_recv_buffer = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)

    current_frame = None
    width = 0
    height = 0
    rows = {}
    row_received = {}
    frame_start = time.time()
    saved_count = 0

    print(f"Listening UDP {UDP_PORT} ...")
    print(f"UDP receive buffer: {actual_recv_buffer} bytes")

    while True:
        try:
            data, addr = sock.recvfrom(2048)
        except socket.timeout:
            if current_frame is not None and rows and time.time() - frame_start > FRAME_TIMEOUT_S:
                complete_rows, max_row, missing_rows = get_frame_status(row_received, width, height)
                missing_preview = ",".join(str(row) for row in missing_rows[:16])
                if len(missing_rows) > 16:
                    missing_preview += ",..."
                print(
                    f"Drop incomplete frame {current_frame}: "
                    f"{complete_rows}/{height} complete rows, "
                    f"{len(rows)} rows touched, max row {max_row}, "
                    f"missing {missing_preview}"
                )
                current_frame = None
                rows = {}
                row_received = {}
            continue

        parsed = parse_packet(data)
        if parsed is None:
            continue

        frame_id, row_id, pkt_width, pkt_height, x_offset, payload = parsed

        if row_id >= pkt_height:
            continue
        if x_offset >= pkt_width or x_offset + len(payload) > pkt_width:
            continue

        if current_frame != frame_id:
            if current_frame is not None and rows:
                complete_rows, max_row, missing_rows = get_frame_status(row_received, width, height)
                missing_preview = ",".join(str(row) for row in missing_rows[:16])
                if len(missing_rows) > 16:
                    missing_preview += ",..."
                print(
                    f"Drop incomplete frame {current_frame} before new frame {frame_id}: "
                    f"{complete_rows}/{height} complete rows, "
                    f"{len(rows)} rows touched, max row {max_row}, "
                    f"missing {missing_preview}"
                )
            current_frame = frame_id
            width = pkt_width
            height = pkt_height
            rows = {}
            row_received = {}
            frame_start = time.time()
            print(f"Start frame {frame_id}: {width}x{height} from {addr[0]}")

        if pkt_width != width or pkt_height != height:
            continue

        if row_id not in rows:
            rows[row_id] = bytearray(width)
            row_received[row_id] = set()

        rows[row_id][x_offset:x_offset + len(payload)] = payload
        row_received[row_id].add((x_offset, x_offset + len(payload)))

        complete_rows, _, _ = get_frame_status(row_received, width, height)

        if complete_rows == height:
            image_rows = [bytes(rows.get(y, bytes([0]) * width)) for y in range(height)]
            out_path = OUT_DIR / f"frame_{saved_count:04d}_id_{frame_id:03d}.bmp"
            save_bmp(out_path, width, height, image_rows)
            if SAVE_PGM_COPY:
                save_pgm(out_path.with_suffix(".pgm"), width, height, image_rows)
            print(f"Saved {out_path} ({width}x{height})")

            saved_count += 1
            current_frame = None
            rows = {}
            row_received = {}


if __name__ == "__main__":
    main()
