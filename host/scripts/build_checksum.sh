#!/usr/bin/env bash
set -euo pipefail

CHECKSUM_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CHECKSUM_CC="${CC:-cc}"

if ! command -v "$CHECKSUM_CC" >/dev/null 2>&1; then
    printf '找不到 C 编译器 %s，请先执行：sudo apt-get install build-essential\n' "$CHECKSUM_CC" >&2
    exit 1
fi

CHECKSUM_OUTPUT="$(mktemp "$CHECKSUM_DIR/.libpango_frame_checksum.XXXXXX")"
trap 'rm -f -- "$CHECKSUM_OUTPUT"' EXIT

"$CHECKSUM_CC" -O3 -std=c11 -fPIC -shared -Wall -Wextra -Werror \
    "$CHECKSUM_DIR/frame_checksum.c" -o "$CHECKSUM_OUTPUT"
mv -f -- "$CHECKSUM_OUTPUT" "$CHECKSUM_DIR/libpango_frame_checksum.so"

python3 - "$CHECKSUM_DIR" <<'PY'
import sys
import time

sys.path.insert(0, sys.argv[1])
from frame_to_png import FRAME_BYTES, checksum_backend, frame_checksum

backend = checksum_backend()
if backend != "C/FNV-1a":
    raise RuntimeError(f"加速库加载失败：{backend}")

pixels = bytes(range(256)) * (FRAME_BYTES // 256)
if frame_checksum(b"") != 2166136261:
    raise RuntimeError("空数据校验测试失败")
if frame_checksum(pixels) != 0x34BD69C5:
    raise RuntimeError("整帧 bytes 校验测试失败")
if frame_checksum(bytearray(pixels)) != 0x34BD69C5:
    raise RuntimeError("整帧 bytearray 校验测试失败")

started = time.perf_counter()
for iteration in range(5):
    frame_checksum(pixels)
elapsed_ms = (time.perf_counter() - started) * 1000 / 5
print(f"校验后端：{backend}；完整帧校验通过；每帧校验平均 {elapsed_ms:.3f} ms")
PY
