#!/usr/bin/env bash
set -euo pipefail
P1_TEST_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
P1_TEST_OUTPUT="$HOME/pcie_sync_rk3568/p1_tests"
mkdir -p "$P1_TEST_OUTPUT"
gcc -O2 -std=c11 -Wall -Wextra -Werror -DFRONT_ONLY -DHAVE_FRONT_P1 -I"$P1_TEST_ROOT" \
    "$P1_TEST_ROOT/tests/front_p1_test.c" "$P1_TEST_ROOT/front_p1.c" "$P1_TEST_ROOT/drive_assist.c" -o "$P1_TEST_OUTPUT/front_p1_test"
"$P1_TEST_OUTPUT/front_p1_test"
gcc -O2 -std=c11 -Wall -Wextra -Werror -DFRONT_ONLY -DHAVE_FRONT_P1 -I"$P1_TEST_ROOT" \
    "$P1_TEST_ROOT/tests/front_junction_test.c" "$P1_TEST_ROOT/front_p1.c" "$P1_TEST_ROOT/drive_assist.c" -o "$P1_TEST_OUTPUT/front_junction_test"
"$P1_TEST_OUTPUT/front_junction_test"
gcc -O2 -std=c11 -Wall -Wextra -Werror -DHAVE_FRONT_P1 -I"$P1_TEST_ROOT" \
    "$P1_TEST_ROOT/tests/road_object_core_test.c" "$P1_TEST_ROOT/road_object_core.c" -lm -o "$P1_TEST_OUTPUT/front_p1_lamp_test"
"$P1_TEST_OUTPUT/front_p1_lamp_test"
gcc -O2 -std=c11 -Wall -Wextra -Werror -DHAVE_FRONT_P1 -I"$P1_TEST_ROOT" \
    "$P1_TEST_ROOT/tests/front_p1_render_test.c" "$P1_TEST_ROOT/road_object_core.c" "$P1_TEST_ROOT/lane_style_core.c" \
    -lm -o "$P1_TEST_OUTPUT/front_p1_render_test"
"$P1_TEST_OUTPUT/front_p1_render_test"
gcc -O2 -std=c11 -Wall -Wextra -Werror -DFRONT_ONLY -DHAVE_FRONT_P1 -DHAVE_DRIVE_ASSIST \
    "$P1_TEST_ROOT/rk_sync_control.c" "$P1_TEST_ROOT/front_p1.c" "$P1_TEST_ROOT/drive_assist.c" -o "$P1_TEST_OUTPUT/rk_front_p1_control_test"
FRONT_P1_TEST_CONTROLLER="$P1_TEST_OUTPUT/rk_front_p1_control_test" python3 "$P1_TEST_ROOT/tests/test_front_p1.py"
FRONT_P1_TEST_CONTROLLER="$P1_TEST_OUTPUT/rk_front_p1_control_test" python3 "$P1_TEST_ROOT/tests/test_junction_scene.py"
gcc -O2 -std=c11 -Wall -Wextra -Werror -I"$P1_TEST_ROOT" "$P1_TEST_ROOT/tests/front_world_test.c" -lm -o "$P1_TEST_OUTPUT/front_world_test"
"$P1_TEST_OUTPUT/front_world_test"
FRONT_P1_TEST_CONTROLLER="$P1_TEST_OUTPUT/rk_front_p1_control_test" python3 "$P1_TEST_ROOT/tests/test_live_road.py"
python3 "$P1_TEST_ROOT/tests/test_junction_perception.py"
python3 "$P1_TEST_ROOT/tests/test_right_turn_simplified.py"
python3 "$P1_TEST_ROOT/tests/test_live_crosswalk.py"
python3 "$P1_TEST_ROOT/tests/test_hybrid_junction.py"
python3 "$P1_TEST_ROOT/tests/test_crosswalk_relations.py"
python3 "$P1_TEST_ROOT/tests/test_pedestrian_display.py"
python3 "$P1_TEST_ROOT/tests/test_junction_presentation.py"
python3 "$P1_TEST_ROOT/tests/test_junction_association.py"
python3 "$P1_TEST_ROOT/tests/test_pedestrian_behavior.py"
python3 "$P1_TEST_ROOT/tests/test_frozen_junction.py"
python3 "$P1_TEST_ROOT/tests/test_junction_route.py"
python3 "$P1_TEST_ROOT/tests/test_live_display_history.py"
gcc -O2 -UNDEBUG -std=c11 -Wall -Wextra -Werror "$P1_TEST_ROOT/tests/pedestrian_gate_test.c" \
    -o "$P1_TEST_OUTPUT/pedestrian_gate_test"
"$P1_TEST_OUTPUT/pedestrian_gate_test"
gcc -O2 -UNDEBUG -std=c11 -Wall -Wextra -Werror "$P1_TEST_ROOT/tests/rknn_scheduler_test.c" \
    -pthread -o "$P1_TEST_OUTPUT/rknn_scheduler_test"
"$P1_TEST_OUTPUT/rknn_scheduler_test"
gcc -O2 -std=c11 -Wall -Wextra -Werror -DFRONT_ONLY -DHAVE_FRONT_P1 -DHAVE_DRIVE_ASSIST -DHAVE_CROSSWALK \
    "$P1_TEST_ROOT/rk_sync_control.c" "$P1_TEST_ROOT/front_p1.c" "$P1_TEST_ROOT/drive_assist.c" -o "$P1_TEST_OUTPUT/rk_front_crosswalk_control_test"
CROSSWALK_TEST_CONTROLLER="$P1_TEST_OUTPUT/rk_front_crosswalk_control_test" python3 "$P1_TEST_ROOT/tests/test_pedestrian_gate.py"
CROSSWALK_TEST_CONTROLLER="$P1_TEST_OUTPUT/rk_front_crosswalk_control_test" python3 "$P1_TEST_ROOT/tests/test_display_world.py"
gcc -O2 -std=c11 -Wall -Wextra -Werror "$P1_TEST_ROOT/tests/crosswalk_core_test.c" \
    -lm -o "$P1_TEST_OUTPUT/crosswalk_core_test"
"$P1_TEST_OUTPUT/crosswalk_core_test"
gcc -O2 -std=c11 -Wall -Wextra -Werror -shared -fPIC -DCROSSWALK_CORE_ONLY \
    "$P1_TEST_ROOT/crosswalk_rknn.c" -lm -o "$P1_TEST_OUTPUT/crosswalk_core_test.so"
CROSSWALK_TEST_LIBRARY="$P1_TEST_OUTPUT/crosswalk_core_test.so" python3 "$P1_TEST_ROOT/tests/crosswalk_preprocess_test.py"
CROSSWALK_TEST_CONTROLLER="$P1_TEST_OUTPUT/rk_front_crosswalk_control_test" python3 "$P1_TEST_ROOT/tests/crosswalk_controller_test.py"
gcc -O2 -std=c11 -Wall -Wextra -Werror -shared -fPIC -I"$P1_TEST_ROOT/rk3568_lane_deploy" \
    "$P1_TEST_ROOT/tests/crosswalk_fake_rknn.c" -pthread -o "$P1_TEST_OUTPUT/crosswalk_fake_rknn.so"
gcc -O2 -std=c11 -Wall -Wextra -Werror -I"$P1_TEST_ROOT/rk3568_lane_deploy" \
    "$P1_TEST_ROOT/tests/crosswalk_async_test.c" -lm -ldl -pthread -o "$P1_TEST_OUTPUT/crosswalk_async_test"
"$P1_TEST_OUTPUT/crosswalk_async_test" "$P1_TEST_OUTPUT/crosswalk_fake_rknn.so"
python3 "$P1_TEST_ROOT/tests/test_drive_preview.py"
printf 'FRONT_P1_TEST_PASS\n'
