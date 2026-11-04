#!/bin/sh
set -eu
export SYNC_SERVER=pcie_multivideo_server.front_p1
export SYNC_CONTROLLER=rk_front_p1_control
exec sh /data/local/tmp/hdmi_lane_v1/run_sync_board.sh "$@"
