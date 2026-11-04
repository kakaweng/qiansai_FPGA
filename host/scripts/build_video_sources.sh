#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${PREVIEW_ZIG:-}" ]]; then
    "$PREVIEW_ZIG" cc -target aarch64-linux-musl -static -O2 -std=c11 \
        -Wall -Wextra -Werror "$SCRIPT_DIR/pcie_video_sources.c" \
        -o "$SCRIPT_DIR/pcie_video_sources"
else
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
    if [[ ! -f "$NATIVE_DIR/sysroot/usr/include/stdio.h" ]]; then
        printf '%s\n' '请设置 OHOS_NATIVE=/实际路径/native，或 PREVIEW_ZIG=/实际路径/zig。' >&2
        exit 1
    fi
    COMPILER="${PREVIEW_CC:-$NATIVE_DIR/llvm/bin/clang}"
    if [[ ! -x "$COMPILER" ]]; then
        COMPILER="$HOME/openharmony/prebuilts/clang/ohos/linux-x86_64/15.0.4/llvm/bin/clang"
    fi
    "$COMPILER" --target=aarch64-linux-ohos --sysroot="$NATIVE_DIR/sysroot" \
        -static -O2 -std=c11 -Wall -Wextra -Werror \
        "$SCRIPT_DIR/pcie_video_sources.c" -o "$SCRIPT_DIR/pcie_video_sources"
fi
file "$SCRIPT_DIR/pcie_video_sources"
