#!/usr/bin/env bash
set -euo pipefail
JUNCTION_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
JUNCTION_WORK="$HOME/pcie_junction_live_build"
JUNCTION_OUTPUT="$JUNCTION_ROOT/rk3568_junction_live_deploy"
mkdir -p "$JUNCTION_WORK/tests" "$JUNCTION_OUTPUT"
for JUNCTION_FILE in pcie_multivideo_server.c pcie_multivideo_protocol.h video_mosaic.c video_mosaic.h \
    junction_demo.c junction_demo.h junction_demo_core.c junction_demo_core.h \
    road_object_core.c road_object_core.h road_object_rknn.c road_object_rknn.h rknn_scheduler.c rknn_scheduler.h \
    tests/junction_demo_core_test.c tests/junction_demo_async_test.c; do
    cp -- "$JUNCTION_ROOT/$JUNCTION_FILE" "$JUNCTION_WORK/$JUNCTION_FILE"
done
cp -- "$JUNCTION_ROOT/rk3568_lane_deploy/rknn_api.h" "$JUNCTION_WORK/rknn_api.h"
cd "$JUNCTION_WORK"
gcc -O2 -std=c11 -Wall -Wextra -Werror tests/junction_demo_core_test.c junction_demo_core.c -lm -o core_test
./core_test
gcc -O2 -std=c11 -Wall -Wextra -Werror tests/junction_demo_async_test.c junction_demo.c junction_demo_core.c -lm -pthread -o async_test
./async_test
"${JUNCTION_CC:-aarch64-linux-gnu-gcc}" -O2 -std=c11 -Wall -Wextra -Werror -DHAVE_JUNCTION_DEMO \
    pcie_multivideo_server.c video_mosaic.c junction_demo.c junction_demo_core.c road_object_core.c \
    road_object_rknn.c rknn_scheduler.c -ldl -lm -pthread -o pcie_multivideo_server.junction_live
cp -- pcie_multivideo_server.junction_live "$JUNCTION_OUTPUT/pcie_multivideo_server.junction_live"
python3 - "$JUNCTION_OUTPUT" <<'PY'
import hashlib, json, sys
from pathlib import Path
directory = Path(sys.argv[1])
data = (directory / 'pcie_multivideo_server.junction_live').read_bytes()
if data[:6] != b'\x7fELF\x02\x01' or int.from_bytes(data[18:20], 'little') != 183:
    raise SystemExit('Expected ARM64 ELF')
(directory / 'manifest.json').write_text(json.dumps(dict(version='multivideo-waymo-junction-v1',
    binary_sha256=hashlib.sha256(data).hexdigest(), fpga_bitstream='unchanged'), indent=2)+'\n')
PY
printf 'JUNCTION_BUILD_PASS\n'
