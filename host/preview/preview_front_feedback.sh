#!/usr/bin/env bash
set -euo pipefail
FEEDBACK_PREVIEW_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 - "$FEEDBACK_PREVIEW_ROOT" "$@" <<'PY'
import sys
sys.path.insert(0,sys.argv[1])
from fpga_drive_feedback import FPGAFeedbackClient,OFF
try: FPGAFeedbackClient(active_low='--fpga-led-active-low' in sys.argv[2:]).send((OFF,0,False,'preview_probe'))
except (OSError,ValueError) as error:
    raise SystemExit('FPGA feedback service unavailable; run deploy_fpga_feedback.sh: '+str(error))
print('FPGA_FEEDBACK_PREVIEW_LINK_PASS')
PY
printf 'FPGA反馈：方向慢闪=请求；方向常亮=通行/模拟动作；三灯常亮=停车；双闪=礼让；快闪=报警；慢闪=未知。\n'
exec bash "$FEEDBACK_PREVIEW_ROOT/preview_front_fpga_keys.sh" --fpga-feedback-port 9017 "$@"
