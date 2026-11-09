#!/usr/bin/env bash
set -euo pipefail
KEY_PREVIEW_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
printf 'FPGA_KEY_PREVIEW_VERSION route-display-v2\n'
python3 - "$KEY_PREVIEW_ROOT" <<'PY'
import json,socket,sys
sys.path.insert(0,sys.argv[1])
from fpga_junction_keys import KeyEvents
try:
    with socket.create_connection(('127.0.0.1',9016),timeout=2) as link:
        data=b''
        while b'\n' not in data:
            part=link.recv(512)
            if not part or len(data)>1024:raise ValueError('incomplete response')
            data+=part
    state=KeyEvents();state.update(json.loads(data))
    if state.generation is None:raise ValueError('invalid key capability')
except (OSError,ValueError) as error:
    raise SystemExit('FPGA key service not ready; run deploy_fpga_junction_keys.sh first: '+str(error))
print('FPGA_KEY_PREVIEW_LINK_PASS')
PY
printf 'FPGA direction keys: KEY5 left / KEY6 straight / KEY7 right. KEY1..4 keep original video controls. N analyze, G play.\n'
exec bash "$KEY_PREVIEW_ROOT/preview_pie_anticipation.sh" --fpga-keys-port 9016 "$@"
