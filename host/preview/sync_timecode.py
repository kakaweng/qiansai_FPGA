"""Small binary timecode carried by pixels, independent of HDMI/EVF1 metadata.

32 bytes, network order: magic, version, role, phase, session, wire id,
effective scene milliseconds, source frame index, CRC32. A 16x16 bit grid
uses 4px cells at 640px width and 12px cells at 1920px width.
"""
import struct
import zlib

PREFIX = struct.Struct(">4sBBHIIQI")
READY, PLAYING, HELD = 1, 2, 3


def stamp_timecode(image, role, phase, session, wire, scene_ns, source_index):
    if role not in (1, 2) or phase not in (READY, PLAYING, HELD):
        raise ValueError("Invalid timecode role/phase")
    prefix = PREFIX.pack(b"SYN1", 1, role, phase, session, wire,
                         max(0, round(scene_ns / 1e6)), source_index)
    data = prefix + struct.pack(">I", zlib.crc32(prefix))
    scale = image.shape[1] // 640
    cell, origin = 4 * scale, 8 * scale
    if scale < 1 or origin + 16 * cell > image.shape[0]:
        raise ValueError("Image too small for timecode")
    for bit in range(256):
        y, x = divmod(bit, 16)
        value = 255 if data[bit // 8] & (128 >> (bit % 8)) else 0
        image[origin+y*cell:origin+(y+1)*cell, origin+x*cell:origin+(x+1)*cell] = value


def read_timecode(image):
    scale = image.shape[1] // 640
    cell, origin = 4 * scale, 8 * scale
    if not scale or origin + 16 * cell > image.shape[0]:
        return None
    data = bytearray(32)
    for bit in range(256):
        y, x = divmod(bit, 16)
        pixel = image[origin+y*cell+cell//2, origin+x*cell+cell//2]
        if int(pixel[0]) + int(pixel[1]) + int(pixel[2]) > 384:
            data[bit // 8] |= 128 >> (bit % 8)
    if zlib.crc32(data[:28]) != struct.unpack_from(">I", data, 28)[0]:
        return None
    magic, version, role, phase, session, wire, scene_ms, index = PREFIX.unpack(data[:28])
    if magic != b"SYN1" or version != 1 or role not in (1, 2) or phase not in (1, 2, 3):
        return None
    return dict(role=role, phase=phase, session=session, wire=wire, scene_ms=scene_ms, source_index=index)
