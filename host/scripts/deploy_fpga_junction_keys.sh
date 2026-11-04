#!/usr/bin/env bash
set -euo pipefail
KEY_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
KEY_HDC="${HDC:-$HOME/openharmony/prebuilts/ohos-sdk/linux/11/toolchains/hdc}"
KEY_TARGET="${TARGET:-7001005458323933328a26cf4db23900}"
KEY_BDF="${FPGA_BDF:-0002:21:00.0}"
KEY_REMOTE=/data/local/tmp/fpga_junction_keys
[[ "$KEY_BDF" =~ ^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$ ]]
key_hdc() { sudo -n "$KEY_HDC" -t "$KEY_TARGET" "$@"; }
trap 'printf "FPGA_KEYS_DEPLOY_FAILED line=%s\n" "$LINENO" >&2' ERR
sudo -n true
python3 "$KEY_ROOT/tests/test_fpga_junction_keys.py"
python3 - "$KEY_ROOT/rk3568_fpga_keys_deploy" <<'PY'
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]);metadata=json.loads((root/'manifest.json').read_text())
data=(root/'rk_fpga_keys').read_bytes()
if (data[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(data[18:20],'little')!=183
    or hashlib.sha256(data).hexdigest()!=metadata['rk_fpga_keys_sha256']):
    raise SystemExit('Key adapter artifact mismatch')
print('FPGA_KEY_ARTIFACT_PASS')
PY
key_hdc shell mkdir -p "$KEY_REMOTE"
for KEY_FILE in rk_fpga_keys run_fpga_junction_keys_board.sh; do
    KEY_LOCAL="$KEY_ROOT/$KEY_FILE"
    [[ "$KEY_FILE" != rk_fpga_keys ]] || KEY_LOCAL="$KEY_ROOT/rk3568_fpga_keys_deploy/$KEY_FILE"
    key_hdc file send "$KEY_LOCAL" "$KEY_REMOTE/$KEY_FILE.upload"
    key_hdc shell mv -f "$KEY_REMOTE/$KEY_FILE.upload" "$KEY_REMOTE/$KEY_FILE"
    key_hdc shell chmod 755 "$KEY_REMOTE/$KEY_FILE"
done
KEY_RESPONSE="$(key_hdc shell sh "$KEY_REMOTE/run_fpga_junction_keys_board.sh" start "$KEY_BDF")"
printf '%s\n' "$KEY_RESPONSE"
grep -q '^FPGA_KEYS_BOARD_START_PASS' <<<"$KEY_RESPONSE"
KEY_FORWARDS="$(key_hdc fport ls)"
if ! grep -Eq 'tcp:9016[[:space:]]+tcp:9014([[:space:]]|$)' <<<"$KEY_FORWARDS"; then
    if [[ -n "$(ss -H -ltn 'sport = :9016')" ]]; then echo 'Ubuntu 9016 occupied';exit 1;fi
    key_hdc fport tcp:9016 tcp:9014
fi
python3 - <<'PY'
import json,socket
with socket.create_connection(('127.0.0.1',9016),timeout=2) as link:
    data=b''
    while b'\n' not in data:
        part=link.recv(512)
        if not part or len(data)>1024:raise SystemExit('Invalid key response')
        data+=part
    snapshot=json.loads(data)
    if snapshot.get('valid') is not True or snapshot.get('version')!=1:raise SystemExit(snapshot)
print('FPGA_KEYS_LINK_PASS')
PY
printf 'FPGA_KEYS_DEPLOY_PASS\nOpen: bash /mnt/hgfs/pcie_host/preview_front_fpga_keys.sh\n'
