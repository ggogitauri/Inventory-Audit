#!/usr/bin/env bash
#
# One-command lab preflight. Runs inside the ROS container, so the host needs
# only Docker + Compose — no ROS, no Python packages, no GPU.
#
#   ./tools/preflight.sh
#   ./tools/preflight.sh --check-upload
#   ./tools/preflight.sh --scan-topic /scan --base-frame base_footprint
#
# It is read-only unless you pass --check-upload, which publishes one synthetic
# pass in the area "preflight-test" and verifies it reaches the backend.
#
# Reports are written to ros_ws/robot_discovery/preflight-<timestamp>/ on the
# host (mounted at /ws in the container).

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found; install Docker Engine + Compose v2" >&2
  exit 127
fi
if [ ! -f docker-compose.yml ]; then
  echo "run this from the repository root (docker-compose.yml not found)" >&2
  exit 1
fi

if ! docker compose ps --services --status running 2>/dev/null | grep -qx api; then
  echo "note: the api service is not running; preflight will report it" >&2
  echo "      start it with: docker compose up -d api" >&2
fi

docker compose run --rm ros bash -lc '
  source /opt/ros/humble/setup.bash
  if [ ! -f /ws/install/setup.bash ]; then
    echo "workspace not built yet. Run:" >&2
    echo "  docker compose run --rm ros bash -lc \"cd /ws && colcon build --symlink-install\"" >&2
    exit 2
  fi
  source /ws/install/setup.bash
  exec ros2 run go2_auditor preflight "$@"
' _ "$@"
