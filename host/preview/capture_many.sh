#!/bin/sh
set -eu

frame_count=${1:-30}
interval_seconds=${2:-1}
output_directory=${3:-/data/local/tmp/pcie_frames_run1}
capture_device=${4:-/dev/pango_pcie_frame_0002:21:00.0}

case "$frame_count" in
    ''|*[!0-9]*|?????*)
        echo "ERROR: frame count must be an integer from 1 to 1000" >&2
        exit 1
        ;;
esac
if [ "$frame_count" -lt 1 ] || [ "$frame_count" -gt 1000 ]; then
    echo "ERROR: frame count must be an integer from 1 to 1000" >&2
    exit 1
fi
case "$interval_seconds" in
    ''|*[!0-9]*)
        echo "ERROR: interval must be a nonnegative integer in seconds" >&2
        exit 1
        ;;
esac
if [ ! -c "$capture_device" ]; then
    echo "ERROR: capture device is missing: $capture_device" >&2
    exit 1
fi

mkdir -p "$output_directory"
frame_index=1
while [ "$frame_index" -le "$frame_count" ]; do
    image_name=$(printf 'frame_%06d.pgm' "$frame_index")
    image_path="$output_directory/$image_name"
    if ! (set -C; cat "$capture_device" > "$image_path"); then
        echo "ERROR: capture failed at $image_name; inspect dmesg and use a new output directory for the next run" >&2
        exit 1
    fi
    image_bytes=$(wc -c < "$image_path")
    if [ "$image_bytes" -ne 2073617 ]; then
        echo "ERROR: invalid image size at $image_name: $image_bytes" >&2
        exit 1
    fi
    echo "Captured $frame_index/$frame_count: $image_path ($image_bytes bytes)"
    if [ "$frame_index" -lt "$frame_count" ]; then
        sleep "$interval_seconds"
    fi
    frame_index=$((frame_index + 1))
done

echo "Capture batch completed: $frame_count frames in $output_directory"
