#!/usr/bin/env bash
set -euo pipefail
LANE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LANE_REPORT="$LANE_ROOT/rk3568_lane_deploy"
LANE_WORK="${LANE_UBUNTU_WORK:-$HOME/pcie_lane_rk3568}"
mkdir -p "$LANE_REPORT" "$LANE_WORK/rk3568_lane_deploy" "$LANE_WORK/tests"
exec > >(tee "$LANE_REPORT/recheck.log") 2>&1
printf 'Rebuilding HDMI lane tensor diagnostics; reusing converted RKNN model.\n'
for filename in pcie_multivideo_server.c pcie_multivideo_protocol.h video_mosaic.c video_mosaic.h \
    lane_style_core.c lane_style_core.h lane_style_rknn.c lane_style_rknn.h lane_style_tensor.h \
    lane_style_async.c lane_style_async.h build_hdmi_lane.sh; do
    cp -- "$LANE_ROOT/$filename" "$LANE_WORK/$filename"
done
cp -- "$LANE_ROOT/tests/lane_style_core_test.c" "$LANE_WORK/tests/"
cp -- "$LANE_ROOT/tests/lane_style_tensor_test.c" "$LANE_WORK/tests/"
cp -- "$LANE_ROOT/tests/lane_style_decode_test.c" "$LANE_WORK/tests/"
cp -- "$LANE_ROOT/tests/lane_style_async_test.c" "$LANE_WORK/tests/"
cp -- "$LANE_ROOT/tests/lane_style_diagnostics_test.c" "$LANE_WORK/tests/"
LANE_HEADER="${RKNN_HEADER_DIR:-$LANE_REPORT}"
cp -- "$LANE_HEADER/rknn_api.h" "$LANE_WORK/rk3568_lane_deploy/rknn_api.h"
LANE_BUILD_DIR="$LANE_WORK/rk3568_lane_deploy" RKNN_HEADER_DIR="$LANE_WORK/rk3568_lane_deploy" bash "$LANE_WORK/build_hdmi_lane.sh"
cp -- "$LANE_WORK/rk3568_lane_deploy/pcie_multivideo_server.lane_v1" "$LANE_REPORT/"
bash "$LANE_ROOT/deploy_hdmi_lane_rk3568.sh" inspect
