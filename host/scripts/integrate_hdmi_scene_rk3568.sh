#!/usr/bin/env bash
set -euo pipefail
SCENE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCENE_REPORT="$SCENE_ROOT/rk3568_scene_deploy"
SCENE_WORK="${LANE_UBUNTU_WORK:-$HOME/pcie_lane_rk3568}"
SCENE_HDC="${HDC:-$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc}"
SCENE_TARGET="${TARGET:-7001005458323933328a26cf4db23900}"
SCENE_OLD_MODEL=/data/local/tmp/rknn-yolov5-test/model/yolov5s-640-640.rknn
SCENE_BOARD=/data/local/tmp/hdmi_lane_v1
SCENE_BASE=/data/local/tmp/rknn-yolov5-test
mkdir -p "$SCENE_REPORT" "$SCENE_WORK/rk3568_lane_deploy" "$SCENE_WORK/tests"
exec > >(tee "$SCENE_REPORT/integrate.log") 2>&1
SCENE_SWITCH_ATTEMPTED=0
scene_failed()
{
    printf 'SCENE_INTEGRATE_FAILED line=%s; see rk3568_scene_deploy/integrate.log\n' "$1"
    if [[ "$SCENE_SWITCH_ATTEMPTED" == 1 ]]; then
        printf 'Restoring the retained lane-only v3 service.\n'
        if ! bash "$SCENE_ROOT/deploy_hdmi_lane_rk3568.sh" start-diag; then
            printf 'Automatic lane-only restore failed; inspect both deployment logs.\n'
        fi
    fi
    exit 1
}
trap 'scene_failed "$LINENO"' ERR
printf 'Adding HDMI-only YOLO objects and signal color; keeping existing lane model.\n'
sudo -n true
for filename in pcie_multivideo_server.c pcie_multivideo_protocol.h video_mosaic.c video_mosaic.h \
    lane_style_core.c lane_style_core.h lane_style_rknn.c lane_style_rknn.h lane_style_tensor.h lane_style_async.c lane_style_async.h \
    road_object_core.c road_object_core.h road_object_rknn.c road_object_rknn.h road_object_async.c road_object_async.h \
    rknn_scheduler.c rknn_scheduler.h build_hdmi_lane.sh build_checksum.sh frame_checksum.c frame_to_png.py frame_format.py \
    multivideo_stream.py preview_multivideo.py traffic_light.py scene_sha256.c; do
    cp -- "$SCENE_ROOT/$filename" "$SCENE_WORK/$filename"
done
for filename in lane_style_core_test.c lane_style_diagnostics_test.c lane_style_tensor_test.c lane_style_decode_test.c \
    lane_style_async_test.c road_object_core_test.c road_object_async_test.c verify_road_object_core.py verify_scene_sha256.py; do
    cp -- "$SCENE_ROOT/tests/$filename" "$SCENE_WORK/tests/$filename"
done
cp -- "$SCENE_ROOT/rk3568_lane_deploy/rknn_api.h" "$SCENE_WORK/rk3568_lane_deploy/rknn_api.h"
LANE_BUILD_DIR="$SCENE_WORK/rk3568_lane_deploy" RKNN_HEADER_DIR="$SCENE_WORK/rk3568_lane_deploy" \
    LANE_SERVER_NAME=pcie_multivideo_server.scene_v4 bash "$SCENE_WORK/build_hdmi_lane.sh"
gcc -O2 -std=c11 -Wall -Wextra -Werror -fPIC -shared "$SCENE_WORK/road_object_core.c" \
    -o "$SCENE_WORK/rk3568_lane_deploy/libroad_object_core_test.so" -lm
SCENE_PARITY_PYTHON="${LANE_TOOLKIT_VENV:-$HOME/.venvs/rknn-lane-2.3.2}/bin/python"
"$SCENE_PARITY_PYTHON" "$SCENE_WORK/tests/verify_road_object_core.py" \
    --library "$SCENE_WORK/rk3568_lane_deploy/libroad_object_core_test.so"
bash "$SCENE_WORK/build_checksum.sh"
cp -- "$SCENE_WORK/rk3568_lane_deploy/pcie_multivideo_server.scene_v4" "$SCENE_REPORT/"
# OpenHarmony's toybox may not include sha256sum. Use our tested utility with
# the already-deployed glibc loader; no board package installation is needed.
gcc -O2 -std=c11 -Wall -Wextra -Werror "$SCENE_WORK/scene_sha256.c" \
    -o "$SCENE_WORK/rk3568_lane_deploy/scene_sha256_host"
python3 "$SCENE_WORK/tests/verify_scene_sha256.py" --program "$SCENE_WORK/rk3568_lane_deploy/scene_sha256_host"
"${RKNN_CC:-aarch64-linux-gnu-gcc}" -O2 -std=c11 -Wall -Wextra -Werror "$SCENE_WORK/scene_sha256.c" \
    -o "$SCENE_REPORT/scene_sha256"
sudo -n "$SCENE_HDC" -t "$SCENE_TARGET" shell mkdir -p "$SCENE_BOARD"
sudo -n "$SCENE_HDC" -t "$SCENE_TARGET" file send "$SCENE_REPORT/scene_sha256" "$SCENE_BOARD/scene_sha256"
sudo -n "$SCENE_HDC" -t "$SCENE_TARGET" shell chmod 755 "$SCENE_BOARD/scene_sha256"
SCENE_HASH_OUTPUT="$(sudo -n "$SCENE_HDC" -t "$SCENE_TARGET" shell \
    "$SCENE_BASE/glibc/ld-linux-aarch64.so.1" --library-path "$SCENE_BASE/lib:$SCENE_BASE/glibc" \
    "$SCENE_BOARD/scene_sha256" "$SCENE_OLD_MODEL")"
printf '%s\n' "$SCENE_HASH_OUTPUT"
SCENE_HASH="$(printf '%s\n' "$SCENE_HASH_OUTPUT" | sed -nE 's/^([a-fA-F0-9]{64})[[:space:]].*/\1/p' | head -n 1)"
[[ "$SCENE_HASH" =~ ^[a-fA-F0-9]{64}$ ]]
sudo -n "$SCENE_HDC" -t "$SCENE_TARGET" file recv "$SCENE_OLD_MODEL" "$SCENE_REPORT/road_objects.download"
python3 - "$SCENE_REPORT" "$SCENE_HASH" <<'PY'
import hashlib
from pathlib import Path
import sys
root = Path(sys.argv[1])
source = root / "road_objects.download"
if hashlib.sha256(source.read_bytes()).hexdigest() != sys.argv[2].lower():
    raise SystemExit("HDC object model SHA256 mismatch; current lane server remains running")
source.replace(root / "road_objects.rknn")
PY
python3 "$SCENE_ROOT/record_hdmi_scene_contract.py" "$SCENE_REPORT" --board-sha256 "$SCENE_HASH"
bash "$SCENE_ROOT/deploy_hdmi_lane_rk3568.sh" inspect-scene
SCENE_SWITCH_ATTEMPTED=1
bash "$SCENE_ROOT/deploy_hdmi_lane_rk3568.sh" start-scene
python3 - "$SCENE_ROOT" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
root = Path(sys.argv[1])
lane = json.loads((root / "rk3568_lane_deploy/lane_style.manifest.json").read_text(encoding="utf-8"))
objects = json.loads((root / "rk3568_scene_deploy/road_objects.manifest.json").read_text(encoding="utf-8"))
runtime = {"server_version": "multivideo-hdmi-scene-v4", "lane_model_sha256": lane["rknn_sha256"],
           "object_model_sha256": objects["artifact"]["sha256"], "object_decoder": objects["decoder"]["version"],
           "signal_version": objects["signal"]["version"],
           "server_sha256": hashlib.sha256((root / "rk3568_scene_deploy/pcie_multivideo_server.scene_v4").read_bytes()).hexdigest(),
           "preview_mode": "two-independent-latest-frame-workers", "max_result_age_ms": 750,
           "signal_evidence_timeout_ms": 500, "source": "HDMI only", "checksum": "C/FNV-1a",
           "board_status": "started_visual_quality_and_two_model_performance_pending"}
(root / "rk3568_scene_deploy/scene_runtime.json").write_text(json.dumps(runtime, indent=2) + "\n", encoding="utf-8")
objects["validation"]["board_status"] = "two_model_inspect_passed_preview_pending"
(root / "rk3568_scene_deploy/road_objects.manifest.json").write_text(json.dumps(objects, indent=2) + "\n", encoding="utf-8")
(root / "rk3568_lane_deploy/preview_runtime.json").write_text(json.dumps(runtime, indent=2) + "\n", encoding="utf-8")
PY
printf '\nSCENE_INTEGRATE_PASS\nOpen preview:\nbash /mnt/hgfs/pcie_host/preview_hdmi_lane_fast.sh\n'
