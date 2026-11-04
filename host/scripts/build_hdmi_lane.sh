#!/usr/bin/env bash
set -euo pipefail
LANE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LANE_OUTPUT="${LANE_BUILD_DIR:-$LANE_ROOT/rk3568_lane_deploy}"
LANE_HEADER="${RKNN_HEADER_DIR:-$LANE_ROOT/rk3568_lane_deploy}"
LANE_CC="${RKNN_CC:-aarch64-linux-gnu-gcc}"
LANE_SERVER="${LANE_SERVER_NAME:-pcie_multivideo_server.lane_v1}"
case "$LANE_SERVER" in pcie_multivideo_server.lane_v1|pcie_multivideo_server.lane_v2|pcie_multivideo_server.lane_v3|pcie_multivideo_server.scene_v4|pcie_multivideo_server.scene_sync_v5|pcie_multivideo_server.drive_v6|pcie_multivideo_server.front_v1|pcie_multivideo_server.front_p1) ;; *) exit 1 ;; esac
LANE_FLAGS=()
LANE_EXTRA_SOURCES=()
if [[ "$LANE_SERVER" == pcie_multivideo_server.lane_v3 ]]; then LANE_FLAGS+=(-DLANE_DIAGNOSTIC_BUILD); fi
if [[ "$LANE_SERVER" == pcie_multivideo_server.scene_v4 || "$LANE_SERVER" == pcie_multivideo_server.scene_sync_v5 || "$LANE_SERVER" == pcie_multivideo_server.drive_v6 || "$LANE_SERVER" == pcie_multivideo_server.front_* ]]; then
    LANE_FLAGS+=(-DHAVE_ROAD_OBJECTS)
    LANE_EXTRA_SOURCES+=("$LANE_ROOT/road_object_core.c" "$LANE_ROOT/road_object_rknn.c" "$LANE_ROOT/rknn_scheduler.c")
    if [[ "$LANE_SERVER" == pcie_multivideo_server.drive_v6 ]]; then
        LANE_FLAGS+=(-DHAVE_MULTI_SCENE)
        LANE_EXTRA_SOURCES+=("$LANE_ROOT/road_multi_async.c")
    else LANE_EXTRA_SOURCES+=("$LANE_ROOT/road_object_async.c"); fi
    if [[ "$LANE_SERVER" == pcie_multivideo_server.front_* ]]; then
        LANE_FLAGS+=(-DHAVE_FRONT_ASSIST)
    fi
    if [[ "$LANE_SERVER" == pcie_multivideo_server.front_p1 ]]; then
        LANE_FLAGS+=(-DHAVE_FRONT_P1)
        LANE_EXTRA_SOURCES+=("$LANE_ROOT/crosswalk_rknn.c")
    fi
fi
if [[ "$LANE_SERVER" == pcie_multivideo_server.scene_sync_v5 || "$LANE_SERVER" == pcie_multivideo_server.drive_v6 ]]; then
    LANE_FLAGS+=(-DHAVE_SYNC_SESSION)
    LANE_EXTRA_SOURCES+=("$LANE_ROOT/sync_receiver_core.c" "$LANE_ROOT/sync_capture.c")
fi
mkdir -p "$LANE_OUTPUT"
test -f "$LANE_HEADER/rknn_api.h"
gcc -O2 -std=c11 -Wall -Wextra -Werror -I"$LANE_ROOT" \
    "$LANE_ROOT/tests/lane_style_core_test.c" "$LANE_ROOT/lane_style_core.c" \
    -o "$LANE_OUTPUT/lane_style_core_test" -lm
"$LANE_OUTPUT/lane_style_core_test"
gcc -O2 -std=c11 -Wall -Wextra -Werror -I"$LANE_ROOT" \
    "$LANE_ROOT/tests/lane_style_diagnostics_test.c" "$LANE_ROOT/lane_style_core.c" \
    -o "$LANE_OUTPUT/lane_style_diagnostics_test" -lm
"$LANE_OUTPUT/lane_style_diagnostics_test"
gcc -O2 -std=c11 -Wall -Wextra -Werror -I"$LANE_ROOT" -I"$LANE_HEADER" \
    "$LANE_ROOT/tests/lane_style_tensor_test.c" -o "$LANE_OUTPUT/lane_style_tensor_test"
"$LANE_OUTPUT/lane_style_tensor_test"
gcc -O2 -std=c11 -Wall -Wextra -Werror -I"$LANE_ROOT" \
    "$LANE_ROOT/tests/lane_style_decode_test.c" "$LANE_ROOT/lane_style_core.c" -o "$LANE_OUTPUT/lane_style_decode_test" -lm
"$LANE_OUTPUT/lane_style_decode_test"
gcc -O2 -std=c11 -Wall -Wextra -Werror -I"$LANE_ROOT" \
    "$LANE_ROOT/tests/lane_style_async_test.c" "$LANE_ROOT/lane_style_core.c" -o "$LANE_OUTPUT/lane_style_async_test" -lm -pthread
"$LANE_OUTPUT/lane_style_async_test"
if [[ "$LANE_SERVER" == pcie_multivideo_server.scene_v4 || "$LANE_SERVER" == pcie_multivideo_server.scene_sync_v5 || "$LANE_SERVER" == pcie_multivideo_server.drive_v6 || "$LANE_SERVER" == pcie_multivideo_server.front_* ]]; then
    gcc -O2 -std=c11 -Wall -Wextra -Werror -I"$LANE_ROOT" \
        "$LANE_ROOT/tests/road_object_core_test.c" "$LANE_ROOT/road_object_core.c" -o "$LANE_OUTPUT/road_object_core_test" -lm
    "$LANE_OUTPUT/road_object_core_test"
    gcc -O2 -std=c11 -Wall -Wextra -Werror -I"$LANE_ROOT" \
        "$LANE_ROOT/tests/road_object_async_test.c" "$LANE_ROOT/road_object_core.c" -o "$LANE_OUTPUT/road_object_async_test" -lm -pthread
    "$LANE_OUTPUT/road_object_async_test"
fi
if [[ "$LANE_SERVER" == pcie_multivideo_server.drive_v6 ]]; then
    gcc -O2 -std=c11 -Wall -Wextra -Werror -I"$LANE_ROOT" "$LANE_ROOT/tests/rknn_scheduler_test.c" -o "$LANE_OUTPUT/rknn_scheduler_test" -pthread
    "$LANE_OUTPUT/rknn_scheduler_test"
    gcc -O2 -std=c11 -Wall -Wextra -Werror -I"$LANE_ROOT" -I"$LANE_HEADER" \
        "$LANE_ROOT/tests/rknn_reserved_test.c" "$LANE_ROOT/lane_style_core.c" "$LANE_ROOT/road_object_core.c" \
        -o "$LANE_OUTPUT/rknn_reserved_test" -ldl -lm -pthread
    "$LANE_OUTPUT/rknn_reserved_test"
    gcc -O2 -std=c11 -Wall -Wextra -Werror -DHAVE_MULTI_SCENE -I"$LANE_ROOT" \
        "$LANE_ROOT/tests/lane_style_async_test.c" "$LANE_ROOT/lane_style_core.c" -o "$LANE_OUTPUT/lane_style_scheduled_test" -lm -pthread
    "$LANE_OUTPUT/lane_style_scheduled_test"
    gcc -O2 -std=c11 -Wall -Wextra -Werror -I"$LANE_ROOT" "$LANE_ROOT/tests/drive_assist_test.c" "$LANE_ROOT/drive_assist.c" -o "$LANE_OUTPUT/drive_assist_test"
    "$LANE_OUTPUT/drive_assist_test"
    gcc -O2 -std=c11 -Wall -Wextra -Werror -I"$LANE_ROOT" "$LANE_ROOT/tests/road_multi_geometry_test.c" "$LANE_ROOT/road_object_core.c" -o "$LANE_OUTPUT/road_multi_geometry_test" -lm
    "$LANE_OUTPUT/road_multi_geometry_test"
    gcc -O2 -std=c11 -Wall -Wextra -Werror -I"$LANE_ROOT" "$LANE_ROOT/tests/road_multi_async_test.c" "$LANE_ROOT/road_object_core.c" -o "$LANE_OUTPUT/road_multi_async_test" -lm -pthread
    "$LANE_OUTPUT/road_multi_async_test"
fi
if [[ "$LANE_SERVER" == pcie_multivideo_server.front_* ]]; then
    gcc -O2 -std=c11 -Wall -Wextra -Werror -DFRONT_ONLY -I"$LANE_ROOT" \
        "$LANE_ROOT/tests/front_drive_assist_test.c" "$LANE_ROOT/drive_assist.c" \
        -o "$LANE_OUTPUT/front_drive_assist_test"
    "$LANE_OUTPUT/front_drive_assist_test"
fi
if [[ "$LANE_SERVER" == pcie_multivideo_server.front_p1 ]]; then
    gcc -O2 -UNDEBUG -std=c11 -Wall -Wextra -Werror -DHAVE_FRONT_P1 -I"$LANE_ROOT" \
        "$LANE_ROOT/tests/road_object_async_test.c" "$LANE_ROOT/road_object_core.c" -lm -pthread -o "$LANE_OUTPUT/front_object_scheduled_test"
    "$LANE_OUTPUT/front_object_scheduled_test"
    gcc -O2 -UNDEBUG -std=c11 -Wall -Wextra -Werror -DHAVE_FRONT_P1 -I"$LANE_ROOT" \
        "$LANE_ROOT/tests/lane_style_async_test.c" "$LANE_ROOT/lane_style_core.c" -lm -pthread -o "$LANE_OUTPUT/front_lane_scheduled_test"
    "$LANE_OUTPUT/front_lane_scheduled_test"
    gcc -O2 -UNDEBUG -std=c11 -Wall -Wextra -Werror -I"$LANE_ROOT" -I"$LANE_HEADER" \
        "$LANE_ROOT/tests/rknn_reserved_test.c" "$LANE_ROOT/lane_style_core.c" "$LANE_ROOT/road_object_core.c" -ldl -lm -pthread -o "$LANE_OUTPUT/front_reserved_test"
    "$LANE_OUTPUT/front_reserved_test"
fi
"$LANE_CC" -O2 -std=c11 -Wall -Wextra -Werror -DHAVE_LANE_STYLE "${LANE_FLAGS[@]}" \
    -I"$LANE_HEADER" "$LANE_ROOT/pcie_multivideo_server.c" "$LANE_ROOT/video_mosaic.c" \
    "$LANE_ROOT/lane_style_core.c" "$LANE_ROOT/lane_style_rknn.c" "$LANE_ROOT/lane_style_async.c" \
    "${LANE_EXTRA_SOURCES[@]}" \
    -o "$LANE_OUTPUT/$LANE_SERVER.tmp" -ldl -lm -pthread
mv -- "$LANE_OUTPUT/$LANE_SERVER.tmp" "$LANE_OUTPUT/$LANE_SERVER"
file "$LANE_OUTPUT/$LANE_SERVER"
printf 'LANE_BUILD_PASS\n'
