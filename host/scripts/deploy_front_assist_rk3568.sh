#!/usr/bin/env bash
set -euo pipefail
FRONT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-prepare}" == rollback ]]; then
    export SYNC_PROFILE=front_v1
    exec bash "$FRONT_ROOT/deploy_sync_control_rk3568.sh" rollback
fi
export SYNC_PROFILE=front_v1
exec bash "$FRONT_ROOT/deploy_sync_control_rk3568.sh" "${1:-prepare}"
