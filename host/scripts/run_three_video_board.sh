#!/bin/sh
set -eu
THREE_BOARD=/data/local/tmp/three_video_display
THREE_BINARY="$THREE_BOARD/pcie_multivideo_server.ethernet_v6"
THREE_ACTION="${1:-start}"
THREE_BDF="${2:-0002:21:00.0}"
THREE_TIMEOUT="${3:-2000}"
case "$THREE_ACTION" in start|stop|log) ;; *) exit 1 ;; esac
case "$THREE_TIMEOUT" in ''|*[!0-9]*) exit 1 ;; esac
if [ "$THREE_TIMEOUT" -lt 100 ] || [ "$THREE_TIMEOUT" -gt 30000 ]; then exit 1; fi
if [ "$THREE_ACTION" = log ]; then
    tail -n 60 "$THREE_BOARD/stream.log"
    printf 'THREE_VIDEO_LOG_PASS\n'
    exit 0
fi
if [ "$THREE_ACTION" = start ]; then
    THREE_VERSION="$("$THREE_BINARY" --version)"
    [ "$THREE_VERSION" = multivideo-ethernet-v6 ]
    if [ ! -d /sys/module/pango_pcie_multivideo ]; then
        insmod /data/local/tmp/pango_pcie_multivideo.ko profile=1 cached_dma=1
    fi
    test -c "/dev/pango_pcie_multivideo_$THREE_BDF"
    [ "$(cat /sys/module/pango_pcie_multivideo/version)" = 1.3 ]
fi
# Stop only identified project controllers. Models are owned by the video server.
SYNC_BOARD=/data/local/tmp/hdmi_lane_v1
SYNC_LOADER=/data/local/tmp/rknn-yolov5-test/glibc/ld-linux-aarch64.so.1
. "$THREE_BOARD/sync_controller_processes.sh"
sync_controller_stop
sh "$THREE_BOARD/stop_multivideo_server.sh"
if [ "$THREE_ACTION" = stop ]; then printf 'THREE_VIDEO_STOP_PASS\n'; exit 0; fi
"$THREE_BINARY" --device "/dev/pango_pcie_multivideo_$THREE_BDF" \
    --source all --overlap-verify --serve --port 9002 --send-timeout-ms "$THREE_TIMEOUT" \
    --profile --daemon --log "$THREE_BOARD/stream.log"
THREE_TRY=0
while [ "$THREE_TRY" -lt 10 ]; do
    if grep -q '^Multivideo listening on .*version=multivideo-ethernet-v6 ' "$THREE_BOARD/stream.log"; then
        cat "$THREE_BOARD/stream.log"
        printf 'THREE_VIDEO_BOARD_START_PASS\n'
        exit 0
    fi
    sleep 1
    THREE_TRY=$((THREE_TRY + 1))
done
cat "$THREE_BOARD/stream.log"
printf 'ERROR: pure transport server did not become ready.\n' >&2
exit 1
