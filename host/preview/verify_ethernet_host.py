from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading

import send_ethernet_video as video


def main():
    video.self_test()
    errors = []
    received = bytearray()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        listener.settimeout(10)

        def receive():
            try:
                for index in range(600):
                    packet, _ = listener.recvfrom(2048)
                    frame_id, offset, payload = video.unpack_packet(packet)
                    assert frame_id == 9 and offset == index * 1024
                    received.extend(payload)
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=receive)
        worker.start()
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "expected.rgb565"
            command = [sys.executable, str(Path(video.__file__)), "--host", "127.0.0.1",
                       "--port", str(listener.getsockname()[1]), "--count", "1", "--frame-id", "9",
                       "--packet-gap-us", "250", "--save-expected", str(expected)]
            subprocess.run(command, check=True, timeout=20)
            worker.join(12)
            assert not worker.is_alive() and not errors, errors
            assert received == expected.read_bytes() == video.test_pattern()
            verify = [sys.executable, str(Path(video.__file__)), "--verify-received", str(expected)]
            subprocess.run(verify, check=True, timeout=10)
            damaged = bytearray(received)
            damaged[12345] ^= 1
            expected.write_bytes(damaged)
            result = subprocess.run(verify, capture_output=True, timeout=10)
            assert result.returncode != 0
    print("PASS: localhost UDP 600 packets, exact RGB565 bytes, CLI readback success and corruption rejection")


if __name__ == "__main__":
    main()
