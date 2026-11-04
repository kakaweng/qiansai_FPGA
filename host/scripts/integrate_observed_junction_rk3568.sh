#!/usr/bin/env bash
set -euo pipefail
JUNCTION_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
JUNCTION_OUTPUT="$JUNCTION_ROOT/rk3568_crosswalk_deploy"
JUNCTION_TOOLKIT="${LANE_TOOLKIT_VENV:-$HOME/.venvs/rknn-lane-2.3.2}"
mkdir -p "$JUNCTION_OUTPUT"
exec > >(tee "$JUNCTION_OUTPUT/integrate.log") 2>&1
trap 'printf "OBSERVED_JUNCTION_FAILED line=%s; see rk3568_crosswalk_deploy/integrate.log\n" "$LINENO"' ERR
printf 'Integrating measured lanes, Mapillary crosswalks and pedestrian relations; no retraining.\n'
for JUNCTION_SCRIPT in run_sync_board.sh deploy_sync_control_rk3568.sh preview_front_p1.sh; do
    bash -n "$JUNCTION_ROOT/$JUNCTION_SCRIPT"
done
if ! python3 "$JUNCTION_ROOT/verify_crosswalk_artifacts.py" "$JUNCTION_OUTPUT" 2>/dev/null; then
    if [[ ! -x "$JUNCTION_TOOLKIT/bin/python" ]]; then
        printf 'Existing RKNN conversion environment missing: %s\n' "$JUNCTION_TOOLKIT" >&2
        exit 1
    fi
    "$JUNCTION_TOOLKIT/bin/python" "$JUNCTION_ROOT/training/convert_crosswalk_rknn.py" \
        --output "$JUNCTION_OUTPUT/crosswalk.rknn"
fi
python3 "$JUNCTION_ROOT/verify_crosswalk_artifacts.py" "$JUNCTION_OUTPUT"
bash "$JUNCTION_ROOT/test_front_p1.sh"
bash "$JUNCTION_ROOT/deploy_pie_anticipation_rk3568.sh" prepare
JUNCTION_HDC="${HDC:-$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc}"
if [[ ! -x "$JUNCTION_HDC" ]]; then
    for JUNCTION_CANDIDATE in /home/*/openharmony/prebuilts/ohos-sdk/linux/*/toolchains/hdc; do
        if [[ -x "$JUNCTION_CANDIDATE" ]]; then JUNCTION_HDC="$JUNCTION_CANDIDATE"; break; fi
    done
fi
test -x "$JUNCTION_HDC"
export HDC="$JUNCTION_HDC"
JUNCTION_TARGET="${TARGET:-7001005458323933328a26cf4db23900}"
JUNCTION_BDF="${FPGA_BDF:-0002:21:00.0}"
[[ "$JUNCTION_BDF" =~ ^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$ ]]
sudo -v
JUNCTION_DEVICE="$(sudo -n "$HDC" -t "$JUNCTION_TARGET" shell \
    "if [ ! -d /sys/module/pango_pcie_multivideo ]; then insmod /data/local/tmp/pango_pcie_multivideo.ko profile=1 cached_dma=1 || exit 1; fi; test -c /dev/pango_pcie_multivideo_$JUNCTION_BDF && echo OBSERVED_JUNCTION_DEVICE_READY")"
printf '%s\n' "$JUNCTION_DEVICE"
if ! printf '%s\n' "$JUNCTION_DEVICE" | grep -q '^OBSERVED_JUNCTION_DEVICE_READY'; then
    printf 'RK3568 or FPGA HDMI device is not ready; service was not switched.\n' >&2
    exit 1
fi
for JUNCTION_FILE in crosswalk.rknn crosswalk.contract crosswalk.manifest.json; do
    sudo -n "$HDC" -t "$JUNCTION_TARGET" file send "$JUNCTION_OUTPUT/$JUNCTION_FILE" \
        "/data/local/tmp/hdmi_lane_v1/$JUNCTION_FILE.upload"
    sudo -n "$HDC" -t "$JUNCTION_TARGET" shell mv -f \
        "/data/local/tmp/hdmi_lane_v1/$JUNCTION_FILE.upload" "/data/local/tmp/hdmi_lane_v1/$JUNCTION_FILE"
done
bash "$JUNCTION_ROOT/deploy_pie_anticipation_rk3568.sh" install
bash "$JUNCTION_ROOT/deploy_pie_anticipation_rk3568.sh" start
python3 - <<'PY'
import json
from urllib.request import build_opener, ProxyHandler
with build_opener(ProxyHandler({})).open('http://127.0.0.1:9011/api/status', timeout=5) as response:
    state = json.load(response)
if (state.get('pedestrian', {}).get('policy') != 'crosswalk-contact-v1' or
        state.get('drive', {}).get('p1', {}).get('people_basis') != 'crosswalk_contact'):
    raise SystemExit('ERROR: active controller does not provide the new pedestrian policy')
print('PEDESTRIAN_POLICY_PASS policy=crosswalk-contact-v1')
PY
printf '\nOBSERVED_JUNCTION_PASS\nPreview: bash /mnt/hgfs/pcie_host/preview_pie_anticipation.sh\n'
