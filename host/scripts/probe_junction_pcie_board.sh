#!/system/bin/sh
set -eu
PROBE_BDF="${1:-0002:21:00.0}"
case "$PROBE_BDF" in *[!0-9a-fA-F:.]*|'') exit 1 ;; esac
PROBE_PCI="/sys/bus/pci/devices/$PROBE_BDF"
printf 'JUNCTION_PCIE_PROBE_BEGIN bdf=%s read_only=1\n' "$PROBE_BDF"
uname -a
for PROBE_FIELD in vendor device enable current_link_speed current_link_width power/control power/runtime_status resource; do
    printf 'SYSFS %s\n' "$PROBE_FIELD"
    cat "$PROBE_PCI/$PROBE_FIELD" 2>/dev/null || printf 'unavailable\n'
done
printf 'SYSFS driver\n'
readlink "$PROBE_PCI/driver" 2>/dev/null || true
printf 'DRIVER version\n'
cat /sys/module/pango_pcie_multivideo/version 2>/dev/null || true
printf 'DEVICE node\n'
ls -l "/dev/pango_pcie_multivideo_$PROBE_BDF" 2>/dev/null || true
PROBE_RESULT=0
/data/local/tmp/waymo_junction_probe/rk_fpga_keys "$PROBE_BDF" --diagnose || PROBE_RESULT=$?
printf 'PROBE_PROCESS_EXIT=%s\n' "$PROBE_RESULT"
printf 'KERNEL_PCIE_LOG_BEGIN\n'
dmesg | grep -i -E 'pcie|pci |pango|aer|abort|timeout' | tail -n 70 || true
printf 'KERNEL_PCIE_LOG_END\n'
printf 'JUNCTION_PCIE_REPORT_COLLECTED\n'
exit "$PROBE_RESULT"
