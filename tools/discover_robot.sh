#!/usr/bin/env bash
#
# Read-only discovery helper for the Unitree Go2 EDU.
#
# It does NOT command the robot. It only lists ROS 2 entities, samples topic
# rates, asks tf2 for the frame tree, and optionally records a short rosbag.
#
# Run it on the machine that has the Unitree ROS 2 bridge (unitree_ros2)
# running, normally a laptop on the robot's network:
#
#   ./tools/discover_robot.sh --ros-setup ~/unitree_ros2/install/setup.bash
#   ./tools/discover_robot.sh --record-seconds 30 --ssh unitree@192.168.123.18
#
# Output goes to ./robot_discovery/<timestamp>/ (git-ignored). Bring the whole
# directory back for integration work.
#
# Safe defaults:
#   - no publishers, no services, no motion commands
#   - 30 s recording, skipped if less than 2 GiB is free
#   - SSH is optional and read-only, with BatchMode (no password prompt)

set -uo pipefail

OUT=""
RECORD_SECONDS=30
DO_BAG=1
SSH_TARGET=""
ROS_SETUP=""

usage() {
  cat <<'EOF'
Usage: discover_robot.sh [options]

Options:
  --out DIR              output directory (default: ./robot_discovery/<timestamp>)
  --record-seconds N     rosbag duration in seconds (default: 30)
  --no-bag               do not record a rosbag
  --ssh USER@HOST        also collect read-only system info from the robot
  --ros-setup FILE       source this setup.bash before using ros2
                         (e.g. ~/unitree_ros2/install/setup.bash)
  -h, --help             show this help

Examples:
  ./tools/discover_robot.sh --ros-setup ~/unitree_ros2/install/setup.bash
  ./tools/discover_robot.sh --no-bag --record-seconds 15
  ./tools/discover_robot.sh --ssh unitree@192.168.123.18
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --out)            OUT="${2:?--out needs a directory}"; shift 2 ;;
    --record-seconds) RECORD_SECONDS="${2:?--record-seconds needs a number}"; shift 2 ;;
    --no-bag)         DO_BAG=0; shift ;;
    --ssh)            SSH_TARGET="${2:?--ssh needs USER@HOST}"; shift 2 ;;
    --ros-setup)      ROS_SETUP="${2:?--ros-setup needs a file}"; shift 2 ;;
    -h|--help)        usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

case "$RECORD_SECONDS" in
  ''|*[!0-9]*) echo "--record-seconds must be a positive integer" >&2; exit 2 ;;
esac
[ "$RECORD_SECONDS" -gt 0 ] || { echo "--record-seconds must be > 0" >&2; exit 2; }

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${OUT:-./robot_discovery/$STAMP}"
mkdir -p "$OUT" || { echo "cannot create $OUT" >&2; exit 1; }
OUT="$(cd "$OUT" && pwd)"

# Optional ROS setup. Sourcing ROS scripts often references unset variables,
# so temporarily disable nounset around it.
if [ -n "$ROS_SETUP" ] && [ ! -f "$ROS_SETUP" ]; then
  echo "warning: --ros-setup file not found: $ROS_SETUP" >&2
fi
if ! command -v ros2 >/dev/null 2>&1; then
  for candidate in "$ROS_SETUP" \
                   /opt/ros/humble/setup.bash \
                   /opt/ros/jazzy/setup.bash \
                   /opt/ros/iron/setup.bash \
                   /opt/ros/foxy/setup.bash; do
    [ -n "$candidate" ] && [ -f "$candidate" ] || continue
    # shellcheck disable=SC1090
    set +u; source "$candidate" >/dev/null 2>&1; set -u
    command -v ros2 >/dev/null 2>&1 && break
  done
fi

HAVE_ROS=0
command -v ros2 >/dev/null 2>&1 && HAVE_ROS=1

have() { command -v "$1" >/dev/null 2>&1; }

capture() {
  local name="$1"; shift
  {
    printf '# %s\n' "$name"
    printf '# started: %s\n' "$(date -Is)"
    printf '# command:'
    printf ' %q' "$@"
    printf '\n\n'
    "$@" 2>&1
    printf '\n# exit: %s\n' "$?"
  } > "$OUT/$name.txt"
}

capture_shell() {
  local name="$1" cmd="$2"
  {
    printf '# %s\n' "$name"
    printf '# started: %s\n' "$(date -Is)"
    printf '# shell: %s\n\n' "$cmd"
    bash -c "$cmd" 2>&1
    printf '\n# exit: %s\n' "$?"
  } > "$OUT/$name.txt"
}

echo "Go2 discovery -> $OUT"

# ---- local host, network, and (optional) robot system info -----------------
capture_shell 00_system '
date -Is
echo
echo "== hostname =="
hostname
echo
echo "== kernel =="
uname -a
echo
echo "== os-release =="
cat /etc/os-release 2>/dev/null || true
echo
echo "== nvidia-smi =="
(nvidia-smi 2>&1 || true)
echo
echo "== jetson_release =="
(jetson_release 2>&1 || true)
echo
echo "== disk =="
df -h 2>/dev/null || true
echo
echo "== memory =="
(free -h 2>/dev/null || true)
'

capture_shell 01_network '
echo "== interfaces =="
(ip -br addr 2>/dev/null || ifconfig -a 2>/dev/null || true)
echo
echo "== routes =="
(ip route 2>/dev/null || route -n 2>/dev/null || true)
echo
echo "== hostname resolution =="
(getent hosts "$(hostname)" 2>/dev/null || true)
'

if [ -n "$SSH_TARGET" ]; then
  {
    echo "# read-only SSH system info: $SSH_TARGET"
    echo "# started: $(date -Is)"
    echo
    timeout 25 ssh -o BatchMode=yes -o ConnectTimeout=5 "$SSH_TARGET" 'bash -s' <<'REMOTE'
set +e
date -Is
echo
echo "== hostname =="
hostname
echo
echo "== kernel =="
uname -a
echo
echo "== os-release =="
cat /etc/os-release 2>/dev/null
echo
echo "== nvidia-smi =="
nvidia-smi 2>&1
echo
echo "== jetson_release =="
jetson_release 2>&1
echo
echo "== interfaces =="
ip -br addr 2>/dev/null
echo
echo "== disk =="
df -h 2>/dev/null
echo
echo "== installed ROS =="
ls -1 /opt/ros 2>/dev/null
REMOTE
    echo
    echo "# exit: $?"
  } > "$OUT/02_ssh_robot.txt" 2>&1
fi

# ---- ROS environment and graph --------------------------------------------
if [ "$HAVE_ROS" -eq 1 ]; then
  capture_shell 03_ros_env '
env | grep -E "^(ROS|RMW|CYCLONEDDS|AMENT|COLCON|LD_LIBRARY_PATH|PYTHONPATH)=" | sort
echo
echo "== ros2 version =="
ros2 --help 2>&1 | head -n 5
echo
echo "== Unitree/Go2 packages =="
ros2 pkg list 2>/dev/null | grep -Ei "unitree|go2" || true
'
  capture_shell 03b_ros_doctor 'timeout 25 ros2 doctor --report 2>&1 || true'
  capture 04_nodes ros2 node list
  capture 05_topics ros2 topic list -t
  capture 06_services ros2 service list -t
else
  {
    echo "ros2 was not found."
    echo
    echo "Run this script inside the ROS 2 container or pass --ros-setup."
  } > "$OUT/03_ros_env.txt"
  for f in 04_nodes 05_topics 06_services; do
    echo "ros2 not found" > "$OUT/$f.txt"
  done
fi

TOPICS=()
if [ "$HAVE_ROS" -eq 1 ]; then
  mapfile -t TOPICS < <(ros2 topic list 2>/dev/null | awk '/^\// {print}' | sort -u)
fi

has_topic() {
  local needle="$1" t
  for t in "${TOPICS[@]}"; do
    [ "$t" = "$needle" ] && return 0
  done
  return 1
}

CAM_TOPICS=()
LIDAR_TOPICS=()
for t in "${TOPICS[@]}"; do
  case "$t" in
    *camera*|*image*|*video*|*rgb*) CAM_TOPICS+=("$t") ;;
  esac
  case "$t" in
    *lidar*|*points*|*cloud*|*scan*|*laser*) LIDAR_TOPICS+=("$t") ;;
  esac
done

# ---- per-topic endpoints ---------------------------------------------------
: > "$OUT/07_topic_info.txt"
if [ "$HAVE_ROS" -eq 1 ]; then
  {
    echo "# per-topic endpoints (ros2 topic info -v)"
    echo "# topic count: ${#TOPICS[@]}"
    echo
  } >> "$OUT/07_topic_info.txt"
  for t in "${TOPICS[@]}"; do
    echo "===== $t =====" >> "$OUT/07_topic_info.txt"
    timeout 5 ros2 topic info -v "$t" >> "$OUT/07_topic_info.txt" 2>&1
    echo >> "$OUT/07_topic_info.txt"
  done
else
  echo "ros2 not found" > "$OUT/07_topic_info.txt"
fi

# ---- rates for the topics that matter -------------------------------------
: > "$OUT/08_topic_rates.txt"
if [ "$HAVE_ROS" -eq 1 ]; then
  {
    echo "# sampled topic rates (5 s each; 124/timeout is normal for idle topics)"
    echo
  } >> "$OUT/08_topic_rates.txt"
  declare -A SEEN_RATES=()
  for t in /utlidar/cloud /scan /odom /sportmodestate /lowstate /tf /tf_static \
           "${CAM_TOPICS[@]}" "${LIDAR_TOPICS[@]}"; do
    [ -n "$t" ] || continue
    has_topic "$t" || continue
    [ -n "${SEEN_RATES[$t]:-}" ] && continue
    SEEN_RATES[$t]=1
    echo "===== $t =====" >> "$OUT/08_topic_rates.txt"
    timeout 5 ros2 topic hz "$t" >> "$OUT/08_topic_rates.txt" 2>&1
    echo >> "$OUT/08_topic_rates.txt"
  done
else
  echo "ros2 not found" > "$OUT/08_topic_rates.txt"
fi

# ---- TF frame tree ---------------------------------------------------------
if [ "$HAVE_ROS" -eq 1 ]; then
  (
    cd "$OUT" || exit 1
    echo "# tf2_tools view_frames (writes frames.pdf/frames.gv)"
    echo "# started: $(date -Is)"
    echo
    timeout 25 ros2 run tf2_tools view_frames 2>&1
    echo
    echo "# exit: $?"
    ls -l frames.pdf frames.gv 2>/dev/null || true
  ) > "$OUT/09_tf.txt" 2>&1
else
  echo "ros2 not found" > "$OUT/09_tf.txt"
fi

# ---- camera and LiDAR detail ----------------------------------------------
write_topic_detail() {
  local file="$1" title="$2"; shift 2
  local topics=("$@")
  {
    echo "# $title"
    echo "# candidates: ${#topics[@]}"
    echo
    if [ "${#topics[@]}" -eq 0 ]; then
      echo "No matching topics found."
      echo
      echo "Go2 camera note: the stock head camera may be exposed only through"
      echo "Unitree's WebRTC/video stream, not as a ROS 2 image topic. Record this"
      echo "finding before choosing the perception stack."
      return
    fi
    for t in "${topics[@]}"; do
      echo "===== $t ====="
      timeout 5 ros2 topic info -v "$t" 2>&1
      echo
      echo "--- rate (5 s) ---"
      timeout 5 ros2 topic hz "$t" 2>&1
      echo
    done
  } > "$OUT/$file.txt"
}

if [ "$HAVE_ROS" -eq 1 ]; then
  write_topic_detail 10_camera "camera / image topics" "${CAM_TOPICS[@]}"
  write_topic_detail 11_lidar "LiDAR / point-cloud / scan topics" "${LIDAR_TOPICS[@]}"
else
  echo "ros2 not found" > "$OUT/10_camera.txt"
  echo "ros2 not found" > "$OUT/11_lidar.txt"
fi

# ---- optional rosbag -------------------------------------------------------
BAG_STATUS="skipped"
if [ "$DO_BAG" -eq 1 ]; then
  if [ "$HAVE_ROS" -eq 0 ]; then
    BAG_STATUS="skipped (ros2 not found)"
  elif [ "${#TOPICS[@]}" -eq 0 ]; then
    BAG_STATUS="skipped (no ROS topics visible)"
  else
    AVAIL_KB="$(df -Pk "$OUT" 2>/dev/null | awk 'NR==2 {print $4}')"
    if [ "${AVAIL_KB:-0}" -lt 2097152 ]; then
      BAG_STATUS="skipped (less than 2 GiB free)"
    else
      echo "Recording all visible topics for ${RECORD_SECONDS}s ..."
      (
        cd "$OUT" || exit 1
        timeout -s INT -k 10 "$RECORD_SECONDS" ros2 bag record -a -o bag
      ) > "$OUT/bag_record.log" 2>&1
      echo "record exit: $?" >> "$OUT/bag_record.log"
      if [ -d "$OUT/bag" ]; then
        BAG_STATUS="bag/ ($(du -sh "$OUT/bag" 2>/dev/null | awk '{print $1}'))"
      else
        BAG_STATUS="failed (see bag_record.log)"
      fi
    fi
  fi
fi

# ---- summary ---------------------------------------------------------------
HAS_TF="no"; has_topic /tf && HAS_TF="yes"
HAS_TF_STATIC="no"; has_topic /tf_static && HAS_TF_STATIC="yes"
HAS_UTLIDAR="no"; has_topic /utlidar/cloud && HAS_UTLIDAR="yes"
HAS_SCAN="no"; has_topic /scan && HAS_SCAN="yes"
HAS_ODOM="no"
for t in /odom /sportmodestate /lowstate; do has_topic "$t" && HAS_ODOM="yes"; done

WARNINGS=()
[ "$HAVE_ROS" -eq 0 ] && WARNINGS+=("ros2 not found: run in the ROS container or pass --ros-setup")
[ "$HAVE_ROS" -eq 1 ] && [ "${#TOPICS[@]}" -eq 0 ] && WARNINGS+=("no topics visible: is unitree_ros2 running and ROS_DOMAIN_ID correct?")
[ "$HAVE_ROS" -eq 1 ] && [ "${#CAM_TOPICS[@]}" -eq 0 ] && WARNINGS+=("no camera/image topic: stock Go2 camera may be WebRTC-only")
[ "$HAVE_ROS" -eq 1 ] && [ "${#LIDAR_TOPICS[@]}" -eq 0 ] && WARNINGS+=("no LiDAR/point-cloud topic found")
[ "$HAVE_ROS" -eq 1 ] && [ "$HAS_TF" = "no" ] && WARNINGS+=("/tf missing: no localisation/TF tree")
[ "$HAVE_ROS" -eq 1 ] && [ "$HAS_SCAN" = "no" ] && [ "${#LIDAR_TOPICS[@]}" -gt 0 ] && WARNINGS+=("no /scan: slam_toolbox will need pointcloud_to_laserscan")
[ "$BAG_STATUS" != "skipped" ] && case "$BAG_STATUS" in bag/*) ;; *) WARNINGS+=("rosbag: $BAG_STATUS") ;; esac

{
  echo "# Go2 discovery summary"
  echo
  echo "- when: $(date -Is)"
  echo "- host: $(hostname)"
  echo "- output: $OUT"
  echo "- ROS distro: ${ROS_DISTRO:-not sourced}"
  echo "- ROS_DOMAIN_ID: ${ROS_DOMAIN_ID:-unset}"
  echo "- RMW_IMPLEMENTATION: ${RMW_IMPLEMENTATION:-unset}"
  echo "- topics: ${#TOPICS[@]}"
  echo "- /utlidar/cloud: $HAS_UTLIDAR"
  echo "- /scan: $HAS_SCAN"
  echo "- /tf + /tf_static: $HAS_TF + $HAS_TF_STATIC"
  echo "- odom/sport state: $HAS_ODOM"
  echo "- rosbag: $BAG_STATUS"
  echo
  echo "## Camera candidates"
  if [ "${#CAM_TOPICS[@]}" -eq 0 ]; then
    echo "- none"
  else
    printf -- '- `%s`\n' "${CAM_TOPICS[@]}"
  fi
  echo
  echo "## LiDAR / scan candidates"
  if [ "${#LIDAR_TOPICS[@]}" -eq 0 ]; then
    echo "- none"
  else
    printf -- '- `%s`\n' "${LIDAR_TOPICS[@]}"
  fi
  echo
  echo "## Warnings"
  if [ "${#WARNINGS[@]}" -eq 0 ]; then
    echo "- none"
  else
    printf -- '- %s\n' "${WARNINGS[@]}"
  fi
  echo
  echo "## Files (created when applicable)"
  echo "- \`05_topics.txt\`: full topic list with types"
  echo "- \`07_topic_info.txt\`: publishers/subscribers per topic"
  echo "- \`08_topic_rates.txt\`: sampled rates"
  echo "- \`09_tf.txt\` + \`frames.pdf\`: TF tree"
  echo "- \`10_camera.txt\`, \`11_lidar.txt\`: sensor detail"
  echo "- \`02_ssh_robot.txt\`: robot OS/compute info (if --ssh used)"
  echo "- \`bag/\`: rosbag (unless --no-bag)"
  echo
  echo "## Next"
  echo "Bring this directory back. Fill the detected topic names and message"
  echo "types into the detector config; do not install anything on the robot yet."
} > "$OUT/summary.md"

echo
cat "$OUT/summary.md"
echo
echo "Discovery complete: $OUT"
