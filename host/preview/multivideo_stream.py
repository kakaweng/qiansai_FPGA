from dataclasses import dataclass
import socket
import struct

from frame_to_png import frame_checksum


HEADER = struct.Struct("<IHHIIIIIIQQQQ")
MAGIC = 0x31465650
GEOMETRIES = {0: (1920, 1080), 1: (640, 480), 2: (640, 480),
              0xFFFFFFFF: (1280, 480), 0xFFFFFFFE: (1280, 480)}


@dataclass(frozen=True)
class VideoFrame:
    source: int
    width: int
    height: int
    sequence: int
    timestamp_ns: int
    hdmi_timestamp_ns: int
    camera_timestamp_ns: int
    pixels: bytes


def read_exact(connection, byte_count, part="视频数据"):
    data = bytearray()
    while len(data) < byte_count:
        try:
            chunk = connection.recv(byte_count - len(data))
        except (TimeoutError, socket.timeout) as error:
            raise TimeoutError(f"接收{part}超时：已收到 {len(data)}/{byte_count} 字节") from error
        if not chunk:
            raise EOFError(f"接收{part}时连接关闭：已收到 {len(data)}/{byte_count} 字节")
        data.extend(chunk)
    return bytes(data)


def read_frame(connection):
    fields = HEADER.unpack(read_exact(connection, HEADER.size, "帧头"))
    magic, version, header_bytes, source, pixel_format, width, height, pixel_bytes, checksum = fields[:9]
    sequence, timestamp_ns, hdmi_timestamp_ns, camera_timestamp_ns = fields[9:]
    if magic != MAGIC or version != 1 or header_bytes != HEADER.size:
        raise ValueError("视频记录版本或帧头长度错误，请配套使用双源驱动和服务程序")
    if source not in GEOMETRIES or (width, height) != GEOMETRIES[source]:
        raise ValueError(f"不支持的视频源或尺寸：source={source} size={width}x{height}")
    if pixel_format != 2 or pixel_bytes != width * height * 2:
        raise ValueError("RGB565LE 像素格式或帧字节数错误")
    if not sequence or not timestamp_ns:
        raise ValueError("视频帧序号或时间戳无效")
    pixels = read_exact(connection, pixel_bytes, f"像素 source={source} sequence={sequence}")
    if frame_checksum(pixels) != checksum:
        raise ValueError(f"视频帧校验失败：source={source} sequence={sequence}")
    return VideoFrame(source, width, height, sequence, timestamp_ns,
                      hdmi_timestamp_ns, camera_timestamp_ns, pixels)
