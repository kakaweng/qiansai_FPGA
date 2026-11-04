#!/usr/bin/env bash
set -euo pipefail
PRESENTATION_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
printf 'Updating completed junction rehearsal and display-only object history; reusing trained models.\n'
bash "$PRESENTATION_ROOT/integrate_observed_junction_rk3568.sh"
python3 - <<'PY'
import json
from urllib.request import build_opener, ProxyHandler
with build_opener(ProxyHandler({})).open('http://127.0.0.1:9011/api/status', timeout=5) as response:
    state = json.load(response)
if state.get('display_only') is not True or not isinstance(state.get('display_world'), dict):
    raise SystemExit('ERROR: active controller is missing display-only object history')
print('JUNCTION_PRESENTATION_PASS')
PY
printf 'Preview: bash /mnt/hgfs/pcie_host/preview_pie_anticipation.sh\n'
