#!/usr/bin/env bash
set -euo pipefail
FRONT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FRONT_WORK="${LANE_UBUNTU_WORK:-$HOME/pcie_lane_rk3568}"
mkdir -p "$FRONT_WORK/preview_fonts"
if [[ "$FRONT_ROOT" != "$FRONT_WORK" ]]; then
    for FRONT_FILE in preview_multivideo.py drive_preview.py frame_to_png.py frame_format.py multivideo_stream.py; do
        cp -- "$FRONT_ROOT/$FRONT_FILE" "$FRONT_WORK/$FRONT_FILE"
    done
    for FRONT_FONT in PangoPreviewSans.ttf OFL.txt manifest.json; do
        if [[ -f "$FRONT_ROOT/preview_fonts/$FRONT_FONT" ]]; then
            cp -- "$FRONT_ROOT/preview_fonts/$FRONT_FONT" "$FRONT_WORK/preview_fonts/$FRONT_FONT"
        fi
    done
fi
printf '前视 HDMI 辅助驾驶预览：仅显示正前方画面，控制面板在 http://127.0.0.1:9011\n'
exec python3 "$FRONT_WORK/preview_multivideo.py" --host 127.0.0.1 --port 9004 --timeout 60 --front-only "$@"
