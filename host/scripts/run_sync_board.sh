#!/bin/sh
set -eu
SYNC_BOARD=/data/local/tmp/hdmi_lane_v1
SYNC_BASE=/data/local/tmp/rknn-yolov5-test
SYNC_LOADER="$SYNC_BASE/glibc/ld-linux-aarch64.so.1"
SYNC_LIB="$SYNC_BASE/lib:$SYNC_BASE/glibc"
SYNC_ACTION="${1:-status}"
SYNC_BDF="${2:-0002:21:00.0}"
SYNC_SERVER="${SYNC_SERVER:-pcie_multivideo_server.scene_sync_v5}"
SYNC_CONTROLLER="${SYNC_CONTROLLER:-rk_sync_control}"
case "$SYNC_SERVER:$SYNC_CONTROLLER" in pcie_multivideo_server.scene_sync_v5:rk_sync_control|pcie_multivideo_server.drive_v6:rk_drive_control|pcie_multivideo_server.front_v1:rk_front_control|pcie_multivideo_server.front_p1:rk_front_p1_control|pcie_multivideo_server.front_p1:rk_front_pie_control) ;; *) exit 1 ;; esac
export SYNC_SERVER SYNC_CONTROLLER
set --
if [ "$SYNC_CONTROLLER" = rk_front_pie_control ] && { [ "$SYNC_ACTION" = inspect ] || [ "$SYNC_ACTION" = start ]; }; then
    if [ ! -s "$SYNC_BOARD/crosswalk.rknn" ] || [ ! -s "$SYNC_BOARD/crosswalk.contract" ]; then
        printf 'ERROR: Mapillary crosswalk model or contract is missing; run integrate_observed_junction_rk3568.sh first.\n' >&2
        exit 1
    fi
    set -- --crosswalk-model "$SYNC_BOARD/crosswalk.rknn" --crosswalk-contract "$SYNC_BOARD/crosswalk.contract"
fi
. "$SYNC_BOARD/sync_controller_processes.sh"
case "$SYNC_ACTION" in
    inspect)
        if [ "$SYNC_CONTROLLER" = rk_front_pie_control ]; then
            "$SYNC_LOADER" --library-path "$SYNC_LIB" "$SYNC_BOARD/pie_anticipation_parity_test"
        fi
        "$SYNC_LOADER" --library-path "$SYNC_LIB" "$SYNC_BOARD/$SYNC_SERVER" \
            --lane-model "$SYNC_BOARD/lane_style.rknn" --lane-contract "$SYNC_BOARD/lane_style.contract" \
            --lane-runtime "$SYNC_BASE/lib/librknnrt.so" \
            --object-model "$SYNC_BOARD/road_objects.rknn" --object-contract "$SYNC_BOARD/road_objects.contract" "$@" --lane-inspect
        ;;
    start)
        test -c "/dev/pango_pcie_multivideo_$SYNC_BDF"
        sh "$SYNC_BOARD/run_sync_board.sh" inspect "$SYNC_BDF"
        sync_controller_stop
        sync_require_free_ports
        "$SYNC_LOADER" --library-path "$SYNC_LIB" "$SYNC_BOARD/$SYNC_CONTROLLER" \
            --state-file "$SYNC_BOARD/sync_session.state" > "$SYNC_BOARD/sync_control.log" 2>&1 < /dev/null &
        SYNC_PID=$!
        sleep 1
        if ! kill -0 "$SYNC_PID" 2>/dev/null || ! grep -q '^RK_SYNC_CONTROL_READY' "$SYNC_BOARD/sync_control.log"; then
            cat "$SYNC_BOARD/sync_control.log"
            sync_controller_diagnostics
            printf 'ERROR: control service did not become ready; existing video server was not stopped.\n' >&2
            exit 1
        fi
        printf '%s\n' "$SYNC_PID" > "$SYNC_BOARD/sync_controller.pid"
        sh "$SYNC_BOARD/stop_multivideo_server.sh"
        SYNC_LOG="$SYNC_BOARD/stream_sync_$$.log"
        printf '%s\n' "$SYNC_LOG" > "$SYNC_BOARD/last_log"
        if [ "$SYNC_SERVER" = pcie_multivideo_server.front_v1 ] || [ "$SYNC_SERVER" = pcie_multivideo_server.front_p1 ]; then
            "$SYNC_LOADER" --library-path "$SYNC_LIB" "$SYNC_BOARD/$SYNC_SERVER" \
                --device "/dev/pango_pcie_multivideo_$SYNC_BDF" --source hdmi --serve --port 9002 \
                --send-timeout-ms 30000 --profile --daemon --log "$SYNC_LOG" \
                --lane-model "$SYNC_BOARD/lane_style.rknn" --lane-contract "$SYNC_BOARD/lane_style.contract" \
                --lane-runtime "$SYNC_BASE/lib/librknnrt.so" --lane-async --lane-max-age-ms 750 \
                --object-model "$SYNC_BOARD/road_objects.rknn" --object-contract "$SYNC_BOARD/road_objects.contract" "$@"
        else
            "$SYNC_LOADER" --library-path "$SYNC_LIB" "$SYNC_BOARD/$SYNC_SERVER" \
                --device "/dev/pango_pcie_multivideo_$SYNC_BDF" --source all --overlap-verify --serve --port 9002 \
                --send-timeout-ms 30000 --profile --daemon --log "$SYNC_LOG" \
                --lane-model "$SYNC_BOARD/lane_style.rknn" --lane-contract "$SYNC_BOARD/lane_style.contract" \
                --lane-runtime "$SYNC_BASE/lib/librknnrt.so" --lane-async --lane-max-age-ms 750 \
                --object-model "$SYNC_BOARD/road_objects.rknn" --object-contract "$SYNC_BOARD/road_objects.contract" \
                --sync-state "$SYNC_BOARD/sync_session.state"
        fi
        SYNC_TRY=0
        while [ "$SYNC_TRY" -lt 30 ]; do
            if [ -f "$SYNC_LOG" ] && grep -q '^Multivideo listening on ' "$SYNC_LOG"; then
                cat "$SYNC_LOG";
                if [ "$SYNC_SERVER" = pcie_multivideo_server.front_v1 ] || [ "$SYNC_SERVER" = pcie_multivideo_server.front_p1 ]; then printf 'FRONT_BOARD_START_PASS\n'; else printf 'SYNC_BOARD_START_PASS\n'; fi
                exit 0
            fi
            sleep 1; SYNC_TRY=$((SYNC_TRY + 1))
        done
        cat "$SYNC_LOG"; exit 1
        ;;
    log|status)
        cat "$SYNC_BOARD/sync_control.log"
        sync_controller_diagnostics
        if [ -f "$SYNC_BOARD/last_log" ]; then IFS= read -r SYNC_LOG < "$SYNC_BOARD/last_log"; tail -n 35 "$SYNC_LOG"; fi
        ;;
    stop-controller) sync_controller_stop ;;
    *) printf 'Usage: sh run_sync_board.sh inspect|start|status|log|stop-controller [BDF]\n' >&2; exit 1 ;;
esac
