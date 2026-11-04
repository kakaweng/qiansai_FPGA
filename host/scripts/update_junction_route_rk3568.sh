#!/usr/bin/env bash
set -euo pipefail
ROUTE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$ROUTE_ROOT/rk3568_pie_deploy"
exec > >(tee "$ROUTE_ROOT/rk3568_pie_deploy/update_route.log") 2>&1
trap 'printf "JUNCTION_ROUTE_FAILED line=%s; see rk3568_pie_deploy/update_route.log\n" "$LINENO"' ERR
printf 'Updating route-aware rehearsal and bounded crosswalk gap matching; trained models reused.\n'
bash "$ROUTE_ROOT/test_front_p1.sh"
bash "$ROUTE_ROOT/deploy_pie_anticipation_rk3568.sh" prepare
bash "$ROUTE_ROOT/deploy_pie_anticipation_rk3568.sh" install
bash "$ROUTE_ROOT/deploy_pie_anticipation_rk3568.sh" start
printf '\nJUNCTION_ROUTE_PASS\n'
printf 'Open preview: bash /mnt/hgfs/pcie_host/preview_pie_anticipation.sh\n'
