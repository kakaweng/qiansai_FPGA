#!/usr/bin/env bash
set -euo pipefail
SYNC_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SYNC_ACTION="${1:-prepare}"
SYNC_WORK="$HOME/pcie_sync_rk3568"
SYNC_OUTPUT="$SYNC_ROOT/rk3568_sync_deploy"
SYNC_HDC="${HDC:-$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc}"
SYNC_TARGET="${TARGET:-7001005458323933328a26cf4db23900}"
SYNC_BDF="${FPGA_BDF:-0002:21:00.0}"
SYNC_BOARD=/data/local/tmp/hdmi_lane_v1
SYNC_SERVER=pcie_multivideo_server.scene_sync_v5
SYNC_CONTROLLER=rk_sync_control
SYNC_REMOTE_SCRIPT=run_sync_board.sh
SYNC_CONTROL_FLAGS=()
if [[ "${SYNC_PROFILE:-sync_v5}" == drive_v6 ]]; then
    SYNC_SERVER=pcie_multivideo_server.drive_v6
    SYNC_CONTROLLER=rk_drive_control
    SYNC_REMOTE_SCRIPT=run_drive_board.sh
    SYNC_CONTROL_FLAGS+=(-DHAVE_DRIVE_ASSIST "$SYNC_WORK/drive_assist.c")
    SYNC_OUTPUT="$SYNC_ROOT/rk3568_drive_deploy"
elif [[ "${SYNC_PROFILE:-sync_v5}" == front_v1 ]]; then
    SYNC_SERVER=pcie_multivideo_server.front_v1
    SYNC_CONTROLLER=rk_front_control
    SYNC_REMOTE_SCRIPT=run_front_board.sh
    SYNC_CONTROL_FLAGS+=(-DFRONT_ONLY -DHAVE_DRIVE_ASSIST "$SYNC_WORK/drive_assist.c")
    SYNC_OUTPUT="$SYNC_ROOT/rk3568_front_deploy"
elif [[ "${SYNC_PROFILE:-sync_v5}" == front_p1 ]]; then
    SYNC_SERVER=pcie_multivideo_server.front_p1
    SYNC_CONTROLLER=rk_front_p1_control
    SYNC_REMOTE_SCRIPT=run_front_p1_board.sh
    SYNC_CONTROL_FLAGS+=(-DFRONT_ONLY -DHAVE_DRIVE_ASSIST -DHAVE_FRONT_P1 "$SYNC_WORK/drive_assist.c" "$SYNC_WORK/front_p1.c")
    SYNC_OUTPUT="$SYNC_ROOT/rk3568_front_p1_deploy"
elif [[ "${SYNC_PROFILE:-sync_v5}" == front_pie ]]; then
    SYNC_SERVER=pcie_multivideo_server.front_p1
    SYNC_CONTROLLER=rk_front_pie_control
    SYNC_REMOTE_SCRIPT=run_front_pie_board.sh
    SYNC_CONTROL_FLAGS+=(-DFRONT_ONLY -DHAVE_DRIVE_ASSIST -DHAVE_FRONT_P1 -DHAVE_PIE_ANTICIPATION -DHAVE_CROSSWALK "$SYNC_WORK/drive_assist.c" "$SYNC_WORK/front_p1.c" "$SYNC_WORK/pie_anticipation_core.c" -lm)
    SYNC_OUTPUT="$SYNC_ROOT/rk3568_pie_deploy"
fi
case "$SYNC_ACTION" in prepare|install|start|log|rollback) ;; *) printf 'Usage: bash deploy_sync_control_rk3568.sh prepare|install|start|log|rollback\n' >&2; exit 1 ;; esac
[[ "$SYNC_BDF" =~ ^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$ ]]
mkdir -p "$SYNC_OUTPUT"
exec > >(tee "$SYNC_OUTPUT/${SYNC_ACTION}.log") 2>&1
hdc_sync() { sudo -n "$SYNC_HDC" -t "$SYNC_TARGET" "$@"; }
sync_upload_scripts()
{
    local SYNC_SCRIPT
    for SYNC_SCRIPT in sync_controller_processes.sh run_sync_board.sh run_drive_board.sh run_front_board.sh run_front_p1_board.sh run_front_pie_board.sh; do
        hdc_sync file send "$SYNC_ROOT/$SYNC_SCRIPT" "$SYNC_BOARD/$SYNC_SCRIPT.upload"
        hdc_sync shell mv -f "$SYNC_BOARD/$SYNC_SCRIPT.upload" "$SYNC_BOARD/$SYNC_SCRIPT"
    done
}
sync_remote_check()
{
    local SYNC_MODE="$1" SYNC_MARKER="$2" SYNC_RESPONSE
    SYNC_RESPONSE="$(mktemp)"
    if ! hdc_sync shell sh "$SYNC_BOARD/$SYNC_REMOTE_SCRIPT" "$SYNC_MODE" "$SYNC_BDF" | tee "$SYNC_RESPONSE"; then
        rm -f -- "$SYNC_RESPONSE"; return 1
    fi
    if ! grep -q "^$SYNC_MARKER" "$SYNC_RESPONSE"; then
        rm -f -- "$SYNC_RESPONSE"; printf 'ERROR: missing board pass marker %s\n' "$SYNC_MARKER" >&2; return 1
    fi
    if [[ "${SYNC_PROFILE:-sync_v5}" == front_pie && ( "$SYNC_MODE" == inspect || "$SYNC_MODE" == start ) ]] &&
        ! grep -qx 'CROSSWALK_INSPECT_PASS' "$SYNC_RESPONSE"; then
        rm -f -- "$SYNC_RESPONSE"; printf 'ERROR: missing board crosswalk inspection; observed-junction deployment is incomplete.\n' >&2; return 1
    fi
    rm -f -- "$SYNC_RESPONSE"
}
sync_forward()
{
    local SYNC_LOCAL="$1" SYNC_REMOTE="$2" SYNC_FORWARDS
    SYNC_FORWARDS="$(hdc_sync fport ls)"
    if ! printf '%s\n' "$SYNC_FORWARDS" | grep -E "tcp:$SYNC_LOCAL[[:space:]]+tcp:$SYNC_REMOTE" >/dev/null; then
        if [[ -n "$(ss -H -ltn "sport = :$SYNC_LOCAL")" ]]; then
            printf 'ERROR: Ubuntu port %s occupied; check hdc fport ls.\n' "$SYNC_LOCAL" >&2; exit 1
        fi
        hdc_sync fport "tcp:$SYNC_LOCAL" "tcp:$SYNC_REMOTE"
    fi
}
if [[ "$SYNC_ACTION" == prepare ]]; then
    mkdir -p "$SYNC_WORK/tests" "$SYNC_WORK/rk3568_lane_deploy"
    # Explicit source-only copies: no model conversion, pip install or FPGA changes.
    cp -- "$SYNC_ROOT"/*.c "$SYNC_ROOT"/*.h "$SYNC_ROOT/build_hdmi_lane.sh" "$SYNC_WORK/"
    cp -- "$SYNC_ROOT"/tests/*_test.c "$SYNC_WORK/tests/"
    if [[ "${SYNC_PROFILE:-sync_v5}" == front_pie ]]; then
        cp -- "$SYNC_ROOT/tests/pie_anticipation_parity.h" "$SYNC_WORK/tests/"
    fi
    cp -- "$SYNC_ROOT/rk3568_lane_deploy/rknn_api.h" "$SYNC_WORK/rk3568_lane_deploy/"
    LANE_BUILD_DIR="$SYNC_WORK/rk3568_sync_deploy" LANE_SERVER_NAME="$SYNC_SERVER" \
        bash "$SYNC_WORK/build_hdmi_lane.sh"
    gcc -O2 -std=c11 -Wall -Wextra -Werror "$SYNC_WORK/tests/sync_receiver_test.c" "$SYNC_WORK/sync_receiver_core.c" \
        -o "$SYNC_WORK/rk3568_sync_deploy/sync_receiver_test"
    "$SYNC_WORK/rk3568_sync_deploy/sync_receiver_test"
    "${RKNN_CC:-aarch64-linux-gnu-gcc}" -O2 -std=c11 -Wall -Wextra -Werror "$SYNC_WORK/rk_sync_control.c" "${SYNC_CONTROL_FLAGS[@]}" \
        -o "$SYNC_WORK/rk3568_sync_deploy/$SYNC_CONTROLLER"
    cp -- "$SYNC_WORK/rk3568_sync_deploy/$SYNC_SERVER" "$SYNC_OUTPUT/"
    cp -- "$SYNC_WORK/rk3568_sync_deploy/$SYNC_CONTROLLER" "$SYNC_OUTPUT/"
    if [[ "${SYNC_PROFILE:-sync_v5}" == front_pie ]]; then
        for SYNC_PIE_TEST in pie_anticipation_core_test pie_anticipation_parity_test; do
            gcc -O2 -UNDEBUG -std=c11 -Wall -Wextra -Werror "$SYNC_WORK/tests/$SYNC_PIE_TEST.c" \
                "$SYNC_WORK/pie_anticipation_core.c" -lm -o "$SYNC_WORK/rk3568_sync_deploy/$SYNC_PIE_TEST"
            "$SYNC_WORK/rk3568_sync_deploy/$SYNC_PIE_TEST"
        done
        gcc -O2 -std=c11 -Wall -Wextra -Werror "$SYNC_WORK/rk_sync_control.c" "${SYNC_CONTROL_FLAGS[@]}" \
            -o "$SYNC_WORK/rk3568_sync_deploy/rk_front_pie_control_test"
        FRONT_P1_TEST_CONTROLLER="$SYNC_WORK/rk3568_sync_deploy/rk_front_pie_control_test" \
            python3 "$SYNC_ROOT/tests/test_pie_controller.py"
        "${RKNN_CC:-aarch64-linux-gnu-gcc}" -O2 -UNDEBUG -std=c11 -Wall -Wextra -Werror \
            "$SYNC_WORK/tests/pie_anticipation_parity_test.c" "$SYNC_WORK/pie_anticipation_core.c" \
            -lm -o "$SYNC_OUTPUT/pie_anticipation_parity_test"
    fi
    printf 'SYNC_PREPARE_PASS; existing board service unchanged.\n'
    exit 0
fi
sudo -n true
if [[ "$SYNC_ACTION" == install ]]; then
    SYNC_INSTALL_FILES=("$SYNC_SERVER" "$SYNC_CONTROLLER")
    if [[ "${SYNC_PROFILE:-sync_v5}" == front_pie ]]; then
        SYNC_INSTALL_FILES+=(pie_anticipation_parity_test)
    fi
    for SYNC_FILE in "${SYNC_INSTALL_FILES[@]}"; do
        test -s "$SYNC_OUTPUT/$SYNC_FILE"
        hdc_sync file send "$SYNC_OUTPUT/$SYNC_FILE" "$SYNC_BOARD/$SYNC_FILE.upload"
        hdc_sync shell chmod 755 "$SYNC_BOARD/$SYNC_FILE.upload"
        hdc_sync shell mv -f "$SYNC_BOARD/$SYNC_FILE.upload" "$SYNC_BOARD/$SYNC_FILE"
    done
    sync_upload_scripts
    sync_remote_check inspect LANE_INSPECT_PASS
    printf 'SYNC_INSTALL_PASS; model reused; existing service unchanged.\n'
elif [[ "$SYNC_ACTION" == start ]]; then
    # Refresh lifecycle fixes independently of compiled models/executables.
    sync_upload_scripts
    if [[ "${SYNC_PROFILE:-sync_v5}" == front_* ]]; then
        sync_remote_check start FRONT_BOARD_START_PASS
    else
        sync_remote_check start SYNC_BOARD_START_PASS
    fi
    sync_forward 9011 9010
    sync_forward 9004 9002
    printf 'SYNC_START_PASS\n'
    if [[ "${SYNC_PROFILE:-sync_v5}" != front_p1 && "${SYNC_PROFILE:-sync_v5}" != front_pie ]]; then
        printf 'Open panel in Ubuntu browser: http://127.0.0.1:9011\n'
    fi
    if [[ "${SYNC_PROFILE:-sync_v5}" == drive_v6 ]]; then
        printf 'Unified preview: bash /mnt/hgfs/pcie_host/preview_drive_assist.sh\n'
    elif [[ "${SYNC_PROFILE:-sync_v5}" == front_v1 ]]; then
        printf 'Front-only preview: bash /mnt/hgfs/pcie_host/preview_front_assist.sh\n'
    elif [[ "${SYNC_PROFILE:-sync_v5}" == front_pie ]]; then
        printf 'PIE preview: bash /mnt/hgfs/pcie_host/preview_pie_anticipation.sh\n'
    elif [[ "${SYNC_PROFILE:-sync_v5}" == front_p1 ]]; then
        printf 'P1 preview: bash /mnt/hgfs/pcie_host/preview_front_p1.sh\n'
    else printf 'Keep preview running: bash /mnt/hgfs/pcie_host/preview_hdmi_lane_fast.sh\n'; fi
    if [[ "${SYNC_PROFILE:-sync_v5}" != front_* ]]; then
        printf 'Relay in another terminal: python3 /mnt/hgfs/pcie_host/sync_control_relay.py\n'
    fi
elif [[ "$SYNC_ACTION" == rollback ]]; then
    sync_upload_scripts
    hdc_sync shell sh "$SYNC_BOARD/run_sync_board.sh" stop-controller "$SYNC_BDF"
    if [[ "${SYNC_PROFILE:-sync_v5}" == front_v1 ]]; then
        bash "$SYNC_ROOT/deploy_hdmi_lane_rk3568.sh" start-scene
    else
        bash "$SYNC_ROOT/deploy_hdmi_lane_rk3568.sh" start-scene
    fi
else
    sync_upload_scripts
    hdc_sync shell sh "$SYNC_BOARD/$SYNC_REMOTE_SCRIPT" log "$SYNC_BDF"
fi
