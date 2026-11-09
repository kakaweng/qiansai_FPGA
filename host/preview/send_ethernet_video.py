import argparse
from pathlib import Path
import socket
import struct
import time
import zlib
import hashlib


WIDTH, HEIGHT = 640, 480
FRAME_BYTES = WIDTH * HEIGHT * 2
PAYLOAD_BYTES = 1024
PREFIX = struct.Struct(">4sIIHHI")
HEADER = struct.Struct(">4sIIHHII")
MAGIC = b"EVF1"


def packets(pixels, frame_id):
    if len(pixels) != FRAME_BYTES:
        raise ValueError(f"RGB565LE 图像必须为 {FRAME_BYTES} 字节")
    for offset in range(0, FRAME_BYTES, PAYLOAD_BYTES):
        payload = pixels[offset:offset + PAYLOAD_BYTES]
        prefix = PREFIX.pack(MAGIC, frame_id, offset, len(payload), 0, FRAME_BYTES)
        yield prefix + struct.pack(">I", zlib.crc32(prefix + payload)) + payload


def unpack_packet(packet):
    if len(packet) != HEADER.size + PAYLOAD_BYTES:
        raise ValueError("分包长度错误")
    magic, frame_id, offset, length, flags, total, crc = HEADER.unpack_from(packet)
    if (magic != MAGIC or flags or length != PAYLOAD_BYTES or total != FRAME_BYTES
            or offset >= total or offset % PAYLOAD_BYTES):
        raise ValueError("分包头错误")
    payload = packet[HEADER.size:]
    if zlib.crc32(packet[:PREFIX.size] + payload) != crc:
        raise ValueError("分包 CRC32 错误")
    return frame_id, offset, payload


def test_pattern():
    colors = (0xFFFF, 0xFFE0, 0x07FF, 0x07E0, 0xF81F, 0xF800, 0x001F, 0x0000)
    rows = []
    for y in range(HEIGHT):
        row = bytearray()
        for x in range(WIDTH):
            color = colors[x // 80]
            if x % 80 == 0 or y % 60 == 0:
                color = 0x8410
            row.extend(struct.pack("<H", color))
        rows.append(row)
    return b"".join(rows)


def load_pixels(arguments):
    if arguments.raw:
        pixels = arguments.raw.read_bytes()
    elif arguments.image:
        from PIL import Image
        image = Image.open(arguments.image).convert("RGB").resize((WIDTH, HEIGHT))
        pixels = bytearray()
        for red, green, blue in image.getdata():
            pixels.extend(struct.pack("<H", ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)))
        pixels = bytes(pixels)
    else:
        pixels = test_pattern()
    if len(pixels) != FRAME_BYTES:
        raise ValueError(f"需要 {FRAME_BYTES} 字节，实际 {len(pixels)} 字节")
    return pixels


def self_test():
    pixels = test_pattern()
    messages = list(packets(pixels, 0x12345678))
    assert len(messages) == 600
    reconstructed = bytearray()
    for index, message in enumerate(messages):
        frame_id, offset, payload = unpack_packet(message)
        assert frame_id == 0x12345678 and offset == index * PAYLOAD_BYTES
        reconstructed.extend(payload)
    assert reconstructed == pixels
    for position in (4, 8, HEADER.size + 1, len(messages[0]) - 1):
        damaged = bytearray(messages[0])
        damaged[position] ^= 1
        try:
            unpack_packet(damaged)
        except ValueError:
            pass
        else:
            raise AssertionError("损坏的分包被接受")
    print("PASS: 600 分包重组、RGB565LE 字节序、帧号/偏移/像素 CRC32 校验")


def wait_until(deadline):
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        if remaining > 0.001:
            time.sleep(remaining - 0.0005)


def main():
    parser = argparse.ArgumentParser(description="向 FPGA 发送 640x480 RGB565LE 图像；网络入口，不使用 HDC")
    parser.add_argument("--host", default="192.168.1.10", help="FPGA IP")
    parser.add_argument("--port", type=int, default=5002)
    parser.add_argument("--bind", default="", help="主机有线网卡 IPv4 地址")
    parser.add_argument("--fps", type=float, default=10)
    parser.add_argument("--packet-gap-us", type=float, default=100)
    parser.add_argument("--count", type=int, default=0, help="0 表示持续发送")
    parser.add_argument("--frame-id", type=int, default=1)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--raw", type=Path)
    source.add_argument("--image", type=Path, help="需安装 Pillow")
    parser.add_argument("--animate", action="store_true", help="叠加移动条，用于检查画面更新")
    parser.add_argument("--save-expected", type=Path, help="保存静态图的原始 RGB565LE 用于回读比对")
    parser.add_argument("--verify-received", type=Path, help="校验 RK 快照的 _ethernet.rgb565 文件后退出")
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()
    if arguments.self_test:
        self_test()
        return
    if (not 0 < arguments.port <= 65535 or not 0 < arguments.fps <= 60
            or not 0 <= arguments.packet_gap_us <= 100000 or arguments.count < 0
            or not 0 <= arguments.frame_id <= 0xFFFFFFFF):
        parser.error("端口、帧率、包间隔、数量或帧号超出范围")
    if arguments.animate and (arguments.save_expected or arguments.verify_received):
        parser.error("静态字节比对请去掉 --animate")
    pixels = load_pixels(arguments)
    if arguments.save_expected:
        arguments.save_expected.write_bytes(pixels)
    if arguments.verify_received:
        received = arguments.verify_received.read_bytes()
        if received != pixels:
            first = next((index for index, pair in enumerate(zip(received, pixels)) if pair[0] != pair[1]), min(len(received), len(pixels)))
            raise ValueError(f"回读不一致：received={len(received)} expected={len(pixels)} first_difference={first}")
        print(f"PASS: 以太网 → FPGA DDR → PCIe → RK 快照，{len(pixels)} 字节完全一致")
        return
    print(f"Ethernet video EVF1: {WIDTH}x{HEIGHT} RGB565LE bytes={FRAME_BYTES} chunks=600")
    print(f"destination={arguments.host}:{arguments.port} fps_limit={arguments.fps} packet_gap_us={arguments.packet_gap_us}")
    print(f"static_sha256={hashlib.sha256(pixels).hexdigest()}")
    completed = 0
    started = time.monotonic()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.settimeout(5)
        if arguments.bind:
            connection.bind((arguments.bind, 0))
        connection.connect((arguments.host, arguments.port))
        while not arguments.count or completed < arguments.count:
            frame_started = time.monotonic()
            frame_id = (arguments.frame_id + completed) & 0xFFFFFFFF
            outgoing = pixels
            if arguments.animate:
                moving = bytearray(pixels)
                x = (completed * 8) % (WIDTH - 16)
                for y in range(HEIGHT):
                    offset = (y * WIDTH + x) * 2
                    moving[offset:offset + 32] = b"\xff\xff" * 16
                outgoing = moving
            packet_deadline = time.monotonic()
            for message in packets(outgoing, frame_id):
                wait_until(packet_deadline)
                if connection.send(message) != len(message):
                    raise OSError("UDP 数据报发送不完整")
                packet_deadline = max(packet_deadline, time.monotonic()) + arguments.packet_gap_us / 1e6
            completed += 1
            if completed <= 3 or completed % 10 == 0 or completed == arguments.count:
                elapsed = time.monotonic() - started
                print(f"submitted={completed} input_frame_id={frame_id} elapsed={elapsed:.2f}s fps={completed / elapsed:.2f}", flush=True)
            wait_until(frame_started + 1 / arguments.fps)
    print(f"SEND_COMPLETE frames={completed}; UDP 发送完成，接收成功以 FPGA 状态和回读校验为准")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("发送已停止")
    except (OSError, ValueError, ImportError) as error:
        raise SystemExit(f"以太网图像工具失败：{error}")
