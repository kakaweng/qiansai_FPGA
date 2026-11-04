#!/usr/bin/env bash
set -euo pipefail
LANE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LANE_REPORT="$LANE_ROOT/rk3568_lane_deploy"
LANE_WORK="${LANE_UBUNTU_WORK:-$HOME/pcie_lane_rk3568}"
LANE_VENV="${LANE_TOOLKIT_VENV:-$HOME/.venvs/rknn-lane-2.3.2}"
mkdir -p "$LANE_REPORT" "$LANE_WORK/rk3568_lane_deploy" "$LANE_WORK/training" "$LANE_WORK/tests"
exec > >(tee "$LANE_REPORT/prepare.log") 2>&1
trap 'printf "PREPARE_FAILED line=%s; see rk3568_lane_deploy/prepare.log\n" "$LINENO"' ERR
printf 'Preparing HDMI lane model; existing board stream remains running.\n'
date -u '+%Y-%m-%d %H:%M:%S UTC'
for filename in pcie_multivideo_server.c pcie_multivideo_protocol.h video_mosaic.c video_mosaic.h \
    lane_style_core.c lane_style_core.h lane_style_rknn.c lane_style_rknn.h lane_style_tensor.h build_hdmi_lane.sh \
    lane_style_async.c lane_style_async.h \
    windows_lane_style.py lane_detection.py lane_boundary_type.py; do
    cp -- "$LANE_ROOT/$filename" "$LANE_WORK/$filename"
done
cp -- "$LANE_ROOT/training/convert_lane_style_rknn.py" "$LANE_WORK/training/"
cp -- "$LANE_ROOT/tests/lane_style_core_test.c" "$LANE_WORK/tests/"
cp -- "$LANE_ROOT/tests/lane_style_tensor_test.c" "$LANE_WORK/tests/"
cp -- "$LANE_ROOT/tests/lane_style_decode_test.c" "$LANE_WORK/tests/"
cp -- "$LANE_ROOT/tests/lane_style_async_test.c" "$LANE_WORK/tests/"
cp -- "$LANE_ROOT/tests/lane_style_diagnostics_test.c" "$LANE_WORK/tests/"
cp -- "$LANE_ROOT/tests/verify_lane_style_core.py" "$LANE_WORK/tests/"
LANE_HEADER="${RKNN_HEADER_DIR:-$HOME/rknn-toolkit2-npu/rknpu2/runtime/Linux/librknn_api/include}"
cp -- "$LANE_HEADER/rknn_api.h" "$LANE_WORK/rk3568_lane_deploy/rknn_api.h"
bash "$LANE_WORK/build_hdmi_lane.sh"
cp -- "$LANE_WORK/rk3568_lane_deploy/pcie_multivideo_server.lane_v1" "$LANE_REPORT/"
if [[ ! -x "$LANE_VENV/bin/python" ]] || ! "$LANE_VENV/bin/python" -m pip --version >/dev/null 2>&1; then
    if ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
        printf 'Missing Python venv support. Run: sudo apt-get update && sudo apt-get install -y python3.8-venv\n' >&2
        exit 1
    fi
    python3 -m venv "$LANE_VENV"
fi
if ! "$LANE_VENV/bin/python" -c 'from importlib.metadata import version; assert version("rknn-toolkit2").split("+")[0] == "2.3.2"; import rknn.api, onnxruntime, cv2, PIL' 2>/dev/null; then
    "$LANE_VENV/bin/python" -m pip install --upgrade 'pip<25.1' wheel setuptools
    "$LANE_VENV/bin/python" -m pip install --index-url https://download.pytorch.org/whl/cpu 'torch==2.4.0'
    "$LANE_VENV/bin/python" -m pip install --index-url https://pypi.org/simple 'rknn-toolkit2==2.3.2' 'Pillow>=9.1' 'opencv-python<4.12' 'numpy<=1.26.4'
fi
"$LANE_VENV/bin/python" "$LANE_WORK/tests/verify_lane_style_core.py" --executable "$LANE_WORK/rk3568_lane_deploy/lane_style_core_test"
"$LANE_VENV/bin/python" "$LANE_WORK/training/convert_lane_style_rknn.py" \
    --onnx "$LANE_ROOT/runs/lane_style/bdd_lraspp_v1_20261013/best.onnx" \
    --output "$LANE_WORK/rk3568_lane_deploy/lane_style.rknn"
for filename in lane_style.rknn lane_style.contract lane_style.manifest.json; do
    cp -- "$LANE_WORK/rk3568_lane_deploy/$filename" "$LANE_REPORT/$filename"
done
printf '\nPREPARE_PASS\nNext: sudo -v && bash /mnt/hgfs/pcie_host/deploy_hdmi_lane_rk3568.sh inspect\n'
