#!/usr/bin/env bash
set -euo pipefail

LANE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LANE_REPORT="$LANE_ROOT/rk3568_lane_deploy"
mkdir -p "$LANE_REPORT"
exec > >(tee "$LANE_REPORT/environment.txt") 2>&1
printf 'Lane deployment environment\n'
date -u '+%Y-%m-%d %H:%M:%S UTC'
uname -a
printf '\nCompilers and Python\n'
for executable in python3 aarch64-linux-gnu-gcc gcc; do
    command -v "$executable" || true
done
python3 - <<'PY'
import importlib.metadata
import json
import pathlib
import sys
packages = {}
for name in ("rknn-toolkit2", "onnx", "onnxruntime", "numpy", "Pillow"):
    try:
        packages[name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        packages[name] = None
print(json.dumps({"python": sys.executable, "packages": packages}))
home = pathlib.Path.home()
for relative in ("rknn-toolkit2-npu", "rknn-toolkit2", "rknn-yolov5-test", "pcie_host"):
    directory = home / relative
    if directory.is_dir():
        print("ASSET_ROOT", directory)
for pattern in ("**/bin/activate", "**/rknn_api.h"):
    for base in (home / "rknn-toolkit2-npu", home / "rknn-toolkit2", home / "pcie_host"):
        if base.is_dir():
            for match in list(base.glob(pattern))[:12]:
                print("ASSET", match)
PY
LANE_HEADER="${RKNN_HEADER_DIR:-$HOME/rknn-toolkit2-npu/rknpu2/runtime/Linux/librknn_api/include}/rknn_api.h"
if [[ -f "$LANE_HEADER" ]]; then
    cp -- "$LANE_HEADER" "$LANE_REPORT/rknn_api.h"
    printf '\nRKNN header copied to %s\n' "$LANE_REPORT/rknn_api.h"
fi
LANE_HDC="${HDC:-$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc}"
LANE_TARGET="${TARGET:-7001005458323933328a26cf4db23900}"
printf '\nBoard connection via %s\n' "$LANE_HDC"
if [[ -x "$LANE_HDC" ]]; then
    if sudo -n true 2>/dev/null; then
        sudo -n "$LANE_HDC" list targets || true
        sudo -n "$LANE_HDC" -t "$LANE_TARGET" shell 'uname -a; ls -l /dev/pango_pcie_multivideo_*; command -v python3; ls -l /data/local/tmp/rknn-yolov5-test/lib/librknnrt.so /data/local/tmp/rknn-yolov5-test/glibc/ld-linux-aarch64.so.1; cat /proc/meminfo | head -n 3' || true
    else
        printf 'Sudo authorization unavailable; run sudo -v, then rerun this probe.\n'
    fi
else
    printf 'HDC not found at configured path. Set HDC=/actual/path/to/hdc.\n'
fi
printf '\nReport ready: %s/environment.txt\n' "$LANE_REPORT"
