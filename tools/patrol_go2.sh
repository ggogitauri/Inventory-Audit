#!/usr/bin/env bash
# Launch the passive coordinator + uploader for this Go2.
# Usage: ./tools/patrol_go2.sh [extra launch args, e.g. name:=run1 n_passes:=3]
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
EXTRA="$*"
exec sudo docker compose run --rm ros bash -lc "
  source /opt/ros/humble/setup.bash
  source /ws/install/setup.bash
  ros2 launch go2_auditor patrol.launch.py \
    route_file:=/ws/src/go2_auditor/config/patrol_route.yaml \
    odom_topic:=/utlidar/robot_odom \
    map_frame:=odom \
    api_url:=http://localhost:8000 \
    ${EXTRA}
"
