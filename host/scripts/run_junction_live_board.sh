#!/bin/sh
set -eu
JUNCTION_BOARD=/data/local/tmp/waymo_junction_live
JUNCTION_MODELS=/data/local/tmp/hdmi_lane_v1
JUNCTION_BASE=/data/local/tmp/rknn-yolov5-test
JUNCTION_LOADER="$JUNCTION_BASE/glibc/ld-linux-aarch64.so.1"
JUNCTION_LIB="$JUNCTION_BASE/lib:$JUNCTION_BASE/glibc"
JUNCTION_BINARY="$JUNCTION_BOARD/pcie_multivideo_server.junction_live"
JUNCTION_ACTION="${1:-start}"
JUNCTION_BDF="${2:-0002:21:00.0}"
case "$JUNCTION_BDF" in *[!0-9a-fA-F:.]*|'') exit 1 ;; esac
case "$JUNCTION_ACTION" in prepare|start|stop|log|inspect) ;; *) exit 1 ;; esac
if [ "$JUNCTION_ACTION" = log ]; then
    tail -n 80 "$JUNCTION_BOARD/stream.log"
    printf 'JUNCTION_BOARD_LOG_PASS\n'
    exit 0
fi
if [ "$JUNCTION_ACTION" = prepare ] || [ "$JUNCTION_ACTION" = start ]; then
    JUNCTION_PCI="/sys/bus/pci/devices/$JUNCTION_BDF"
    if [ ! -d "$JUNCTION_PCI" ]; then
        printf 'JUNCTION_PCIE_ABSENT bdf=%s; PCIe device is not enumerated.\n' "$JUNCTION_BDF" >&2
        exit 1
    fi
    JUNCTION_ENABLED="$(cat "$JUNCTION_PCI/enable")"
    JUNCTION_DRIVER="$(readlink "$JUNCTION_PCI/driver" 2>/dev/null || true)"
    printf 'JUNCTION_PCIE_BEFORE bdf=%s enable=%s driver=%s\n' "$JUNCTION_BDF" "$JUNCTION_ENABLED" "${JUNCTION_DRIVER:-none}"
    if [ ! -d /sys/module/pango_pcie_multivideo ]; then
        printf 'JUNCTION_DRIVER_LOADING existing_module=pango_pcie_multivideo.ko\n'
        insmod /data/local/tmp/pango_pcie_multivideo.ko profile=1 cached_dma=1
    fi
    JUNCTION_ENABLED="$(cat "$JUNCTION_PCI/enable")"
    JUNCTION_DRIVER="$(readlink "$JUNCTION_PCI/driver" 2>/dev/null || true)"
    JUNCTION_VERSION="$(cat /sys/module/pango_pcie_multivideo/version)"
    printf 'JUNCTION_PCIE_STATE bdf=%s enable=%s driver=%s version=%s\n' "$JUNCTION_BDF" "$JUNCTION_ENABLED" "${JUNCTION_DRIVER:-none}" "$JUNCTION_VERSION"
    case "$JUNCTION_ENABLED" in ''|*[!0-9]*|0) echo 'ERROR: PCIe device is disabled; key/LED registers have not been inspected.' >&2; exit 1 ;; esac
    if [ "${JUNCTION_DRIVER##*/}" != pango_pcie_multivideo ] || [ "$JUNCTION_VERSION" != 1.3 ]; then
        echo 'ERROR: expected pango_pcie_multivideo driver version 1.3; existing services preserved.' >&2
        exit 1
    fi
    test -c "/dev/pango_pcie_multivideo_$JUNCTION_BDF"
    if [ "$JUNCTION_ACTION" = prepare ]; then
        printf 'JUNCTION_BOARD_PREPARE_PASS\n'
        exit 0
    fi
fi
if [ "$JUNCTION_ACTION" = start ] || [ "$JUNCTION_ACTION" = inspect ]; then
    test -s "$JUNCTION_MODELS/road_objects.rknn"
    test -s "$JUNCTION_MODELS/road_objects.contract"
    "$JUNCTION_LOADER" --library-path "$JUNCTION_LIB" "$JUNCTION_BINARY" \
        --demo-model "$JUNCTION_MODELS/road_objects.rknn" --demo-contract "$JUNCTION_MODELS/road_objects.contract" \
        --demo-runtime "$JUNCTION_BASE/lib/librknnrt.so" --demo-inspect
fi
if [ "$JUNCTION_ACTION" = inspect ]; then printf 'JUNCTION_BOARD_INSPECT_PASS\n'; exit 0; fi
if [ "$JUNCTION_ACTION" = start ]; then
    for JUNCTION_INODE in $(awk '$2 ~ /:233C$/ && $4 == "0A" {print $10}' /proc/net/tcp /proc/net/tcp6); do
        JUNCTION_OWNED=no
        for JUNCTION_PROC in /proc/[0-9]*; do
            JUNCTION_NAME="$(cat "$JUNCTION_PROC/comm" 2>/dev/null || true)"
            [ "$JUNCTION_NAME" = pcie_multivideo ] || continue
            for JUNCTION_FD in "$JUNCTION_PROC"/fd/*; do
                if [ "$(readlink "$JUNCTION_FD" 2>/dev/null || true)" = "socket:[$JUNCTION_INODE]" ]; then JUNCTION_OWNED=yes; fi
            done
        done
        if [ "$JUNCTION_OWNED" != yes ]; then echo 'ERROR: unrelated service occupies port 9020; existing video preserved.'; exit 1; fi
    done
fi
SYNC_BOARD=/data/local/tmp/hdmi_lane_v1
SYNC_LOADER="$JUNCTION_LOADER"
. "$JUNCTION_BOARD/sync_controller_processes.sh"
sync_controller_stop
sh "$JUNCTION_BOARD/stop_multivideo_server.sh"
if [ "$JUNCTION_ACTION" = stop ]; then printf 'JUNCTION_BOARD_STOP_PASS\n'; exit 0; fi
"$JUNCTION_LOADER" --library-path "$JUNCTION_LIB" "$JUNCTION_BINARY" \
    --device "/dev/pango_pcie_multivideo_$JUNCTION_BDF" --source all --overlap-verify --serve --port 9002 \
    --send-timeout-ms 2000 --profile --daemon --log "$JUNCTION_BOARD/stream.log" \
    --demo-model "$JUNCTION_MODELS/road_objects.rknn" --demo-contract "$JUNCTION_MODELS/road_objects.contract" \
    --demo-runtime "$JUNCTION_BASE/lib/librknnrt.so"
JUNCTION_TRY=0
while [ "$JUNCTION_TRY" -lt 20 ]; do
    if grep -q '^JUNCTION_LIVE_READY ' "$JUNCTION_BOARD/stream.log" && \
        grep -q '^Multivideo listening on .*version=multivideo-waymo-junction-v1 ' "$JUNCTION_BOARD/stream.log"; then
        cat "$JUNCTION_BOARD/stream.log"
        printf 'JUNCTION_BOARD_START_PASS\n'
        exit 0
    fi
    sleep 1
    JUNCTION_TRY=$((JUNCTION_TRY + 1))
done
cat "$JUNCTION_BOARD/stream.log"
printf 'ERROR: junction server not ready; restore with deploy_three_video_rk3568.sh start.\n' >&2
exit 1
