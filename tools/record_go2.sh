#!/usr/bin/env bash
# Record one Go2 patrol bag with the topics the auditor actually needs.
#
# Usage:
#   ./tools/record_go2.sh [bag_name] [extra topics...]
#
# Defaults are deliberately lean: the base_link cloud (not the three larger
# copies), odometry, IMU, the pass events, and TF if the robot publishes it.
# Start this BEFORE driving the loop, then stop it with Ctrl-C.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

NAME="${1:-lab_$(date +%H%M%S)}"
if [ "$#" -gt 0 ]; then shift; fi

# Never overwrite an existing bag: ros2 bag record exits if the folder exists.
if [ -e "bags/${NAME}" ]; then
  BASE="$NAME"
  n=2
  while [ -e "bags/${NAME}" ]; do
    NAME="${BASE}_${n}"
    n=$((n + 1))
  done
  echo "note: bags/${BASE} already exists; recording to bags/${NAME}" >&2
fi

EXTRA=""
for topic in "$@"; do
  EXTRA="$EXTRA $topic"
done

exec sudo docker compose run --rm ros bash -lc "
  source /opt/ros/humble/setup.bash
  source /ws/install/setup.bash
  exec ros2 bag record -o /ws/bags/${NAME} \
    /utlidar/cloud_base \
    /utlidar/robot_odom \
    /utlidar/imu \
    /auditor/pass_complete \
    /tf /tf_static \
    ${EXTRA}
"
