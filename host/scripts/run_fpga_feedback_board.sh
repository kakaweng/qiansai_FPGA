#!/system/bin/sh
set -eu
FEEDBACK_ROOT=/data/local/tmp/fpga_drive_feedback
FEEDBACK_BDF="${2:-0002:21:00.0}"
case "$FEEDBACK_BDF" in *[!0-9a-fA-F:.]*|'') exit 1;; esac
case "${1:-start}" in
inspect) exec "$FEEDBACK_ROOT/rk_fpga_feedback" "$FEEDBACK_BDF" --inspect ;;
log) tail -n 40 "$FEEDBACK_ROOT/service.log";exit 0 ;;
start)
    FEEDBACK_PCI="/sys/bus/pci/devices/$FEEDBACK_BDF"
    if [ ! -d "$FEEDBACK_PCI" ]; then
        echo "FPGA_FEEDBACK_PCIE_ABSENT bdf=$FEEDBACK_BDF; check enumeration before starting feedback" >&2
        exit 1
    fi
    FEEDBACK_ENABLED="$(cat "$FEEDBACK_PCI/enable")"
    FEEDBACK_DRIVER="$(readlink "$FEEDBACK_PCI/driver" 2>/dev/null || true)"
    printf 'FPGA_FEEDBACK_PCIE_STATE bdf=%s enable=%s driver=%s\n' "$FEEDBACK_BDF" "$FEEDBACK_ENABLED" "${FEEDBACK_DRIVER:-none}"
    if [ "$FEEDBACK_ENABLED" = 0 ]; then
        echo 'FPGA_FEEDBACK_PCIE_DISABLED: restore pango_pcie_multivideo driver before checking the bitstream' >&2
        exit 1
    fi
    "$FEEDBACK_ROOT/rk_fpga_feedback" --self-test
    "$FEEDBACK_ROOT/rk_fpga_feedback" "$FEEDBACK_BDF" --inspect
    if [ -f "$FEEDBACK_ROOT/service.pid" ]; then
        FEEDBACK_PID="$(cat "$FEEDBACK_ROOT/service.pid")"
        case "$FEEDBACK_PID" in *[!0-9]*|'') echo 'Invalid feedback service pid';exit 1;; esac
        if kill -0 "$FEEDBACK_PID" 2>/dev/null; then
            FEEDBACK_EXE="$(readlink "/proc/$FEEDBACK_PID/exe")"
            case "$FEEDBACK_EXE" in "$FEEDBACK_ROOT/rk_fpga_feedback"|"$FEEDBACK_ROOT/rk_fpga_feedback (deleted)") kill "$FEEDBACK_PID";; *) echo 'Feedback pid belongs to another program';exit 1;; esac
            FEEDBACK_WAIT=0
            while kill -0 "$FEEDBACK_PID" 2>/dev/null && [ -e "/proc/$FEEDBACK_PID/exe" ] && [ "$FEEDBACK_WAIT" -lt 30 ]; do sleep .1;FEEDBACK_WAIT=$((FEEDBACK_WAIT+1));done
            if kill -0 "$FEEDBACK_PID" 2>/dev/null && [ -e "/proc/$FEEDBACK_PID/exe" ]; then echo 'Old feedback service did not exit';exit 1;fi
        fi
    fi
    nohup "$FEEDBACK_ROOT/rk_fpga_feedback" "$FEEDBACK_BDF" > "$FEEDBACK_ROOT/service.log" 2>&1 < /dev/null &
    FEEDBACK_PID=$!
    printf '%s\n' "$FEEDBACK_PID" > "$FEEDBACK_ROOT/service.pid"
    sleep .3
    kill -0 "$FEEDBACK_PID"
    grep -q '^FPGA_FEEDBACK_READY ' "$FEEDBACK_ROOT/service.log"
    printf 'FPGA_FEEDBACK_BOARD_START_PASS\n'
    ;;
*) exit 1 ;;
esac
