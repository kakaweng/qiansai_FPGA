import argparse
import binascii
import ctypes
from pathlib import Path
import struct
import zlib

from frame_format import (
    FRAME_WIDTH, FRAME_HEIGHT, GRAY8_BYTES, RGB565_BYTES, PGM_HEADER, PPM_HEADER,
    RGB565LE, format_name, pixel_format, rgb565_to_rgb, rgb_to_rgb565,
)


FRAME_BYTES = GRAY8_BYTES


def read_frame(path):
    image = path.read_bytes()
    if image.startswith(PGM_HEADER):
        frame = image[len(PGM_HEADER):]
        if len(frame) != FRAME_BYTES:
            raise ValueError("PGM 灰度帧长度错误")
        return frame
    if image.startswith(PPM_HEADER):
        return rgb_to_rgb565(image[len(PPM_HEADER):])
    if path.suffix.lower() == ".rgb565" and len(image) == RGB565_BYTES:
        return image
    raise ValueError("请使用 1920x1080 的 PGM、PPM 或 RGB565LE 原始帧")


def _load_native_checksum():
    library_path = Path(__file__).resolve().with_name("libpango_frame_checksum.so")
    if not library_path.is_file():
        return None, "未找到加速库，请执行 bash build_checksum.sh"
    try:
        library = ctypes.CDLL(str(library_path))
        native_checksum = library.pango_frame_checksum
        native_checksum.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        native_checksum.restype = ctypes.c_uint32
        if native_checksum(ctypes.c_char_p(b"foobar"), 6) != 0xBF9CF968:
            return None, "加速库自检失败，请重新执行 bash build_checksum.sh"
        return native_checksum, None
    except (OSError, AttributeError) as error:
        return None, f"加速库加载失败：{error}"


_NATIVE_CHECKSUM, _CHECKSUM_LOAD_ERROR = _load_native_checksum()


def checksum_backend():
    if _NATIVE_CHECKSUM is not None:
        return "C/FNV-1a"
    return f"Python/FNV-1a（{_CHECKSUM_LOAD_ERROR}）"


def _python_frame_checksum(frame):
    checksum = 2166136261
    for pixel in frame:
        checksum = ((checksum ^ pixel) * 16777619) & 0xFFFFFFFF
    return checksum


def frame_checksum(frame):
    if _NATIVE_CHECKSUM is not None:
        if isinstance(frame, bytes):
            return _NATIVE_CHECKSUM(ctypes.c_char_p(frame), len(frame))
        if isinstance(frame, bytearray):
            native_buffer = (ctypes.c_ubyte * len(frame)).from_buffer(frame)
            return _NATIVE_CHECKSUM(native_buffer, len(frame))
    return _python_frame_checksum(frame)


def png_chunk(kind, payload):
    checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def write_png(frame, path):
    color = pixel_format(len(frame)) == RGB565LE
    if color:
        frame = rgb565_to_rgb(frame)
    row_bytes = FRAME_WIDTH * (3 if color else 1)
    header = struct.pack(">IIBBBBB", FRAME_WIDTH, FRAME_HEIGHT, 8, 2 if color else 0, 0, 0, 0)
    rows = b"".join(
        b"\x00" + frame[offset:offset + row_bytes]
        for offset in range(0, len(frame), row_bytes)
    )
    image = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", zlib.compress(rows))
        + png_chunk(b"IEND", b"")
    )
    path.write_bytes(image)


def main():
    parser = argparse.ArgumentParser(description="将 PCIe 灰度或 RGB565 彩色帧转为 PNG")
    parser.add_argument("input", type=Path, help="驱动导出的 .pgm/.rgb565，或 RKNN 保存的 .ppm")
    parser.add_argument("output", type=Path, help="输出的 PNG 文件")
    arguments = parser.parse_args()
    try:
        frame = read_frame(arguments.input)
        write_png(frame, arguments.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"转换失败：{error}\n")

    print(f"{FRAME_WIDTH}x{FRAME_HEIGHT} {format_name(frame)}, bytes={len(frame)}, checksum=0x{frame_checksum(frame):08x}")
    print(f"PNG 已保存：{arguments.output}")


if __name__ == "__main__":
    main()
