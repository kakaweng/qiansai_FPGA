#!/usr/bin/env bash
set -euo pipefail
PIE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-prepare}" == rollback ]]; then
    exec bash "$PIE_ROOT/deploy_front_p1_rk3568.sh" start
fi
export SYNC_PROFILE=front_pie
exec bash "$PIE_ROOT/deploy_sync_control_rk3568.sh" "$@"
