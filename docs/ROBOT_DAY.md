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
| QoS | reliable, transient-local, depth 20 (`topics.py`) |
| Frames | detections already in the map frame; document the frame name |
| Pose | per pass, `{x,y,theta,sigma}` in the same map frame |
| Map | `map_version` must change whenever the map is rebuilt |
| Clock | NTP/chrony on robot and VM; `captured_at` is wall-clock seconds |
| Uploader | `patrol_uploader`, API URL reachable from the robot network |
| Backend | `uvicorn` or `docker compose up api`, port 8000 |
| Media | `POST /media/{key}` raw JPEG/PNG/WebP; key stored in `Detection.image` |
| Storage | enough disk for rosbags (dominant cost) and media |

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
- A Nav2 waypoint patrol with explicit repeated loops; record the route as a
  parameter file.
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
