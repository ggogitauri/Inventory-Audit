#!/usr/bin/env bash
# Preflight wrapper for this Go2's discovered topics.
# Usage: ./tools/preflight_go2.sh [--check-upload]
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
exec ./tools/preflight.sh \
  --odom-topic /utlidar/robot_odom \
  --scan-topic /utlidar/cloud_base \
  --map-frame odom \
  --base-frame base_link \
  "$@"
