# Robot day: integration contract and first session

The first physical session is **data collection and shadow mode**, not unattended
autonomy. The goal is to measure real detector/localisation behaviour and compare
it with the offline numbers.

## Integration contract

| Item | Value / expectation |
|---|---|
| ROS | 2 Humble, `rmw_cyclonedds_cpp` (already set in the container) |
| Domain | one `ROS_DOMAIN_ID` for robot + VM; verify with `ros2 topic list` |
| Detection topic | `/auditor/detections`, `std_msgs/String` JSON, schema v2 |
| Pass topic | `/auditor/pass_complete`, `std_msgs/String` JSON, one message per loop |
| QoS | reliable, transient-local, depth 20 (`topics.py`) |
| Frames | detections already in the map frame; document the frame name |
| Pose | per pass, `{x,y,theta,sigma}` in the same map frame |
| Map | `map_version` must change whenever the map is rebuilt |
| Clock | NTP/chrony on robot and VM; `captured_at` is wall-clock seconds |
| Uploader | `patrol_uploader`, API URL reachable from the robot network |
| Backend | `uvicorn` or `docker compose up api`, port 8000 |
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

- [ ] `ros2 topic echo /auditor/detections` shows schema-v2 messages.
- [ ] TF tree is connected: map → odom → base_link → camera/lidar.
- [ ] Robot and VM clocks agree within a second (`date`, `chronyc tracking`).
- [ ] Backend reachable from the robot (`curl http://<vm>:8000/health`).
- [ ] E-stop tested; motion limits agreed with everyone in the lab.
- [ ] Camera intrinsics and hand-eye/extrinsics validated on a known target.
- [ ] Empty lab baseline recorded (2–3 passes) and promoted to known state.
- [ ] A manual walkthrough video/notes exist for ground truth.
- [ ] Disk space and battery plan for the session.

## Session plan (in order)

1. **Static robot, sensors only.** No motion. Verify TF, detector output, image
   crops, timestamps, and upload to the backend. This catches 80% of integration
   bugs with zero risk.
2. **One short teleop loop.** Record a rosbag *and* upload detections. Confirm
   reports render and media loads. Measure end-to-end latency.
3. **2–3 repeated loops at normal patrol speed.** This is what makes multi-pass
   voting work; without repeats the backend falls back to single-pass behaviour.
4. **Plant a small number of changes** (move 2 chairs, remove 1 box, add 1 object)
   and record the changed patrol. Compare in `baseline_mode: "state"` with
   `correct_drift: true`.
5. **Shadow mode.** Run with `auto_compare:=false` (uploader parameter) or leave
   reports unreviewed until checked against the manual walkthrough. Do not act on
   reports yet.
6. **Measure, then tune.** Record:
   - real SLAM drift: compare known fixed objects across patrols;
   - detector precision/recall per class;
   - crop embedding separability for same-class neighbours;
   - false changes per patrol before/after review.

## Acceptance criteria for the first session

- Contract: no malformed messages, no duplicate patrols/passes after retries.
- Pipeline: every pass stored; report generated for the changed patrol.
- Safety: all motion under teleop, e-stop within reach.
- Data: rosbags + detections + ground-truth notes archived for offline replay.
- Quality: at least one real drift measurement and a per-class error count; do
  not require a precision target on day one.

## What to build while waiting

- Real detector node publishing schema v2 (class, map-frame x/y, conf, crop key,
  embedding). Keep the current mock/replay path as the regression fixture.
- A hand-eye/extrinsics calibration procedure and a small fiducial (AprilTag)
  survey for map anchoring.
- Optional: switch the passive coordinator to active Nav2 driving once the
  motion interface is known. Day one stays teleop.
- A "shadow mode" launch that records everything but suppresses compare/report
  notifications.
- A ground-truth annotation sheet (object, class, expected location, moved/removed)
  for the first lab walkthrough.

## Failure handling

- API down: uploader queues and retries; keep the robot on the network until the
  queue drains or the recording is safely on disk.
- Bad map: bump `map_version`; do not compare across versions.
- Detector regression: replay the archived rosbag through `replay.launch.py` and
  compare `evaluate.py` numbers before touching the live robot.
- Suspicious report: review it in the UI, mark `dismissed`, and inspect raw passes
  with `GET /patrols/{id}/passes`.
