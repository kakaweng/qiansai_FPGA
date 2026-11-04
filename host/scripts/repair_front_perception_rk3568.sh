#!/usr/bin/env bash
set -euo pipefail
PERCEPTION_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$PERCEPTION_ROOT/rk3568_pie_deploy"
exec > >(tee "$PERCEPTION_ROOT/rk3568_pie_deploy/repair_perception.log") 2>&1
trap 'printf "FRONT_PERCEPTION_REPAIR_FAILED line=%s; see rk3568_pie_deploy/repair_perception.log\n" "$LINENO"' ERR
printf 'Repairing latest-frame YOLO, lane cadence and normal-view display history; trained models reused.\n'
bash "$PERCEPTION_ROOT/test_front_p1.sh"
bash "$PERCEPTION_ROOT/deploy_pie_anticipation_rk3568.sh" prepare
bash "$PERCEPTION_ROOT/deploy_pie_anticipation_rk3568.sh" install
bash "$PERCEPTION_ROOT/deploy_pie_anticipation_rk3568.sh" start
PERCEPTION_START_LOG="$PERCEPTION_ROOT/rk3568_pie_deploy/start.log"
for PERCEPTION_MARKER in \
    'multivideo-hdmi-front-observed-junction-v5-fresh' \
    'Objects scheduling: reserve-before-latest-v2' \
    'Lane cadence: analysis_gap_ms=1200'; do
    if ! grep -Fq "$PERCEPTION_MARKER" "$PERCEPTION_START_LOG"; then
        printf 'ERROR: active board program is missing marker: %s\n' "$PERCEPTION_MARKER" >&2
        exit 1
    fi
done
printf '\nFRONT_PERCEPTION_REPAIR_PASS\n'
printf 'Preview: bash /mnt/hgfs/pcie_host/preview_pie_anticipation.sh\n'
