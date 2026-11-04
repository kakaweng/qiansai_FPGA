#!/usr/bin/env bash
set -euo pipefail
PROBE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROBE_HDC="${HDC:-$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc}"
PROBE_TARGET="${TARGET:-7001005458323933328a26cf4db23900}"
PROBE_BDF="${FPGA_BDF:-0002:21:00.0}"
PROBE_BOARD=/data/local/tmp/waymo_junction_probe
[[ "$PROBE_BDF" =~ ^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$ ]]
test -x "$PROBE_HDC"
sudo -n true
mkdir -p "$PROBE_ROOT/rk3568_junction_live_deploy"
exec > >(tee "$PROBE_ROOT/rk3568_junction_live_deploy/pcie_probe.log") 2>&1
probe_hdc() { sudo -n "$PROBE_HDC" -t "$PROBE_TARGET" "$@"; }
printf 'Read-only PCIe diagnosis; video services and FPGA bitstream are unchanged.\n'
python3 - "$PROBE_ROOT/rk3568_fpga_keys_deploy" <<'PY'
import hashlib, json, sys
from pathlib import Path
root=Path(sys.argv[1]); data=(root/'rk_fpga_keys').read_bytes()
manifest=json.loads((root/'manifest.json').read_text())
if (data[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(data[18:20],'little')!=183 or
    hashlib.sha256(data).hexdigest()!=manifest['rk_fpga_keys_sha256'] or b'PROBE_SUMMARY' not in data):
    raise SystemExit('Diagnostic binary missing or mismatched')
print('JUNCTION_PCIE_PROBE_ARTIFACT_PASS')
PY
PROBE_TMP="$(mktemp -d)"
trap 'rm -f -- "$PROBE_TMP/returned" "$PROBE_TMP/response"; rmdir -- "$PROBE_TMP"' EXIT
probe_hdc shell mkdir -p "$PROBE_BOARD"
for PROBE_FILE in rk_fpga_keys probe_junction_pcie_board.sh; do
    PROBE_LOCAL="$PROBE_ROOT/$PROBE_FILE"
    [[ "$PROBE_FILE" != rk_fpga_keys ]] || PROBE_LOCAL="$PROBE_ROOT/rk3568_fpga_keys_deploy/rk_fpga_keys"
    probe_hdc file send "$PROBE_LOCAL" "$PROBE_BOARD/$PROBE_FILE.upload"
    probe_hdc file recv "$PROBE_BOARD/$PROBE_FILE.upload" "$PROBE_TMP/returned"
    cmp -- "$PROBE_LOCAL" "$PROBE_TMP/returned"
    rm -f -- "$PROBE_TMP/returned"
    probe_hdc shell chmod 755 "$PROBE_BOARD/$PROBE_FILE.upload"
    probe_hdc shell mv -f "$PROBE_BOARD/$PROBE_FILE.upload" "$PROBE_BOARD/$PROBE_FILE"
done
# Preserve kernel diagnostics even if the MMIO helper faults or HDC propagates a nonzero exit.
PROBE_TRANSPORT=0
probe_hdc shell sh "$PROBE_BOARD/probe_junction_pcie_board.sh" "$PROBE_BDF" | tee "$PROBE_TMP/response" || PROBE_TRANSPORT=$?
if ! grep -qx 'JUNCTION_PCIE_REPORT_COLLECTED' "$PROBE_TMP/response"; then
    echo 'ERROR: report incomplete; see pcie_probe.log' >&2; exit 1
fi
printf 'Report saved: %s/rk3568_junction_live_deploy/pcie_probe.log\n' "$PROBE_ROOT"
if [[ "$PROBE_TRANSPORT" != 0 ]] || ! grep -qx 'PROBE_PROCESS_EXIT=0' "$PROBE_TMP/response"; then
    echo 'MMIO probe failed; its diagnostics were saved.' >&2; exit 1
fi
echo 'JUNCTION_PCIE_DIAG_DONE (collection complete; this is not a hardware PASS)'
