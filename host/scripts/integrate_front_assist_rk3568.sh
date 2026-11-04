#!/usr/bin/env bash
set -euo pipefail
FRONT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
printf 'Integrating HDMI-only front assist; existing three-route profile remains available.\n'
bash "$FRONT_ROOT/deploy_front_assist_rk3568.sh" prepare
bash "$FRONT_ROOT/deploy_front_assist_rk3568.sh" install
bash "$FRONT_ROOT/deploy_front_assist_rk3568.sh" start
printf '\nFRONT_ASSIST_PASS\nPanel: http://127.0.0.1:9011\nPreview: bash /mnt/hgfs/pcie_host/preview_front_assist.sh\n'
