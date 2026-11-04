#!/usr/bin/env bash
set -euo pipefail
FRONT_P1_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export SYNC_PROFILE=front_p1
exec bash "$FRONT_P1_ROOT/deploy_sync_control_rk3568.sh" "$@"
