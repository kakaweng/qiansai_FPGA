#!/usr/bin/env bash
set -euo pipefail
PIE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
printf 'PIE v2：RK3568 CPU 实验分数；采样与动作条件状态在左下卡片显示。\n'
exec bash "$PIE_ROOT/preview_front_p1.sh" "$@"
