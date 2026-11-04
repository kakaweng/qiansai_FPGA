import argparse
import socket
import time
from pathlib import Path


MAGIC = b"IM"
DEFAULT_BIND_IP = "0.0.0.0"
DEFAULT_PORT = 1234
DEFAULT_RECV_BUFFER = 16 * 1024 * 1024
DEFAULT_FRAME_TIMEOUT_S = 1.0


def parse_args():
    parser = argparse.ArgumentParser(
        description="HDMI UDP stream probe for Pango FPGA. "
                    "Receives UDP image packets, validates them, prints stats, "
                    "and saves complete frames as BMP."
    )
    parser.add_argument("--bind-ip", default=DEFAULT_BIND_IP, help="Local bind IP, default: 0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="UDP listen port, default: 1234")
    parser.add_argument(
        "--out-dir",
        default="udp_probe_output",
        help="Directory for saved frames, default: udp_probe_output",
    )
    parser.add_argument(
        "--save-count",
        type=int,
        default=3,
        help="How many numbered complete frames to save, default: 3. latest_frame.bmp is always updated.",
    )
    parser.add_argument(
        "--exit-after-complete",
        type=int,
        default=1,
        help="Exit after receiving this many complete frames, default: 1. Use 0 to keep running.",
    )
    parser.add_argument(
        "--frame-timeout",
        type=float,
        default=DEFAULT_FRAME_TIMEOUT_S,
        help="Seconds before an incomplete frame is dropped, default: 1.0",
    )
    return parser.parse_args()


def le16(data, offset):
    return data[offset] | (data[offset + 1] << 8)


def parse_packet(data):
    if len(data) >= 14 and data[0:2] == MAGIC and data[3] in (2, 3):
        frame_id = data[2]
        packet_type = data[3]
        row_id = le16(data, 4)
        width = le16(data, 6)
        height = le16(data, 8)
        x_offset = le16(data, 10)
        payload_len = le16(data, 12)
        payload = data[14:14 + payload_len]
        if len(payload) != payload_len:
            return None
        pixel_bytes = 3 if packet_type == 3 else 1
        return {
            "frame_id": frame_id,
            "packet_type": packet_type,
            "row_id": row_id,
            "width": width,
            "height": height,
            "x_offset": x_offset,
            "payload": payload,
            "pixel_bytes": pixel_bytes,
        }

    if len(data) >= 10 and data[0:2] == MAGIC and data[3] == 1:
        frame_id = data[2]
        row_id = le16(data, 4)
        width = le16(data, 6)
        height = le16(data, 8)
        payload = data[10:]
        return {
            "frame_id": frame_id,
            "packet_type": 1,
            "row_id": row_id,
            "width": width,
            "height": height,
            "x_offset": 0,
            "payload": payload,
            "pixel_bytes": 1,
        }

    return None


def is_row_complete(ranges, row_bytes):
    merged_end = 0
    for start, end in sorted(ranges):
        if start > merged_end:
            return False
        if end > merged_end:
            merged_end = end
        if merged_end >= row_bytes:
            return True
    return False


def build_missing_rows(row_received, row_bytes, height):
    missing = []
    for row in range(height):
        ranges = row_received.get(row)
        if ranges is None or not is_row_complete(ranges, row_bytes):
            missing.append(row)
    return missing


def save_bmp(path, width, height, rows, pixel_bytes):
    if pixel_bytes == 1:
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
        return

    row_stride = ((width * 3) + 3) & ~3
    pixel_size = row_stride * height
    file_header_size = 14
    dib_header_size = 40
    pixel_offset = file_header_size + dib_header_size
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
        f.write((24).to_bytes(2, "little"))
        f.write((0).to_bytes(4, "little"))
        f.write(pixel_size.to_bytes(4, "little"))
        f.write((2835).to_bytes(4, "little", signed=True))
        f.write((2835).to_bytes(4, "little", signed=True))
        f.write((0).to_bytes(4, "little"))
        f.write((0).to_bytes(4, "little"))

        pad = bytes(row_stride - width * 3)
        for row in reversed(rows):
            bgr = bytearray()
            for index in range(0, len(row), 3):
                r = row[index]
                g = row[index + 1]
                b = row[index + 2]
                bgr.extend((b, g, r))
            f.write(bgr)
            f.write(pad)


class FrameCollector:
    def __init__(self, out_dir, save_count):
        self.out_dir = out_dir
        self.save_count = save_count
        self.complete_frames = 0
        self.dropped_frames = 0
        self.total_packets = 0
        self.current = None
        self.last_source = None

    def reset_current(self):
        self.current = None

    def start_frame(self, packet, source_ip):
        row_bytes = packet["width"] * packet["pixel_bytes"]
        self.current = {
            "frame_id": packet["frame_id"],
            "packet_type": packet["packet_type"],
            "width": packet["width"],
            "height": packet["height"],
            "pixel_bytes": packet["pixel_bytes"],
            "row_bytes": row_bytes,
            "rows": {},
            "row_received": {},
            "complete_rows": set(),
            "packets": 0,
            "start_time": time.time(),
            "source_ip": source_ip,
        }
        self.last_source = source_ip

    def finalize_incomplete(self, reason):
        if self.current is None:
            return

        missing = build_missing_rows(
            self.current["row_received"],
            self.current["row_bytes"],
            self.current["height"],
        )
        preview = ",".join(str(row) for row in missing[:12])
        if len(missing) > 12:
            preview += ",..."

        self.dropped_frames += 1
        print(
            f"[DROP] frame_id={self.current['frame_id']} reason={reason} "
            f"rows={len(self.current['complete_rows'])}/{self.current['height']} "
            f"packets={self.current['packets']} missing={preview}"
        )
        self.reset_current()

    def accept_packet(self, packet, source_ip):
        self.total_packets += 1

        if self.current is None:
            self.start_frame(packet, source_ip)
        elif self.current["frame_id"] != packet["frame_id"]:
            self.finalize_incomplete(f"new_frame_{packet['frame_id']}")
            self.start_frame(packet, source_ip)

        current = self.current
        if current is None:
            return None

        if (
            packet["width"] != current["width"]
            or packet["height"] != current["height"]
            or packet["pixel_bytes"] != current["pixel_bytes"]
        ):
            return None

        row_id = packet["row_id"]
        width = packet["width"]
        pixel_bytes = packet["pixel_bytes"]
        row_bytes = current["row_bytes"]
        x_byte_offset = packet["x_offset"] * pixel_bytes
        payload = packet["payload"]

        if row_id >= packet["height"]:
            return None
        if packet["x_offset"] >= width or x_byte_offset + len(payload) > row_bytes:
            return None

        if row_id not in current["rows"]:
            current["rows"][row_id] = bytearray(row_bytes)
            current["row_received"][row_id] = set()

        current["rows"][row_id][x_byte_offset:x_byte_offset + len(payload)] = payload
        current["row_received"][row_id].add((x_byte_offset, x_byte_offset + len(payload)))
        current["packets"] += 1

        if row_id not in current["complete_rows"]:
            if is_row_complete(current["row_received"][row_id], row_bytes):
                current["complete_rows"].add(row_id)

        if len(current["complete_rows"]) == current["height"]:
            return self.finalize_complete()
        return None

    def finalize_complete(self):
        current = self.current
        if current is None:
            return None

        width = current["width"]
        height = current["height"]
        row_bytes = current["row_bytes"]
        zero_row = bytes(row_bytes)
        image_rows = [bytes(current["rows"].get(row, zero_row)) for row in range(height)]

        self.out_dir.mkdir(parents=True, exist_ok=True)
        latest_path = self.out_dir / "latest_frame.bmp"
        save_bmp(latest_path, width, height, image_rows, current["pixel_bytes"])

        self.complete_frames += 1
        if self.complete_frames <= self.save_count:
            numbered_path = self.out_dir / (
                f"frame_{self.complete_frames:03d}_id_{current['frame_id']:03d}.bmp"
            )
            save_bmp(numbered_path, width, height, image_rows, current["pixel_bytes"])
            saved_note = f", saved={numbered_path.name}"
        else:
            saved_note = ""

        elapsed = time.time() - current["start_time"]
        mode_name = "RGB" if current["pixel_bytes"] == 3 else "GRAY"
        print(
            f"[OK] frame={self.complete_frames} frame_id={current['frame_id']} "
            f"{width}x{height} {mode_name} packets={current['packets']} "
            f"elapsed={elapsed:.3f}s src={current['source_ip']} "
            f"latest={latest_path.name}{saved_note}"
        )

        self.reset_current()
        return latest_path


def main():
    args = parse_args()
    out_dir = Path(args.out_dir)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, DEFAULT_RECV_BUFFER)
    sock.bind((args.bind_ip, args.port))
    sock.settimeout(0.2)
    actual_recv_buffer = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)

    collector = FrameCollector(out_dir=out_dir, save_count=args.save_count)
    total_udp_datagrams = 0
    unknown_udp_datagrams = 0
    unknown_examples = 0
    last_stat_time = time.time()
    last_stat_packets = 0
    last_stat_datagrams = 0
    last_stat_frames = 0

    print(f"Listening on UDP {args.bind_ip}:{args.port}")
    print(f"Socket receive buffer: {actual_recv_buffer} bytes")
    print(f"Output directory: {out_dir.resolve()}")
    print("Press KEY4 on FPGA, then watch for [OK] complete frame logs.")

    try:
        while True:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                if collector.current is not None:
                    if time.time() - collector.current["start_time"] > args.frame_timeout:
                        collector.finalize_incomplete("timeout")

                now = time.time()
                if now - last_stat_time >= 1.0:
                    datagram_delta = total_udp_datagrams - last_stat_datagrams
                    packet_delta = collector.total_packets - last_stat_packets
                    frame_delta = collector.complete_frames - last_stat_frames
                    print(
                        f"[STAT] total_udp={total_udp_datagrams} "
                        f"parsed_packets={collector.total_packets} "
                        f"unknown_udp={unknown_udp_datagrams} "
                        f"complete_frames={collector.complete_frames} "
                        f"dropped_frames={collector.dropped_frames} "
                        f"udp_dps={datagram_delta / (now - last_stat_time):.1f} "
                        f"pps={packet_delta / (now - last_stat_time):.1f} "
                        f"fps={frame_delta / (now - last_stat_time):.2f}"
                    )
                    last_stat_time = now
                    last_stat_datagrams = total_udp_datagrams
                    last_stat_packets = collector.total_packets
                    last_stat_frames = collector.complete_frames
                continue

            total_udp_datagrams += 1
            packet = parse_packet(data)
            if packet is None:
                unknown_udp_datagrams += 1
                if unknown_examples < 5:
                    preview = " ".join(f"{byte:02X}" for byte in data[:16])
                    print(
                        f"[UNKNOWN] len={len(data)} src={addr[0]}:{addr[1]} "
                        f"head={preview}"
                    )
                    unknown_examples += 1
                continue

            collector.accept_packet(packet, addr[0])

            if args.exit_after_complete > 0 and collector.complete_frames >= args.exit_after_complete:
                break
    finally:
        sock.close()

    print(
        f"Done. complete_frames={collector.complete_frames}, "
        f"dropped_frames={collector.dropped_frames}, "
        f"total_udp={total_udp_datagrams}, "
        f"parsed_packets={collector.total_packets}, "
        f"unknown_udp={unknown_udp_datagrams}"
    )


if __name__ == "__main__":
    main()
