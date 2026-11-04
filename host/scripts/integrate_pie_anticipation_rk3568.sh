#!/usr/bin/env bash
set -euo pipefail
PIE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PIE_WORK="${LANE_UBUNTU_WORK:-$HOME/pcie_lane_rk3568}"
mkdir -p "$PIE_ROOT/rk3568_pie_deploy" "$PIE_WORK"
exec > >(tee "$PIE_ROOT/rk3568_pie_deploy/integrate.log") 2>&1
trap 'printf "PIE_INTEGRATE_FAILED line=%s; see rk3568_pie_deploy/integrate.log\n" "$LINENO"' ERR
printf 'Preparing HDMI lane/YOLO plus PIE v2 geometry scores on RK3568 CPU.\n'
for PIE_SCRIPT in deploy_sync_control_rk3568.sh run_sync_board.sh sync_controller_processes.sh \
    deploy_pie_anticipation_rk3568.sh run_front_pie_board.sh preview_pie_anticipation.sh; do
    bash -n "$PIE_ROOT/$PIE_SCRIPT"
done
PIE_HDC="${HDC:-$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc}"
if [[ ! -x "$PIE_HDC" ]]; then
    for PIE_CANDIDATE in /home/*/openharmony/prebuilts/ohos-sdk/linux/*/toolchains/hdc; do
        if [[ -x "$PIE_CANDIDATE" ]]; then PIE_HDC="$PIE_CANDIDATE"; break; fi
    done
fi
if [[ ! -x "$PIE_HDC" ]]; then printf 'ERROR: HDC missing; export HDC=/actual/path/hdc\n' >&2; exit 1; fi
export HDC="$PIE_HDC"
PIE_TARGET="${TARGET:-7001005458323933328a26cf4db23900}"
PIE_BDF="${FPGA_BDF:-0002:21:00.0}"
[[ "$PIE_BDF" =~ ^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$ ]]
sudo -n true
sudo -n "$HDC" list targets
PIE_DEVICE_REPORT="$(sudo -n "$HDC" -t "$PIE_TARGET" shell \
    "if [ ! -d /sys/module/pango_pcie_multivideo ]; then insmod /data/local/tmp/pango_pcie_multivideo.ko profile=1 cached_dma=1 || exit 1; fi; cat /sys/module/pango_pcie_multivideo/version && test -c /dev/pango_pcie_multivideo_$PIE_BDF && echo PIE_DEVICE_READY")"
printf '%s\n' "$PIE_DEVICE_REPORT"
if ! printf '%s\n' "$PIE_DEVICE_REPORT" | grep -q '^PIE_DEVICE_READY'; then
    printf 'ERROR: RK3568 or FPGA device not ready; existing recognition service was not switched.\n' >&2; exit 1
fi
python3 "$PIE_ROOT/verify_pie_c_export.py"
bash "$PIE_ROOT/test_front_p1.sh"
python3 "$PIE_ROOT/tests/test_front_anticipation_preview.py"
if [[ "$PIE_ROOT" != "$PIE_WORK" ]]; then
    for PIE_FILE in build_checksum.sh frame_checksum.c frame_to_png.py frame_format.py; do
        cp -- "$PIE_ROOT/$PIE_FILE" "$PIE_WORK/$PIE_FILE"
    done
fi
bash "$PIE_WORK/build_checksum.sh"
bash "$PIE_ROOT/deploy_pie_anticipation_rk3568.sh" prepare
bash "$PIE_ROOT/deploy_pie_anticipation_rk3568.sh" install
bash "$PIE_ROOT/deploy_pie_anticipation_rk3568.sh" start
python3 "$PIE_ROOT/check_pie_anticipation.py"
printf '\nPIE_INTEGRATE_PASS\nPreview: bash /mnt/hgfs/pcie_host/preview_pie_anticipation.sh\n'
