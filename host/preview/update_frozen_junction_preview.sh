#!/usr/bin/env bash
set -euo pipefail
FROZEN_PREVIEW_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
trap 'printf "FROZEN_JUNCTION_PREVIEW_FAILED line=%s\n" "$LINENO" >&2' ERR
printf 'Checking frozen-scene capture and causal trajectory rehearsal; board service/models stay in use.\n'
printf 'Pause check: ignore known board boxes/lane paint; retain source-motion and timestamp pairing checks.\n'
bash "$FROZEN_PREVIEW_ROOT/update_junction_alignment_preview.sh"
python3 "$FROZEN_PREVIEW_ROOT/tests/test_frozen_junction.py"
printf '\nFROZEN_JUNCTION_PREVIEW_PASS\n'
printf 'Open: bash %q/preview_pie_anticipation.sh\n' "$FROZEN_PREVIEW_ROOT"
printf 'Pause Windows source video first. M: scene, N: analyze, J/I/L: direction, G: play, B: pause, R: replay, M: return.\n'
