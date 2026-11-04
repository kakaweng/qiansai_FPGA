#!/system/bin/sh
set -eu
KEY_ROOT=/data/local/tmp/fpga_junction_keys
KEY_BDF="${2:-0002:21:00.0}"
case "$KEY_BDF" in *[!0-9a-fA-F:.]*|'') exit 1;; esac
case "${1:-start}" in
inspect|start)
    KEY_PCI="/sys/bus/pci/devices/$KEY_BDF"
    if [ ! -d "$KEY_PCI" ]; then
        echo "FPGA_KEY_PCIE_ABSENT bdf=$KEY_BDF; check PCIe enumeration" >&2
        exit 1
    fi
    KEY_ENABLED="$(cat "$KEY_PCI/enable")"
    KEY_DRIVER="$(readlink "$KEY_PCI/driver" 2>/dev/null || true)"
    printf 'FPGA_KEY_PCIE_STATE bdf=%s enable=%s driver=%s\n' "$KEY_BDF" "$KEY_ENABLED" "${KEY_DRIVER:-none}"
    case "$KEY_ENABLED" in ''|*[!0-9]*|0)
        echo 'FPGA_KEY_PCIE_DISABLED: restore the capture driver before inspecting FPGA capabilities' >&2
        exit 1 ;;
    esac
    ;;
esac
case "${1:-start}" in
inspect) exec "$KEY_ROOT/rk_fpga_keys" "$KEY_BDF" --inspect ;;
start)
    "$KEY_ROOT/rk_fpga_keys" "$KEY_BDF" --inspect
    if [ -f "$KEY_ROOT/service.pid" ]; then
        KEY_PID="$(cat "$KEY_ROOT/service.pid")"
        case "$KEY_PID" in *[!0-9]*|'') echo 'Invalid key service pid';exit 1;; esac
        if kill -0 "$KEY_PID" 2>/dev/null; then
            KEY_EXE="$(readlink "/proc/$KEY_PID/exe")"
            case "$KEY_EXE" in "$KEY_ROOT/rk_fpga_keys"|"$KEY_ROOT/rk_fpga_keys (deleted)") kill "$KEY_PID";; *) echo 'Key pid belongs to another program';exit 1;; esac
            KEY_WAIT=0
            while kill -0 "$KEY_PID" 2>/dev/null && [ -e "/proc/$KEY_PID/exe" ] && [ "$KEY_WAIT" -lt 30 ]; do sleep .1;KEY_WAIT=$((KEY_WAIT+1));done
            if kill -0 "$KEY_PID" 2>/dev/null && [ -e "/proc/$KEY_PID/exe" ]; then echo 'Old key service did not exit';exit 1;fi
        fi
    fi
    "$KEY_ROOT/rk_fpga_keys" "$KEY_BDF" --daemon --pid "$KEY_ROOT/service.pid" --log "$KEY_ROOT/service.log"
    sleep .2
    test -s "$KEY_ROOT/service.pid"
    kill -0 "$(cat "$KEY_ROOT/service.pid")"
    echo FPGA_KEYS_BOARD_START_PASS
    ;;
*) echo 'Usage: start|inspect PCI_BDF';exit 1;;
esac
