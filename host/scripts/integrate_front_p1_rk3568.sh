#!/usr/bin/env bash
set -euo pipefail
FRONT_P1_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FRONT_P1_WORK="${LANE_UBUNTU_WORK:-$HOME/pcie_lane_rk3568}"
mkdir -p "$FRONT_P1_ROOT/rk3568_front_p1_deploy" "$FRONT_P1_WORK"
exec > >(tee "$FRONT_P1_ROOT/rk3568_front_p1_deploy/integrate.log") 2>&1
trap 'printf "FRONT_P1_FAILED line=%s; see rk3568_front_p1_deploy/integrate.log\n" "$LINENO"' ERR
printf 'Preparing HDMI P1 with live road mapping and junction scene, existing models and A1 lane rules.\n'
bash "$FRONT_P1_ROOT/test_front_p1.sh"
if [[ "$FRONT_P1_ROOT" != "$FRONT_P1_WORK" ]]; then
    for FRONT_P1_FILE in build_checksum.sh frame_checksum.c frame_to_png.py frame_format.py; do
        cp -- "$FRONT_P1_ROOT/$FRONT_P1_FILE" "$FRONT_P1_WORK/$FRONT_P1_FILE"
    done
fi
bash "$FRONT_P1_WORK/build_checksum.sh"
bash "$FRONT_P1_ROOT/deploy_front_p1_rk3568.sh" prepare
bash "$FRONT_P1_ROOT/deploy_front_p1_rk3568.sh" install
bash "$FRONT_P1_ROOT/deploy_front_p1_rk3568.sh" start
printf '\nFRONT_P1_PASS\nPreview: bash /mnt/hgfs/pcie_host/preview_front_p1.sh\n'
