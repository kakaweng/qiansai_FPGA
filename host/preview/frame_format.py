from array import array
import sys


FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080
GRAY8_BYTES = FRAME_WIDTH * FRAME_HEIGHT
RGB565_BYTES = GRAY8_BYTES * 2
GRAY8 = 1
RGB565LE = 2
PGM_HEADER = b"P5\n1920 1080\n255\n"
PPM_HEADER = b"P6\n1920 1080\n255\n"


def pixel_format(pixel_bytes):
    if pixel_bytes == GRAY8_BYTES:
        return GRAY8
    if pixel_bytes == RGB565_BYTES:
        return RGB565LE
    raise ValueError(f"不支持的 1920x1080 像素大小：{pixel_bytes}")


def format_name(pixels):
    return "RGB565LE" if pixel_format(len(pixels)) == RGB565LE else "GRAY8"


def rgb565_to_rgb(pixels):
    if len(pixels) != RGB565_BYTES:
        raise ValueError("RGB565 帧长度错误")
    packed = array("H")
    packed.frombytes(pixels)
    if sys.byteorder != "little":
        packed.byteswap()
    rgb = bytearray(GRAY8_BYTES * 3)
    for index, value in enumerate(packed):
        red = (value >> 11) & 31
        green = (value >> 5) & 63
        blue = value & 31
        rgb[index * 3] = (red << 3) | (red >> 2)
        rgb[index * 3 + 1] = (green << 2) | (green >> 4)
        rgb[index * 3 + 2] = (blue << 3) | (blue >> 2)
    return bytes(rgb)


def rgb_to_rgb565(rgb):
    if len(rgb) != GRAY8_BYTES * 3:
        raise ValueError("PPM 彩色帧长度错误")
    packed = array("H", (
        ((rgb[offset] >> 3) << 11) | ((rgb[offset + 1] >> 2) << 5) | (rgb[offset + 2] >> 3)
        for offset in range(0, len(rgb), 3)
    ))
    if sys.byteorder != "little":
        packed.byteswap()
    return packed.tobytes()


def to_bgr(pixels, numpy):
    if pixel_format(len(pixels)) == GRAY8:
        gray = numpy.frombuffer(pixels, dtype=numpy.uint8).reshape(FRAME_HEIGHT, FRAME_WIDTH)
        return numpy.repeat(gray[:, :, None], 3, axis=2)
    packed = numpy.frombuffer(pixels, dtype="<u2").reshape(FRAME_HEIGHT, FRAME_WIDTH)
    image = numpy.empty((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=numpy.uint8)
    red = (packed >> 11) & 31
    green = (packed >> 5) & 63
    blue = packed & 31
    image[:, :, 2] = (red << 3) | (red >> 2)
    image[:, :, 1] = (green << 2) | (green >> 4)
    image[:, :, 0] = (blue << 3) | (blue >> 2)
    return image


def save_record_frame(prefix, pixels):
    if pixel_format(len(pixels)) == RGB565LE:
        path = prefix.with_suffix(".rgb565")
        path.write_bytes(pixels)
    else:
        path = prefix.with_suffix(".pgm")
        path.write_bytes(PGM_HEADER + pixels)
    return path
