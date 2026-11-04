#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDK_ROOT="${RKNN_SDK_ROOT:-$HOME/rknn-toolkit2-npu/rknpu2}"
HEADER_DIR="${RKNN_HEADER_DIR:-$SDK_ROOT/runtime/Linux/librknn_api/include}"
RUNTIME="${RKNN_RUNTIME:-$HOME/rknn-yolov5-test/lib/librknnrt.so}"
OUTPUT="${RKNN_PROBE_OUTPUT:-$ROOT/pcie_rknn_probe}"

if ! command -v strings >/dev/null 2>&1; then
    printf '缺少 strings，无法检查彩色程序版本；请安装 binutils。\n' >&2
    exit 1
fi

if [[ ! -f "$HEADER_DIR/rknn_api.h" ]]; then
    printf '找不到 rknn_api.h：%s/rknn_api.h\n' "$HEADER_DIR" >&2
    printf '可设置 RKNN_HEADER_DIR=/实际的/include目录后重试。\n' >&2
    exit 1
fi
if [[ ! -f "$RUNTIME" ]]; then
    printf '找不到 ARM64 librknnrt.so：%s\n' "$RUNTIME" >&2
    printf '可设置 RKNN_RUNTIME=/实际的librknnrt.so后重试。\n' >&2
    exit 1
fi

if [[ -n "${RKNN_CC:-}" ]]; then
    COMPILER="$RKNN_CC"
elif command -v aarch64-linux-gnu-gcc >/dev/null 2>&1; then
    COMPILER="$(command -v aarch64-linux-gnu-gcc)"
elif command -v aarch64-linux-gnu-clang >/dev/null 2>&1; then
    COMPILER="$(command -v aarch64-linux-gnu-clang)"
else
    printf '找不到 ARM64 GNU/Linux 编译器。请安装 aarch64-linux-gnu-gcc，或设置 RKNN_CC。\n' >&2
    exit 1
fi

BUILD_OUTPUT="$(mktemp "${OUTPUT}.tmp.XXXXXX")"
trap 'rm -f -- "$BUILD_OUTPUT"' EXIT

"$COMPILER" -O2 -std=c11 -Wall -Wextra -Wpedantic \
    -I"$HEADER_DIR" "$ROOT/pcie_rknn_probe.c" \
    -o "$BUILD_OUTPUT" -ldl -lm -pthread

if ! strings "$BUILD_OUTPUT" | grep -F 'PCIe input formats: GRAY8, RGB565LE;' >/dev/null; then
    printf '构建产物缺少彩色版本标识；请重新同步 pcie_rknn_probe.c。\n' >&2
    exit 1
fi
chmod 755 "$BUILD_OUTPUT"
mv -f -- "$BUILD_OUTPUT" "$OUTPUT"

file "$OUTPUT"
printf '已生成：%s\n' "$OUTPUT"
printf '彩色支持检查通过：GRAY8 / RGB565LE；部署后用 --help 核对 result_version=4 result_header=88。\n'
