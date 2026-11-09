#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${PREVIEW_ZIG:-}" ]]; then
    "$PREVIEW_ZIG" cc -target aarch64-linux-musl -static -O2 -std=c11 \
        -D_POSIX_C_SOURCE=200809L -Wall -Wextra -Werror \
        "$SCRIPT_DIR/pcie_stream_server.c" -o "$SCRIPT_DIR/pcie_stream_server"
    file "$SCRIPT_DIR/pcie_stream_server"
    exit 0
fi

SDK_ROOT="$HOME/openharmony/prebuilts/ohos-sdk"
NATIVE_DIR="${OHOS_NATIVE:-}"

if [[ -z "$NATIVE_DIR" ]]; then
    for candidate in "$SDK_ROOT/linux/11/native" "$SDK_ROOT/linux/native"; do
        if [[ -f "$candidate/sysroot/usr/include/stdio.h" ]]; then
            NATIVE_DIR="$candidate"
            break
        fi
    done
fi

if [[ -z "$NATIVE_DIR" && -d "$SDK_ROOT" ]]; then
    while IFS= read -r header; do
        NATIVE_DIR="${header%/sysroot/usr/include/stdio.h}"
        break
    done < <(find "$SDK_ROOT" -maxdepth 8 -path '*/native/sysroot/usr/include/stdio.h' -print)
fi

if [[ ! -f "$NATIVE_DIR/sysroot/usr/include/stdio.h" ]]; then
    printf '%s\n' '未找到 OpenHarmony Native SDK 的 sysroot。' >&2
    printf '%s\n' '请安装与板端兼容的 Native SDK，然后执行：' >&2
    printf '%s\n' 'OHOS_NATIVE=/实际路径/native bash build_preview.sh' >&2
    exit 1
fi

COMPILER="${PREVIEW_CC:-$NATIVE_DIR/llvm/bin/clang}"
if [[ ! -x "$COMPILER" ]]; then
    COMPILER="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin/clang"
fi
if [[ ! -x "$COMPILER" ]]; then
    printf '找不到 clang：%s\n' "$COMPILER" >&2
    exit 1
fi

printf 'Native SDK：%s\n编译器：%s\n' "$NATIVE_DIR" "$COMPILER"
"$COMPILER" --target=aarch64-linux-ohos \
    --sysroot="$NATIVE_DIR/sysroot" -static -O2 -std=c11 \
    -D_POSIX_C_SOURCE=200809L -Wall -Wextra -Werror \
    "$SCRIPT_DIR/pcie_stream_server.c" -o "$SCRIPT_DIR/pcie_stream_server"
file "$SCRIPT_DIR/pcie_stream_server"
printf '%s\n' '已生成 pcie_stream_server（用户空间程序，无需模块签名）。'
