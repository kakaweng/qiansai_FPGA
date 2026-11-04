#!/usr/bin/env bash
set -euo pipefail
LANE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LANE_REPORT="$LANE_ROOT/rk3568_lane_deploy"
LANE_WORK="${LANE_UBUNTU_WORK:-$HOME/pcie_lane_rk3568}"
mkdir -p "$LANE_REPORT" "$LANE_WORK/rk3568_lane_deploy" "$LANE_WORK/tests"
exec > >(tee "$LANE_REPORT/diagnose.log") 2>&1
trap 'printf "DIAGNOSE_FAILED line=%s; see rk3568_lane_deploy/diagnose.log\n" "$LINENO"' ERR
printf 'Building lane diagnostics and cached preprocessing; reusing existing RKNN model.\n'
for filename in pcie_multivideo_server.c pcie_multivideo_protocol.h video_mosaic.c video_mosaic.h \
    lane_style_core.c lane_style_core.h lane_style_rknn.c lane_style_rknn.h lane_style_tensor.h \
    lane_style_async.c lane_style_async.h build_hdmi_lane.sh windows_lane_style.py lane_detection.py lane_boundary_type.py \
    build_checksum.sh frame_checksum.c frame_to_png.py frame_format.py multivideo_stream.py preview_multivideo.py; do
    cp -- "$LANE_ROOT/$filename" "$LANE_WORK/$filename"
done
for filename in lane_style_core_test.c lane_style_diagnostics_test.c lane_style_tensor_test.c \
    lane_style_decode_test.c lane_style_async_test.c verify_lane_style_core.py; do
    cp -- "$LANE_ROOT/tests/$filename" "$LANE_WORK/tests/$filename"
done
LANE_HEADER="${RKNN_HEADER_DIR:-$LANE_REPORT}"
cp -- "$LANE_HEADER/rknn_api.h" "$LANE_WORK/rk3568_lane_deploy/rknn_api.h"
LANE_BUILD_DIR="$LANE_WORK/rk3568_lane_deploy" RKNN_HEADER_DIR="$LANE_WORK/rk3568_lane_deploy" \
    LANE_SERVER_NAME=pcie_multivideo_server.lane_v3 bash "$LANE_WORK/build_hdmi_lane.sh"
LANE_PARITY_PYTHON="${LANE_TOOLKIT_VENV:-$HOME/.venvs/rknn-lane-2.3.2}/bin/python"
"$LANE_PARITY_PYTHON" "$LANE_WORK/tests/verify_lane_style_core.py" --executable "$LANE_WORK/rk3568_lane_deploy/lane_style_core_test"
bash "$LANE_WORK/build_checksum.sh"
cp -- "$LANE_WORK/rk3568_lane_deploy/pcie_multivideo_server.lane_v3" "$LANE_REPORT/"
python3 - "$LANE_ROOT" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
root = Path(sys.argv[1])
report = root / "rk3568_lane_deploy"
model = json.loads((report / "lane_style.manifest.json").read_text(encoding="utf-8"))
runtime = {"model_version": model["model_version"], "model_sha256": model["rknn_sha256"],
           "weights_epoch": model["weights_epoch"], "decoder": model["decoder_version"],
           "server_version": "multivideo-hdmi-lane-v3-diag", "preview_mode": "latest-frame-async-v1",
           "diagnostics": "lane-reasons-v1", "preprocessor": "cached-pil-bilinear-v1",
           "thresholds_changed": False, "max_result_age_ms": 750, "pending_queue_capacity": 1,
           "checksum": "C/FNV-1a", "board_status": "pending_inspect",
           "server_sha256": hashlib.sha256((report / "pcie_multivideo_server.lane_v3").read_bytes()).hexdigest(),
           "sources": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in
                       ("pcie_multivideo_server.c", "lane_style_async.c", "lane_style_core.c", "lane_style_core.h", "lane_style_rknn.c")}}
(report / "diagnostic_runtime.json").write_text(json.dumps(runtime, indent=2) + "\n", encoding="utf-8")
PY
bash "$LANE_ROOT/deploy_hdmi_lane_rk3568.sh" inspect-diag
bash "$LANE_ROOT/deploy_hdmi_lane_rk3568.sh" start-diag
python3 - "$LANE_REPORT" <<'PY'
import json
from pathlib import Path
import sys
root = Path(sys.argv[1])
runtime = json.loads((root / "diagnostic_runtime.json").read_text(encoding="utf-8"))
runtime["board_status"] = "started_preview_quality_pending"
for name in ("diagnostic_runtime.json", "preview_runtime.json"):
    (root / name).write_text(json.dumps(runtime, indent=2) + "\n", encoding="utf-8")
PY
printf '\nDIAGNOSE_PASS\nOpen preview:\nbash /mnt/hgfs/pcie_host/preview_hdmi_lane_fast.sh\n'
