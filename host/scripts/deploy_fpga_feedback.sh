#!/usr/bin/env bash
set -euo pipefail
FEEDBACK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FEEDBACK_HDC="${HDC:-$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc}"
FEEDBACK_TARGET="${TARGET:-7001005458323933328a26cf4db23900}"
FEEDBACK_BDF="${FPGA_BDF:-0002:21:00.0}"
FEEDBACK_REMOTE=/data/local/tmp/fpga_drive_feedback
[[ "$FEEDBACK_BDF" =~ ^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$ ]]
feedback_hdc() { sudo -n "$FEEDBACK_HDC" -t "$FEEDBACK_TARGET" "$@"; }
trap 'printf "FPGA_FEEDBACK_DEPLOY_FAILED line=%s\n" "$LINENO" >&2' ERR
sudo -n true
python3 "$FEEDBACK_ROOT/tests/test_fpga_feedback.py"
python3 - "$FEEDBACK_ROOT/rk3568_fpga_feedback_deploy" <<'PY'
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]);metadata=json.loads((root/'manifest.json').read_text())
data=(root/'rk_fpga_feedback').read_bytes()
if (data[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(data[18:20],'little')!=183
    or hashlib.sha256(data).hexdigest()!=metadata['rk_fpga_feedback_sha256']):
    raise SystemExit('Feedback adapter artifact mismatch')
print('FPGA_FEEDBACK_ARTIFACT_PASS')
PY
feedback_hdc shell mkdir -p "$FEEDBACK_REMOTE"
FEEDBACK_TMP="$(mktemp -d)"
trap 'rm -f -- "$FEEDBACK_TMP/returned"; rmdir -- "$FEEDBACK_TMP"' EXIT
for FEEDBACK_FILE in rk_fpga_feedback run_fpga_feedback_board.sh; do
    FEEDBACK_LOCAL="$FEEDBACK_ROOT/$FEEDBACK_FILE"
    [[ "$FEEDBACK_FILE" != rk_fpga_feedback ]] || FEEDBACK_LOCAL="$FEEDBACK_ROOT/rk3568_fpga_feedback_deploy/$FEEDBACK_FILE"
    feedback_hdc file send "$FEEDBACK_LOCAL" "$FEEDBACK_REMOTE/$FEEDBACK_FILE.upload"
    feedback_hdc file recv "$FEEDBACK_REMOTE/$FEEDBACK_FILE.upload" "$FEEDBACK_TMP/returned"
    cmp -- "$FEEDBACK_LOCAL" "$FEEDBACK_TMP/returned"
    rm -f -- "$FEEDBACK_TMP/returned"
    feedback_hdc shell mv -f "$FEEDBACK_REMOTE/$FEEDBACK_FILE.upload" "$FEEDBACK_REMOTE/$FEEDBACK_FILE"
    feedback_hdc shell chmod 755 "$FEEDBACK_REMOTE/$FEEDBACK_FILE"
done
FEEDBACK_RESPONSE="$(feedback_hdc shell sh "$FEEDBACK_REMOTE/run_fpga_feedback_board.sh" start "$FEEDBACK_BDF")"
printf '%s\n' "$FEEDBACK_RESPONSE"
grep -q '^FPGA_FEEDBACK_BOARD_START_PASS' <<<"$FEEDBACK_RESPONSE"
FEEDBACK_SPEED="$(feedback_hdc shell cat "/sys/bus/pci/devices/$FEEDBACK_BDF/current_link_speed" 2>/dev/null || true)"
printf 'FPGA_FEEDBACK_LINK_SPEED %s\n' "${FEEDBACK_SPEED:-unavailable}"
case "$FEEDBACK_SPEED" in
    *5.0*) ;;
    *2.5*) printf 'NOTE: Gen1 link: hardware blink periods and lease are twice nominal (lease about 3s).\n' ;;
    *) printf 'NOTE: check actual PCIe link speed; nominal LED timing assumes Gen2 / 125MHz.\n' ;;
esac
FEEDBACK_FORWARDS="$(feedback_hdc fport ls)"
if ! grep -Eq "^$FEEDBACK_TARGET[[:space:]]+tcp:9017[[:space:]]+tcp:9015([[:space:]]|$)" <<<"$FEEDBACK_FORWARDS"; then
    if [[ -n "$(ss -H -ltn 'sport = :9017')" ]]; then echo 'Ubuntu 9017 occupied';exit 1;fi
    feedback_hdc fport tcp:9017 tcp:9015
fi
python3 - "$FEEDBACK_ROOT" <<'PY'
import sys
sys.path.insert(0,sys.argv[1])
from fpga_drive_feedback import FPGAFeedbackClient,OFF
FPGAFeedbackClient().send((OFF,0,False,'deployment_probe'))
print('FPGA_FEEDBACK_LINK_PASS')
PY
printf 'FPGA_FEEDBACK_DEPLOY_PASS\nOpen: bash /mnt/hgfs/pcie_host/preview_front_feedback.sh\n'
