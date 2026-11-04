#!/usr/bin/env bash
set -euo pipefail
DRIVE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-prepare}" == rollback ]]; then
    # Return to the previously installed, independently named v5 artifacts.
    unset SYNC_PROFILE
    exec bash "$DRIVE_ROOT/deploy_sync_control_rk3568.sh" start
fi
export SYNC_PROFILE=drive_v6
exec bash "$DRIVE_ROOT/deploy_sync_control_rk3568.sh" "${1:-prepare}"
