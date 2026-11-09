#!/usr/bin/env bash
set -euo pipefail
FRONT_P1_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FRONT_P1_WORK="${LANE_UBUNTU_WORK:-$HOME/pcie_lane_rk3568}"
mkdir -p "$FRONT_P1_WORK/preview_fonts"
if [[ "$FRONT_P1_ROOT" != "$FRONT_P1_WORK" ]]; then
    for FRONT_P1_FILE in preview_multivideo.py fpga_drive_feedback.py fpga_junction_keys.py drive_preview.py front_p1_preview.py front_junction_preview.py junction_scene.py junction_route.py junction_association.py pedestrian_behavior.py frozen_junction.py junction_presentation.py hybrid_junction.py front_live_preview.py live_road.py live_crosswalk.py crosswalk_relations.py frame_to_png.py frame_format.py multivideo_stream.py; do
        cp -- "$FRONT_P1_ROOT/$FRONT_P1_FILE" "$FRONT_P1_WORK/$FRONT_P1_FILE"
    done
    for FRONT_P1_FONT in PangoPreviewSans.ttf PangoPreviewSans-Bold.ttf OFL.txt manifest.json; do
        if [[ -f "$FRONT_P1_ROOT/preview_fonts/$FRONT_P1_FONT" ]]; then
            cp -- "$FRONT_P1_ROOT/preview_fonts/$FRONT_P1_FONT" "$FRONT_P1_WORK/preview_fonts/$FRONT_P1_FONT"
        fi
    done
fi
python3 - "$FRONT_P1_WORK" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from frame_to_png import checksum_backend
if checksum_backend() != 'C/FNV-1a':
    raise SystemExit('缺少 C 校验加速库；请重新运行 integrate_front_p1_rk3568.sh 完成安装。')
PY
printf 'HDMI P1：实时道路2.5D；M路口示意叠加实时感知，V大视频，H地面标定，U视频区域。\n'
printf '路口规则：source-band-route-conflict-v2；保留真实行人，连续运动判断横穿，位置推测用虚环标识。\n'
printf '冻结预演：Windows 先暂停原视频；M 进入，N 分析，J/I/L 选方向，G 播放，B 暂停，R 重播，M 返回。\n'
exec python3 "$FRONT_P1_WORK/preview_multivideo.py" --host 127.0.0.1 --port 9004 --timeout 60 --front-p1 "$@"
