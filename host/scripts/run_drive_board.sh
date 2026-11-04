#!/bin/sh
set -eu
export SYNC_SERVER=pcie_multivideo_server.drive_v6
export SYNC_CONTROLLER=rk_drive_control
exec sh /data/local/tmp/hdmi_lane_v1/run_sync_board.sh "$@"
