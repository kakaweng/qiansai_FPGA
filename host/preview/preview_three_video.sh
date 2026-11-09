#!/usr/bin/env bash
set -euo pipefail
THREE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
THREE_WORK="$HOME/pcie_three_video"
mkdir -p "$THREE_WORK"
for THREE_FILE in preview_three_video.py preview_multivideo.py multivideo_stream.py frame_to_png.py frame_format.py frame_checksum.c build_checksum.sh; do
    if [[ "$THREE_ROOT" != "$THREE_WORK" ]] && ! cmp -s "$THREE_ROOT/$THREE_FILE" "$THREE_WORK/$THREE_FILE"; then
        cp -- "$THREE_ROOT/$THREE_FILE" "$THREE_WORK/$THREE_FILE"
    fi
done
if [[ ! -f "$THREE_WORK/libpango_frame_checksum.so" || "$THREE_WORK/frame_checksum.c" -nt "$THREE_WORK/libpango_frame_checksum.so" ]]; then
    bash "$THREE_WORK/build_checksum.sh"
fi
python3 - "$THREE_WORK" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import cv2, numpy
from frame_to_png import checksum_backend
if checksum_backend() != 'C/FNV-1a':
    raise SystemExit('C校验库不可用；请执行 bash ~/pcie_three_video/build_checksum.sh 后重试。')
print('THREE_VIDEO_PREVIEW_READY OpenCV=%s checksum=C/FNV-1a' % cv2.__version__)
PY
if [[ "${1:-}" == --prepare-only ]]; then exit 0; fi
cd "$THREE_WORK"
exec python3 "$THREE_WORK/preview_three_video.py" "$@"
