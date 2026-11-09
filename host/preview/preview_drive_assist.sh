#!/usr/bin/env bash
set -euo pipefail
DRIVE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DRIVE_WORK="${LANE_UBUNTU_WORK:-$HOME/pcie_lane_rk3568}"
mkdir -p "$DRIVE_WORK/preview_fonts"
# Update presentation only. Keep the verified native checksum and board service.
if [[ "$DRIVE_ROOT" != "$DRIVE_WORK" ]]; then
    for DRIVE_FILE in preview_multivideo.py drive_preview.py frame_to_png.py frame_format.py multivideo_stream.py; do
        cp -- "$DRIVE_ROOT/$DRIVE_FILE" "$DRIVE_WORK/$DRIVE_FILE"
    done
    for DRIVE_FONT in PangoPreviewSans.ttf OFL.txt manifest.json; do
        if [[ -f "$DRIVE_ROOT/preview_fonts/$DRIVE_FONT" ]]; then
            cp -- "$DRIVE_ROOT/preview_fonts/$DRIVE_FONT" "$DRIVE_WORK/preview_fonts/$DRIVE_FONT"
        fi
    done
fi
printf '统一预览：三路视频、中文状态、变道请求和反馈在同一窗口。\n'
printf '保留原中继终端；无需另外打开控制网页。空格开始，A/D变道，K取消，X停止。\n'
exec bash "$DRIVE_ROOT/preview_hdmi_lane_fast.sh" --drive-panel "$@"
