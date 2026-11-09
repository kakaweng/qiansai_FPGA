import unittest

from frame_to_png import frame_checksum
from multivideo_stream import GEOMETRIES, HEADER, MAGIC, read_frame


class FragmentedConnection:
    def __init__(self, data, fragment_bytes=4093):
        self.data = data
        self.position = 0
        self.fragment_bytes = fragment_bytes

    def recv(self, byte_count):
        end = min(self.position + min(byte_count, self.fragment_bytes), len(self.data))
        chunk = self.data[self.position:end]
        self.position = end
        return chunk


def record(source=1, **overrides):
    width, height = GEOMETRIES[source]
    pixels = b"\x00\xf8\xe0\x07\x1f\x00\xff\xff" * (width * height // 4)
    fields = dict(magic=MAGIC, version=1, header_bytes=64, source=source,
                  pixel_format=2, width=width, height=height, pixel_bytes=len(pixels),
                  checksum=frame_checksum(pixels), sequence=7, timestamp_ns=2000000000,
                  hdmi_timestamp_ns=1900000000 if source != 1 else 0,
                  camera_timestamp_ns=2000000000 if source != 0 else 0)
    fields.update(overrides)
    return HEADER.pack(*fields.values()) + pixels


class MultivideoProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.camera = record()

    def test_fragmented_camera_with_rgb565_byte_order(self):
        frame = read_frame(FragmentedConnection(self.camera, 17))
        self.assertEqual((frame.source, frame.width, frame.height, frame.sequence), (1, 640, 480, 7))
        self.assertEqual(frame.pixels[:8], b"\x00\xf8\xe0\x07\x1f\x00\xff\xff")

    def test_hdmi_and_mosaic_geometries(self):
        for source in (0, 2, 0xffffffff, 0xfffffffe):
            with self.subTest(source=source):
                frame = read_frame(FragmentedConnection(record(source)))
                self.assertEqual((frame.width, frame.height), GEOMETRIES[source])
                self.assertEqual(len(frame.pixels), frame.width * frame.height * 2)

    def test_two_consecutive_records(self):
        connection = FragmentedConnection(self.camera + self.camera, 1001)
        self.assertEqual(read_frame(connection), read_frame(connection))
        self.assertEqual(connection.recv(1), b"")

    def test_invalid_headers_rejected_before_payload(self):
        cases = ((0, 0), (1, 2), (2, 63), (3, 99), (4, 1), (5, 639),
                 (6, 479), (7, 0xffffffff), (9, 0), (10, 0))
        for index, invalid in cases:
            with self.subTest(field=index):
                fields = list(HEADER.unpack(self.camera[:HEADER.size]))
                fields[index] = invalid
                connection = FragmentedConnection(HEADER.pack(*fields) + self.camera[HEADER.size:])
                with self.assertRaises(ValueError):
                    read_frame(connection)
                self.assertEqual(connection.position, HEADER.size)

    def test_truncated_records(self):
        for byte_count in (0, 9, HEADER.size - 1, HEADER.size, len(self.camera) - 1):
            with self.subTest(bytes=byte_count):
                with self.assertRaises(EOFError):
                    read_frame(FragmentedConnection(self.camera[:byte_count]))

    def test_corrupt_pixels(self):
        corrupted = bytearray(self.camera)
        corrupted[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "校验失败"):
            read_frame(FragmentedConnection(bytes(corrupted)))

    def test_eof_reports_header_progress(self):
        with self.assertRaisesRegex(EOFError, "帧头.*9/64"):
            read_frame(FragmentedConnection(self.camera[:9]))

    def test_eof_reports_source_sequence_and_pixel_progress(self):
        with self.assertRaisesRegex(EOFError, "source=1 sequence=7.*101/614400"):
            read_frame(FragmentedConnection(self.camera[:HEADER.size + 101]))

    def test_timeout_reports_pixel_progress(self):
        class PausedConnection(FragmentedConnection):
            def recv(self, byte_count):
                if self.position == len(self.data):
                    raise TimeoutError("simulated stalled connection")
                return super().recv(byte_count)

        with self.assertRaisesRegex(TimeoutError, "source=1 sequence=7.*101/614400"):
            read_frame(PausedConnection(self.camera[:HEADER.size + 101]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
