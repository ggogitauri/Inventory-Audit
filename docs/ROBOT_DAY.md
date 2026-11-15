# Robot day: integration contract and first session

The first physical session is **data collection**, not unattended autonomy.
The goal is to capture real sensor and camera data, verify the interfaces, and
measure what the detector will have to work with.

## Day-one scope

Ready now:

- read-only preflight (topics, TF, rates, clock, API)
- passive loop counting from odometry, with no motion commands
- rosbag recording of scan, odom, TF and camera
- the pass/report pipeline, exercised in simulation and by `--check-upload`

Deliberately not ready:

- the real detector (no model is trained before real lab images exist)
- camera intrinsics and hand-eye/extrinsics calibration

So day one is: preflight, static captures, teleop loops with bags, and ground
truth photos. There is nothing to promote until a detector exists; do not
promote an empty baseline.

## First run on the lab laptop

Only Docker Engine and Compose v2 are required on the host.

```bash
docker compose build ros api           # first time, ~10 min, ~6 GB
docker compose up -d --build api
docker compose run --rm ros bash -lc 'cd /ws && colcon build --symlink-install'
./tools/preflight.sh
```

Rerun `colcon build` after pulling code. Bags and reports land in the mounted
repo (`bags/`, `ros_ws/robot_discovery/`), not inside a container.

## Network and DDS

- Laptop and Go2 on the same subnet/AP. The `ros` service uses
  `network_mode: host`, so containers share the laptop's DDS interfaces.
- `ROS_DOMAIN_ID` must match the robot bridge (Unitree default `0`).
- DDS discovery uses multicast: AP client isolation, a firewall or a VPN can
  hide the robot even when `ping` works.
- Verify inside the container with `ros2 topic list`: expect the LiDAR
  (`/scan` or a PointCloud2), `/odom` and `/tf`. An empty graph means the bridge
  or the network is not visible yet.

## Integration contract

| Item | Value / expectation |
|---|---|
| ROS | 2 Humble, `rmw_cyclonedds_cpp` (already set in the container) |
| Domain | one `ROS_DOMAIN_ID` for robot + laptop; verify with `ros2 topic list` |
| Detection topic | `/auditor/detections`, `std_msgs/String` JSON, schema v2 |
| Pass topic | `/auditor/pass_complete`, `std_msgs/String` JSON, one message per loop |
| QoS | detections: reliable, transient-local, depth 20; pass events: reliable, depth 20 |
| Frames | detections in the map frame; on day one `odom` is acceptable, document it |
| Pose | per pass, `{x,y,theta,sigma}` in the same map frame |
| Map | `map_version` must change whenever the map is rebuilt |
| Clock | NTP/chrony on robot and laptop; `captured_at` is wall-clock seconds |
| Uploader | `patrol_uploader`, API URL reachable from the robot network |
| Backend | `docker compose up -d api` on the laptop, port 8000 |
| Media | `POST /media/{key}` raw JPEG/PNG/WebP; key stored in `Detection.image` |
| Storage | enough disk for rosbags (dominant cost) and media |

## Preflight (run before anything moves)

One command, read-only unless you ask for the upload test:

```bash
docker compose up -d api            # if it is not already running
./tools/preflight.sh
./tools/preflight.sh --check-upload # also publish one synthetic pass
```

It runs inside the ROS container (no host ROS) and checks: ROS domain and graph,
scan topic and rate, odometry, `map -> base_link` and sensor TF, clock skew
against the backend, API health and contract version, and optionally the full
ROS -> uploader -> API path.

It also lists the camera/image topics it finds. If none appear, the detector
will have to be LiDAR-only and camera frames must be captured outside ROS.

Output is printed and written to `ros_ws/robot_discovery/preflight-<stamp>/`:

- `preflight.md` — pass/fail table plus discovered topic and frame names
- `run_sheet.md` — the one-page lab checklist, with the discovered values filled in

If the verdict is **FAIL**, fix those items before driving. The synthetic pass
uses area `preflight-test`, so it never becomes a baseline for your real area.

Useful overrides when the robot names things differently:

```bash
./tools/preflight.sh --scan-topic /scan --odom-topic /odom \
    --map-frame map --base-frame base_footprint
```

## Passive patrol coordinator

The coordinator never commands motion. It watches odometry and counts one pass
each time the robot returns near the start of the route after enough distance.
Drive with teleop, Nav2, or the Unitree app; pass counting is the same.

```bash
ros2 launch go2_auditor patrol.launch.py \
    route_file:=/ws/src/go2_auditor/config/patrol_route.yaml
```

The route file (`config/patrol_route.yaml`) holds the waypoints, number of
passes, loop radius, and pose uncertainty. The waypoints are placeholders: edit
them to your map frame before the first real patrol. They define the loop
length and the closure point, not a path the robot follows.

Drive to the loop start before launching: the start is learned from the first
odometry message unless you set `start: [x, y]`. `min_loop_distance` defaults
to 60% of the route length; measure one slow loop and set it explicitly if a
hand-driven path cuts corners. Run one uploader (one `patrol.launch.py`) at a
time; duplicate uploaders double-post and make the logs noisy.

Use the Unitree controller/app for teleop if a ROS teleop node is not wired up;
the coordinator does not care how the robot moves.

Each completed loop publishes:

```json
{"pass_idx": 0, "n_passes": 2, "last": false,
 "pose": {"x": 2.0, "y": 2.0, "theta": -1.55, "sigma": 0.15, "frame": "map"},
 "source": "patrol_coordinator", "captured_at": 1794742388.9}
```

The pose is transformed into `map` when `map -> odom` exists; otherwise it stays
in `odom` and the log warns once. The detector uses `pass_idx`, `n_passes`, and
`last` to build the schema-v2 pass, so no simulator node is involved.

To start the route from a fixed point instead of learning it from the first odom
message, uncomment `start: [x, y]` in the route file. To stop after N loops,
set `n_passes`; the coordinator stays up but emits nothing further.

Restart the whole `patrol.launch.py` if you need to reset pass counting: the
detector owns the patrol uid, and restarting only the coordinator would replay
pass indices that the backend already has (and de-duplicate away).

## Day-one checklist

- [ ] `ros2 topic list` shows the LiDAR (`/scan` or PointCloud2), `/odom`, `/tf`.
- [ ] TF tree is connected: map (or odom) → base_link → camera/lidar.
- [ ] Robot and laptop clocks agree within a second (`date`, `chronyc tracking`).
- [ ] Backend reachable from the laptop (`curl -s http://localhost:8000/health`).
- [ ] E-stop tested; motion limits agreed with everyone in the lab.
- [ ] Camera topic identified; images captured at taped 1/2/3 m distances.
- [ ] One 60 s sensor bag recorded and replayed without errors.
- [ ] One teleop loop recorded; coordinator logged exactly one loop event.
- [ ] 2–3 teleop loops recorded for the day's data set.
- [ ] Planted changes photographed and written down.
- [ ] Bags, `data/` and `ros_ws/robot_discovery/` copied off the machine.
- [ ] Disk space and battery plan for the session.

## Session plan (in order)

1. **Static robot, sensors only.** No motion. Verify TF, sensor rates, camera
   frames, timestamps, and the upload path. Capture images at 1/2/3 m with a
   tape measure in frame. This catches most integration bugs with zero risk.
2. **One short teleop loop.** Record a rosbag and confirm the coordinator logs
   exactly one loop event. Measure end-to-end latency if a detector is running.
3. **2–3 repeated loops at normal patrol speed.** These are the data set for
   detector development and for the later multi-pass vote.
4. **Plant a small number of changes** (move 2 chairs, remove 1 box, add 1
   object), photograph the setup, and record the changed run the same way.
5. **Detector work happens after the images exist.** When the detector is
   ready, replay the bags, check class/position errors, then run it live in
   shadow mode (`auto_compare:=false`) before trusting reports.
6. **Measure.** Record SLAM/odometry drift between the two runs, detector
   precision/recall per class once available, and false changes per patrol.

## Acceptance criteria for the first session

- Contract: no malformed messages, no duplicate patrols/passes after retries.
- Pipeline: with a detector, every pass stored and a report generated for the
  changed patrol; without one, every loop counted once and the bags replay
  cleanly.
- Safety: all motion under teleop, e-stop within reach.
- Data: rosbags + camera frames + ground-truth notes archived for offline replay
  (plus detections if a detector ran).
- Quality: at least one real drift measurement; add a per-class error count once
  a detector exists. Do not require a precision target on day one.

## What to build while waiting

- Real detector node publishing schema v2 (class, map-frame x/y, conf, crop key,
  embedding). Keep the current mock/replay path as the regression fixture.
- A hand-eye/extrinsics calibration procedure and a small fiducial (AprilTag)
  survey for map anchoring.
- `pointcloud_to_laserscan` launch if the Go2 LiDAR is cloud-only, and real
  `slam_toolbox` parameters for the Go2 if no map frame exists.
- A ground-truth annotation sheet (object, class, expected location,
  moved/removed) for the first lab walkthrough.
- Optional: switch the passive coordinator to active Nav2 driving once the
  motion interface is known. Day one stays teleop.

## Failure handling

- API down: uploader queues and retries; keep the robot on the network until the
  queue drains or the recording is safely on disk.
- Bad map: bump `map_version`; do not compare across versions.
- No map frame: run with `map_frame:=odom` and `--map-frame odom`, keep baseline
  and changed in one session, and write down the drift.
- Detector regression: once the detector exists, replay the archived rosbag
  through it and compare detections; for the mock path use
  `replay.launch.py` and `evaluate.py`.
- Suspicious report: review it in the UI, mark `dismissed`, and inspect raw passes
  with `GET /patrols/{id}/passes`.
