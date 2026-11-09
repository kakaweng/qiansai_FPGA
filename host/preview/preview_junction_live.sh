#!/usr/bin/env bash
set -euo pipefail
JUNCTION_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
JUNCTION_PREVIEW="$HOME/pcie_three_video"
bash "$JUNCTION_ROOT/preview_three_video.sh" --prepare-only
for JUNCTION_FILE in junction_live_preview.py fpga_drive_feedback.py fpga_junction_keys.py; do
    if [[ "$JUNCTION_ROOT" != "$JUNCTION_PREVIEW" ]]; then
        cp -- "$JUNCTION_ROOT/$JUNCTION_FILE" "$JUNCTION_PREVIEW/$JUNCTION_FILE"
    fi
done
mkdir -p "$JUNCTION_PREVIEW/preview_fonts"
cp -- "$JUNCTION_ROOT/preview_fonts/PangoPreviewSans.ttf" "$JUNCTION_PREVIEW/preview_fonts/PangoPreviewSans.ttf"
python3 - "$JUNCTION_PREVIEW" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from junction_live_preview import JunctionView
JunctionView()
print('JUNCTION_PREVIEW_DEPENDENCIES_PASS')
PY
if [[ "${1:-}" == --prepare-only ]]; then exit 0; fi
cd "$JUNCTION_PREVIEW"
exec python3 junction_live_preview.py "$@"
