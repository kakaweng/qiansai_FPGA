#!/usr/bin/env bash
set -euo pipefail
LANE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LANE_REPORT="$LANE_ROOT/rk3568_lane_deploy"
LANE_ACTION="${1:-inspect}"
case "$LANE_ACTION" in inspect|inspect-fast|inspect-diag|inspect-scene|start|start-fast|start-diag|start-scene|log|rollback) ;; *) printf 'Usage: bash deploy_hdmi_lane_rk3568.sh inspect|inspect-fast|inspect-diag|inspect-scene|start|start-fast|start-diag|start-scene|log|rollback\n' >&2; exit 1 ;; esac
mkdir -p "$LANE_REPORT"
exec > >(tee "$LANE_REPORT/deploy_${LANE_ACTION}.log") 2>&1
LANE_HDC="${HDC:-$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc}"
LANE_TARGET="${TARGET:-7001005458323933328a26cf4db23900}"
LANE_BDF="${FPGA_BDF:-0002:21:00.0}"
LANE_BOARD=/data/local/tmp/hdmi_lane_v1
[[ "$LANE_BDF" =~ ^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$ ]]
sudo -n true
hdc_lane() { sudo -n "$LANE_HDC" -t "$LANE_TARGET" "$@"; }
if [[ "$LANE_ACTION" == inspect || "$LANE_ACTION" == inspect-fast || "$LANE_ACTION" == inspect-diag || "$LANE_ACTION" == inspect-scene ]]; then
    python3 - "$LANE_REPORT" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
root = Path(sys.argv[1])
manifest = json.loads((root / "lane_style.manifest.json").read_text(encoding="utf-8"))
assert manifest["target"] == "rk3568" and manifest["weights_epoch"] == 24
assert manifest["decoder_version"] == "lane-style-curves-c-v1"
assert hashlib.sha256((root / "lane_style.rknn").read_bytes()).hexdigest() == manifest["rknn_sha256"]
assert hashlib.sha256((root / "lane_style.contract").read_bytes()).hexdigest() == manifest["contract_sha256"]
assert len(manifest["simulator_parity"]) == 3
print("PASS: artifact SHA256 and model contract")
PY
    hdc_lane shell mkdir -p "$LANE_BOARD"
    LANE_EXECUTABLE=pcie_multivideo_server.lane_v1
    if [[ "$LANE_ACTION" == inspect-fast ]]; then LANE_EXECUTABLE=pcie_multivideo_server.lane_v2; fi
    if [[ "$LANE_ACTION" == inspect-diag ]]; then LANE_EXECUTABLE=pcie_multivideo_server.lane_v3; fi
    if [[ "$LANE_ACTION" == inspect-scene ]]; then
        python3 "$LANE_ROOT/record_hdmi_scene_contract.py" --verify "$LANE_ROOT/rk3568_scene_deploy"
        LANE_SIGNAL_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["signal"]["version"])' \
            "$LANE_ROOT/rk3568_scene_deploy/road_objects.manifest.json")"
        LANE_EXECUTABLE=pcie_multivideo_server.scene_v4
        # Replace the pathname atomically, preserving the inode used by the
        # currently running scene service while the new executable is checked.
        hdc_lane file send "$LANE_ROOT/rk3568_scene_deploy/$LANE_EXECUTABLE" "$LANE_BOARD/$LANE_EXECUTABLE.upload"
        hdc_lane shell chmod 755 "$LANE_BOARD/$LANE_EXECUTABLE.upload"
        hdc_lane shell mv -f "$LANE_BOARD/$LANE_EXECUTABLE.upload" "$LANE_BOARD/$LANE_EXECUTABLE"
        for filename in road_objects.rknn road_objects.contract road_objects.manifest.json; do
            hdc_lane file send "$LANE_ROOT/rk3568_scene_deploy/$filename" "$LANE_BOARD/$filename"
        done
    else
        hdc_lane file send "$LANE_REPORT/$LANE_EXECUTABLE" "$LANE_BOARD/$LANE_EXECUTABLE"
    fi
    for filename in lane_style.rknn lane_style.contract lane_style.manifest.json; do
        hdc_lane file send "$LANE_REPORT/$filename" "$LANE_BOARD/$filename"
    done
    hdc_lane file send "$LANE_ROOT/run_hdmi_lane_board.sh" "$LANE_BOARD/run_hdmi_lane_board.sh"
    hdc_lane file send "$LANE_ROOT/stop_multivideo_server.sh" "$LANE_BOARD/stop_multivideo_server.sh"
    hdc_lane shell chmod 755 "$LANE_BOARD/$LANE_EXECUTABLE"
fi
LANE_RESPONSE="$(mktemp)"
trap 'rm -f -- "$LANE_RESPONSE"' EXIT
hdc_lane shell sh "$LANE_BOARD/run_hdmi_lane_board.sh" "$LANE_ACTION" "$LANE_BDF" | tee "$LANE_RESPONSE"
case "$LANE_ACTION" in
    inspect|inspect-fast|inspect-diag) grep -q '^LANE_INSPECT_PASS' "$LANE_RESPONSE" ;;
    inspect-scene)
        grep -q '^LANE_INSPECT_PASS' "$LANE_RESPONSE"
        grep '^OBJECT_INSPECT_PASS' "$LANE_RESPONSE" | grep -Fq "signal=$LANE_SIGNAL_VERSION "
        ;;
    start|start-fast|start-diag|start-scene) grep -q '^LANE_START_PASS' "$LANE_RESPONSE" ;;
    rollback) grep -q '^LANE_ROLLBACK_STARTED' "$LANE_RESPONSE" ;;
esac
if [[ "$LANE_ACTION" == start || "$LANE_ACTION" == start-fast || "$LANE_ACTION" == start-diag || "$LANE_ACTION" == start-scene ]]; then
    LANE_FORWARDINGS="$(hdc_lane fport ls)"
    printf '%s\n' "$LANE_FORWARDINGS"
    if ! printf '%s\n' "$LANE_FORWARDINGS" | grep -E 'tcp:9004[[:space:]]+tcp:9002' >/dev/null; then
        if [[ -n "$(ss -H -ltn 'sport = :9004')" ]]; then
            printf 'Ubuntu 9004 is occupied; inspect the forwarding before opening preview.\n' >&2
            exit 1
        fi
        hdc_lane fport tcp:9004 tcp:9002
    fi
    printf 'Open preview in another Ubuntu terminal:\npython3 /mnt/hgfs/pcie_host/preview_multivideo.py --host 127.0.0.1 --port 9004 --timeout 60\n'
fi
printf 'DEPLOY_%s_PASS\n' "$LANE_ACTION"
