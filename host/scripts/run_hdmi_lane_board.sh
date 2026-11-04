#!/bin/sh
set -eu
LANE_BOARD=/data/local/tmp/hdmi_lane_v1
LANE_BASE=/data/local/tmp/rknn-yolov5-test
LANE_LOADER="$LANE_BASE/glibc/ld-linux-aarch64.so.1"
LANE_LIBRARIES="$LANE_BASE/lib:$LANE_BASE/glibc"
LANE_DEVICE="/dev/pango_pcie_multivideo_${2:-0002:21:00.0}"
LANE_EXECUTABLE="$LANE_BOARD/pcie_multivideo_server.lane_v1"
case "${1:-inspect}" in
    inspect-fast|start-fast) LANE_EXECUTABLE="$LANE_BOARD/pcie_multivideo_server.lane_v2" ;;
    inspect-diag|start-diag) LANE_EXECUTABLE="$LANE_BOARD/pcie_multivideo_server.lane_v3" ;;
    inspect-scene|start-scene) LANE_EXECUTABLE="$LANE_BOARD/pcie_multivideo_server.scene_v4" ;;
esac
lane_model_args()
{
    if [ "$1" = inspect-scene ] || [ "$1" = start-scene ]; then
        shift
        "$LANE_LOADER" --library-path "$LANE_LIBRARIES" "$LANE_EXECUTABLE" \
            --lane-model "$LANE_BOARD/lane_style.rknn" --lane-contract "$LANE_BOARD/lane_style.contract" \
            --lane-runtime "$LANE_BASE/lib/librknnrt.so" \
            --object-model "$LANE_BOARD/road_objects.rknn" --object-contract "$LANE_BOARD/road_objects.contract" "$@"
    else
        shift
        "$LANE_LOADER" --library-path "$LANE_LIBRARIES" "$LANE_EXECUTABLE" \
            --lane-model "$LANE_BOARD/lane_style.rknn" --lane-contract "$LANE_BOARD/lane_style.contract" \
            --lane-runtime "$LANE_BASE/lib/librknnrt.so" "$@"
    fi
}
LANE_ACTION="${1:-inspect}"

lane_inspect()
{
    lane_model_args "$LANE_ACTION" --lane-inspect
}

lane_start()
{
    lane_model_args "$LANE_ACTION" \
        --device "$LANE_DEVICE" --source all --overlap-verify --serve --port 9002 \
        --send-timeout-ms 30000 --profile --daemon --log "$LANE_LOG" \
        "$@"
}

case "${1:-inspect}" in
    inspect|inspect-fast|inspect-diag|inspect-scene) lane_inspect ;;
    start|start-fast|start-diag|start-scene)
        test -c "$LANE_DEVICE"
        lane_inspect
        sh "$LANE_BOARD/stop_multivideo_server.sh"
        LANE_LOG="$LANE_BOARD/stream_$$.log"
        printf '%s\n' "$LANE_LOG" > "$LANE_BOARD/last_log"
        if [ "$1" = start ]; then lane_start; else lane_start --lane-async --lane-max-age-ms 750; fi
        LANE_ATTEMPT=0
        while [ "$LANE_ATTEMPT" -lt 30 ]; do
            if [ -f "$LANE_LOG" ]; then
                while IFS= read -r LANE_LINE; do
                    case "$LANE_LINE" in
                        'Multivideo listening on '*) cat "$LANE_LOG"; printf 'LANE_START_PASS log=%s\n' "$LANE_LOG"; exit 0 ;;
                    esac
                done < "$LANE_LOG"
            fi
            sleep 1
            LANE_ATTEMPT=$((LANE_ATTEMPT + 1))
        done
        cat "$LANE_LOG"
        printf 'ERROR: lane server did not become ready; use rollback.\n' >&2
        exit 1
        ;;
    log)
        IFS= read -r LANE_LOG < "$LANE_BOARD/last_log"
        tail -n 60 "$LANE_LOG"
        ;;
    rollback)
        test -x /data/local/tmp/pcie_multivideo_server.ethernet_v6
        test -c "$LANE_DEVICE"
        sh "$LANE_BOARD/stop_multivideo_server.sh"
        /data/local/tmp/pcie_multivideo_server.ethernet_v6 --device "$LANE_DEVICE" \
            --source all --overlap-verify --serve --port 9002 --send-timeout-ms 30000 --profile \
            --daemon --log /data/local/tmp/ethernet_lane_rollback.log
        printf 'LANE_ROLLBACK_STARTED\n'
        ;;
    *) printf 'Usage: sh run_hdmi_lane_board.sh inspect|inspect-fast|inspect-diag|inspect-scene|start|start-fast|start-diag|start-scene|log|rollback [BDF]\n' >&2; exit 1 ;;
esac
