#!/usr/bin/env bash
set -euo pipefail
LANE_WORK="${LANE_UBUNTU_WORK:-$HOME/pcie_lane_rk3568}"
test -f "$LANE_WORK/preview_multivideo.py"
python3 - "$LANE_WORK" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from frame_to_png import checksum_backend
backend = checksum_backend()
print("Preview checksum:", backend)
if backend != "C/FNV-1a":
    raise SystemExit("Native checksum unavailable; rerun optimize_hdmi_lane_rk3568.sh")
PY
exec python3 "$LANE_WORK/preview_multivideo.py" --host 127.0.0.1 --port 9004 --timeout 60 "$@"
