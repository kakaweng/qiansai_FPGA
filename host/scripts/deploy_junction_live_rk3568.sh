#!/usr/bin/env bash
set -euo pipefail
JUNCTION_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
JUNCTION_ACTION="${1:-start}"
JUNCTION_HDC="${HDC:-$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc}"
JUNCTION_TARGET="${TARGET:-7001005458323933328a26cf4db23900}"
JUNCTION_BDF="${FPGA_BDF:-0002:21:00.0}"
JUNCTION_BOARD=/data/local/tmp/waymo_junction_live
case "$JUNCTION_ACTION" in start|stop|log|inspect) ;; *) echo 'Usage: start|stop|log|inspect'; exit 1 ;; esac
[[ "$JUNCTION_BDF" =~ ^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$ ]]
test -x "$JUNCTION_HDC"
sudo -n true
mkdir -p "$JUNCTION_ROOT/rk3568_junction_live_deploy"
exec > >(tee "$JUNCTION_ROOT/rk3568_junction_live_deploy/$JUNCTION_ACTION.log") 2>&1
trap 'printf "JUNCTION_DEPLOY_FAILED line=%s\n" "$LINENO" >&2' ERR
junction_hdc() { sudo -n "$JUNCTION_HDC" -t "$JUNCTION_TARGET" "$@"; }
JUNCTION_TMP="$(mktemp -d)"
trap 'rm -f -- "$JUNCTION_TMP/returned" "$JUNCTION_TMP/response"; rmdir -- "$JUNCTION_TMP"' EXIT
if [[ "$JUNCTION_ACTION" == start || "$JUNCTION_ACTION" == inspect ]]; then
    bash "$JUNCTION_ROOT/preview_junction_live.sh" --prepare-only
    python3 "$JUNCTION_ROOT/tests/test_junction_live.py"
    bash "$JUNCTION_ROOT/build_junction_live.sh"
    junction_hdc shell mkdir -p "$JUNCTION_BOARD"
    for JUNCTION_FILE in pcie_multivideo_server.junction_live run_junction_live_board.sh stop_multivideo_server.sh sync_controller_processes.sh; do
        JUNCTION_LOCAL="$JUNCTION_ROOT/$JUNCTION_FILE"
        [[ "$JUNCTION_FILE" != pcie_multivideo_server.junction_live ]] || JUNCTION_LOCAL="$JUNCTION_ROOT/rk3568_junction_live_deploy/$JUNCTION_FILE"
        junction_hdc file send "$JUNCTION_LOCAL" "$JUNCTION_BOARD/$JUNCTION_FILE.upload"
        junction_hdc file recv "$JUNCTION_BOARD/$JUNCTION_FILE.upload" "$JUNCTION_TMP/returned"
        cmp -- "$JUNCTION_LOCAL" "$JUNCTION_TMP/returned"
        rm -f -- "$JUNCTION_TMP/returned"
        junction_hdc shell chmod 755 "$JUNCTION_BOARD/$JUNCTION_FILE.upload"
        junction_hdc shell mv -f "$JUNCTION_BOARD/$JUNCTION_FILE.upload" "$JUNCTION_BOARD/$JUNCTION_FILE"
    done
fi
if [[ "$JUNCTION_ACTION" == start ]]; then
    JUNCTION_FORWARDS="$(junction_hdc fport ls)"
    for JUNCTION_PAIR in 9004:9002 9021:9020; do
        JUNCTION_LOCAL_PORT="${JUNCTION_PAIR%:*}"
        JUNCTION_BOARD_PORT="${JUNCTION_PAIR#*:}"
        if ! grep -Eq "^$JUNCTION_TARGET[[:space:]]+tcp:$JUNCTION_LOCAL_PORT[[:space:]]+tcp:$JUNCTION_BOARD_PORT([[:space:]]|$)" <<<"$JUNCTION_FORWARDS"; then
            if [[ -n "$(ss -H -ltn "sport = :$JUNCTION_LOCAL_PORT")" ]]; then
                printf 'ERROR: Ubuntu port %s occupied; existing service preserved.\n' "$JUNCTION_LOCAL_PORT"; exit 1
            fi
            junction_hdc fport "tcp:$JUNCTION_LOCAL_PORT" "tcp:$JUNCTION_BOARD_PORT"
        fi
    done
    # BAR capability reads require the capture driver to enable the PCIe device.
    # This preflight does not stop the working video service or change the bitstream.
    junction_hdc shell sh "$JUNCTION_BOARD/run_junction_live_board.sh" prepare "$JUNCTION_BDF" | tee "$JUNCTION_TMP/response"
    if ! grep -qx 'JUNCTION_BOARD_PREPARE_PASS' "$JUNCTION_TMP/response"; then
        echo 'ERROR: board PCIe preparation failed; key/LED checks and service switch were not attempted.' >&2
        exit 1
    fi
    bash "$JUNCTION_ROOT/deploy_fpga_junction_keys.sh"
    bash "$JUNCTION_ROOT/deploy_fpga_feedback.sh"
fi
junction_hdc shell sh "$JUNCTION_BOARD/run_junction_live_board.sh" "$JUNCTION_ACTION" "$JUNCTION_BDF" | tee "$JUNCTION_TMP/response"
grep -qx "JUNCTION_BOARD_${JUNCTION_ACTION^^}_PASS" "$JUNCTION_TMP/response"
printf 'JUNCTION_%s_PASS\n' "${JUNCTION_ACTION^^}"
if [[ "$JUNCTION_ACTION" == start ]]; then
    printf 'Open: bash /mnt/hgfs/pcie_host/preview_junction_live.sh\n'
    printf 'FPGA bitstream unchanged; original key/LED services reused.\n'
fi
