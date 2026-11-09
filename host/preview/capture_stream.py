import argparse
import os
from pathlib import Path
import struct
import sys
import time

from frame_to_png import FRAME_BYTES, frame_checksum
from frame_format import FRAME_WIDTH, FRAME_HEIGHT, pixel_format, save_record_frame


STREAM_MAGIC = 0x50475246
STREAM_VERSION = 1
STREAM_HEADER = struct.Struct("<IHHIIIIQQ")
RECORD_BYTES = STREAM_HEADER.size + FRAME_BYTES


def read_exact(file_descriptor, byte_count):
    record = bytearray()
    while len(record) < byte_count:
        chunk = os.read(file_descriptor, byte_count - len(record))
        if not chunk:
            raise RuntimeError("stream device closed before a complete record was received")
        record.extend(chunk)
    return bytes(record)


def read_record(file_descriptor):
    fields = STREAM_HEADER.unpack(read_exact(file_descriptor, STREAM_HEADER.size))
    magic, version, header_bytes, width, height, pixel_bytes, checksum, sequence, timestamp_ns = fields
    if magic != STREAM_MAGIC or version != STREAM_VERSION:
        raise RuntimeError(f"invalid stream header: magic=0x{magic:08x} version={version}")
    if header_bytes != STREAM_HEADER.size or width != FRAME_WIDTH or height != FRAME_HEIGHT:
        raise RuntimeError("stream metadata does not describe a 1920x1080 frame")
    pixel_format(pixel_bytes)
    pixels = read_exact(file_descriptor, pixel_bytes)
    actual_checksum = frame_checksum(pixels)
    if actual_checksum != checksum:
        raise RuntimeError(
            f"sequence {sequence}: checksum mismatch, header=0x{checksum:08x} "
            f"actual=0x{actual_checksum:08x}"
        )
    return sequence, timestamp_ns, checksum, pixels


def main():
    parser = argparse.ArgumentParser(description="从连续 PCIe 帧设备读取灰度或 RGB565 彩色帧")
    parser.add_argument("device", help="设备或本地记录文件路径；- 读取可靠二进制标准输入，勿使用 HDC shell 管道")
    parser.add_argument("output", type=Path, help="输出 .pgm 或 .rgb565 文件目录")
    parser.add_argument("--count", type=int, default=30, help="采集帧数，默认 30")
    parser.add_argument("--duration", type=float, help="按秒采集；与 --count 不能同时使用")
    arguments = parser.parse_args()
    if arguments.count < 1 or arguments.count > 100000:
        parser.error("--count 必须在 1 到 100000 之间")
    if arguments.duration is not None and arguments.duration <= 0:
        parser.error("--duration 必须大于零")
    if arguments.duration is not None and arguments.count != 30:
        parser.error("使用 --duration 时请不要指定 --count")
    if arguments.device != "-" and not Path(arguments.device).exists():
        parser.error(f"设备不存在：{arguments.device}")
    arguments.output.mkdir(parents=True, exist_ok=False)
    metadata_path = arguments.output / "frames.csv"
    start = time.monotonic()
    deadline = start + arguments.duration if arguments.duration is not None else None
    captured = 0
    previous_sequence = None
    dropped = 0
    if arguments.device == "-":
        stream = sys.stdin.buffer
        close_stream = False
    else:
        stream = Path(arguments.device).open("rb", buffering=0)
        close_stream = True
    try:
        with metadata_path.open("w", encoding="ascii") as metadata:
            metadata.write("file,sequence,timestamp_ns,checksum\n")
            while deadline is None or time.monotonic() < deadline:
                sequence, timestamp_ns, checksum, pixels = read_record(stream.fileno())
                if previous_sequence is not None and sequence > previous_sequence + 1:
                    dropped += sequence - previous_sequence - 1
                previous_sequence = sequence
                captured += 1
                image_name = save_record_frame(arguments.output / f"frame_{captured:06d}", pixels).name
                metadata.write(f"{image_name},{sequence},{timestamp_ns},0x{checksum:08x}\n")
                metadata.flush()
                print(
                    f"Captured {captured}: sequence={sequence} checksum=0x{checksum:08x} "
                    f"bytes={len(pixels)}"
                )
                if arguments.duration is None and captured >= arguments.count:
                    break
    finally:
        if close_stream:
            stream.close()
    elapsed = time.monotonic() - start
    print(f"Stream capture completed: frames={captured} elapsed_s={elapsed:.3f} dropped_sequences={dropped}")


if __name__ == "__main__":
    main()
