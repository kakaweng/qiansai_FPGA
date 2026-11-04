#!/usr/bin/env bash
set -euo pipefail
THREE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
THREE_ACTION="${1:-start}"
if [[ "$THREE_ACTION" == start-junction || "$THREE_ACTION" == junction-log ]]; then
    [[ "$THREE_ACTION" != start-junction ]] || exec bash "$THREE_ROOT/deploy_junction_live_rk3568.sh" start
    exec bash "$THREE_ROOT/deploy_junction_live_rk3568.sh" log
fi
THREE_HDC="${HDC:-$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc}"
THREE_TARGET="${TARGET:-7001005458323933328a26cf4db23900}"
THREE_BDF="${FPGA_BDF:-0002:21:00.0}"
THREE_TIMEOUT="${THREE_SEND_TIMEOUT_MS:-2000}"
THREE_BOARD=/data/local/tmp/three_video_display
case "$THREE_ACTION" in start|stop|log|restore-front) ;; *) printf 'Usage: bash deploy_three_video_rk3568.sh start|start-junction|junction-log|stop|log|restore-front\n' >&2; exit 1 ;; esac
if [[ "$THREE_ACTION" == restore-front ]]; then
    exec env SYNC_PROFILE=front_pie bash "$THREE_ROOT/deploy_sync_control_rk3568.sh" start
fi
[[ "$THREE_BDF" =~ ^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$ ]]
[[ "$THREE_TIMEOUT" =~ ^[0-9]+$ && "$THREE_TIMEOUT" -ge 100 && "$THREE_TIMEOUT" -le 30000 ]]
test -x "$THREE_HDC"
sudo -n true
hdc_three() { sudo -n "$THREE_HDC" -t "$THREE_TARGET" "$@"; }
mkdir -p "$THREE_ROOT/rk3568_three_video_deploy"
exec > >(tee "$THREE_ROOT/rk3568_three_video_deploy/$THREE_ACTION.log") 2>&1
if [[ "$THREE_ACTION" == start ]]; then
    # Check viewer dependencies before switching the working board service.
    bash "$THREE_ROOT/preview_three_video.sh" --prepare-only
    python3 - "$THREE_ROOT/pcie_multivideo_server.ethernet_v6" <<'PY'
import hashlib, sys
from pathlib import Path
actual = hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest()
if actual != 'd28439094128866ac04273379fddc9ac1c817c1c9d3426f07d25b01787dd99a8':
    raise SystemExit('Historical transport binary SHA256 mismatch; refusing to replace the board service.')
print('THREE_VIDEO_ARTIFACT_PASS version=multivideo-ethernet-v6')
PY
    hdc_three shell mkdir -p "$THREE_BOARD"
    THREE_TMP="$(mktemp -d)"
    trap 'rm -f -- "$THREE_TMP/returned" "$THREE_TMP/response"; rmdir -- "$THREE_TMP"' EXIT
    # Board toybox lacks sha256sum: read each upload back and verify on Ubuntu.
    for THREE_FILE in pcie_multivideo_server.ethernet_v6 run_three_video_board.sh stop_multivideo_server.sh sync_controller_processes.sh; do
        hdc_three file send "$THREE_ROOT/$THREE_FILE" "$THREE_BOARD/$THREE_FILE.upload"
        hdc_three file recv "$THREE_BOARD/$THREE_FILE.upload" "$THREE_TMP/returned"
        python3 - "$THREE_ROOT/$THREE_FILE" "$THREE_TMP/returned" <<'PY'
import hashlib, sys
from pathlib import Path
if hashlib.sha256(Path(sys.argv[1]).read_bytes()).digest() != hashlib.sha256(Path(sys.argv[2]).read_bytes()).digest():
    raise SystemExit('Board upload verification failed: '+sys.argv[1])
PY
        rm -f -- "$THREE_TMP/returned"
        hdc_three shell chmod 755 "$THREE_BOARD/$THREE_FILE.upload"
        hdc_three shell mv -f "$THREE_BOARD/$THREE_FILE.upload" "$THREE_BOARD/$THREE_FILE"
    done
    THREE_FORWARDS="$(hdc_three fport ls)"
    if ! printf '%s\n' "$THREE_FORWARDS" | grep -E "^$THREE_TARGET[[:space:]]+tcp:9004[[:space:]]+tcp:9002([[:space:]]|$)" >/dev/null; then
        if [[ -n "$(ss -H -ltn 'sport = :9004')" ]]; then
            printf 'ERROR: Ubuntu port 9004 is occupied; check hdc fport ls before switching.\n' >&2
            exit 1
        fi
        hdc_three fport tcp:9004 tcp:9002
    fi
else
    THREE_TMP="$(mktemp -d)"
    trap 'rm -f -- "$THREE_TMP/response"; rmdir -- "$THREE_TMP"' EXIT
fi
hdc_three shell sh "$THREE_BOARD/run_three_video_board.sh" "$THREE_ACTION" "$THREE_BDF" "$THREE_TIMEOUT" | tee "$THREE_TMP/response"
case "$THREE_ACTION" in start) THREE_MARKER=THREE_VIDEO_BOARD_START_PASS ;; stop) THREE_MARKER=THREE_VIDEO_STOP_PASS ;; log) THREE_MARKER=THREE_VIDEO_LOG_PASS ;; esac
if ! grep -qx "$THREE_MARKER" "$THREE_TMP/response"; then
    printf 'ERROR: missing board pass marker %s\n' "$THREE_MARKER" >&2
    exit 1
fi
printf 'THREE_VIDEO_%s_PASS\n' "${THREE_ACTION^^}"
if [[ "$THREE_ACTION" == start ]]; then
    printf 'Open: bash /mnt/hgfs/pcie_host/preview_three_video.sh\n'
    printf 'Return to front assist: sudo -v && bash /mnt/hgfs/pcie_host/deploy_three_video_rk3568.sh restore-front\n'
fi
