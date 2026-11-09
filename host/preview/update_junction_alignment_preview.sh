#!/usr/bin/env bash
set -euo pipefail
ALIGNMENT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
printf 'Checking pedestrian visibility, motion behavior and source-band association; reusing running board service and models.\n'
python3 "$ALIGNMENT_ROOT/tests/test_junction_association.py"
python3 "$ALIGNMENT_ROOT/tests/test_pedestrian_behavior.py"
python3 "$ALIGNMENT_ROOT/tests/test_junction_presentation.py"
python3 "$ALIGNMENT_ROOT/tests/test_junction_route.py"
python3 "$ALIGNMENT_ROOT/tests/test_junction_perception.py"
python3 "$ALIGNMENT_ROOT/tests/test_front_anticipation_preview.py"
printf '\nJUNCTION_ALIGNMENT_PREVIEW_PASS\n'
printf 'Close the old preview, then open:\nbash %q/preview_pie_anticipation.sh\n' "$ALIGNMENT_ROOT"
