#!/usr/bin/env bash
set -euo pipefail
DRIVE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
trap 'printf "DRIVE_INTEGRATE_FAILED line=%s; see rk3568_drive_deploy logs.\n" "$LINENO" >&2' ERR
sudo -n true
printf 'Integrating side vehicles and RK-owned Chinese driving panel; reusing existing models.\n'
printf 'Includes lane-priority-v1: reserve NPU before choosing newest frame; unchanged lane thresholds and expiry.\n'
bash "$DRIVE_ROOT/deploy_drive_assist_rk3568.sh" prepare
bash "$DRIVE_ROOT/deploy_drive_assist_rk3568.sh" install
bash "$DRIVE_ROOT/deploy_drive_assist_rk3568.sh" start
printf '\nDRIVE_ASSIST_PASS\nPanel: http://127.0.0.1:9011 (refresh browser)\n'
printf 'Unified preview: bash /mnt/hgfs/pcie_host/preview_drive_assist.sh\nRelay: python3 /mnt/hgfs/pcie_host/sync_control_relay.py\n'
