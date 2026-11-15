# Simulated patrol (Level 2, no Gazebo)

This is a lightweight simulated robot for a **stock Ubuntu VM**: 4 cores,
8 GB RAM, no GPU, no Gazebo, no host ROS installation. Everything runs in the
same Ubuntu 22.04 / ROS 2 Humble container as the real stack.

The goal is not physics fidelity. It is to exercise the interfaces that usually
break on robot day:

- `map -> odom -> base_link -> laser` TF
- a real `slam_toolbox` (optional) on a simulated `/scan`
- patrol loops and one pass per loop
- the schema-v2 contract, uploader queue, backend, reports

## What runs

| Node | Purpose |
|---|---|
| `sim_robot` | kinematic differential-drive robot, ray-cast 2D LiDAR, `/odom`, TF, `/clock`, loop events |
| `ground_truth_detector` | turns each completed loop into one schema-v2 `/auditor/detections` message |
| `patrol_uploader` | unchanged production uploader |
| `slam_toolbox` | optional; only when `use_slam:=true` |

The world is 12 x 8 m and uses the same object vocabulary as `mock_world.py`.
`scenario:=changed` plants moved, removed, and added objects.

## Run

Start the backend first:

```bash
docker compose up --build -d api
curl http://localhost:8000/health
```

Build the workspace once per container/host:

```bash
docker compose run --rm ros bash -lc 'cd /ws && colcon build --symlink-install'
```

Then start a shell and run the baseline patrol:

```bash
docker compose run --rm ros bash
cd /ws
source install/setup.bash

ros2 launch go2_auditor sim_patrol.launch.py \
  scenario:=baseline name:=monday n_passes:=2 speed:=1.5
```

Wait for `patrol complete`, then `Ctrl-C`. Run the changed patrol:

```bash
ros2 launch go2_auditor sim_patrol.launch.py \
  scenario:=changed name:=tuesday n_passes:=2 speed:=1.5
```

The baseline has no previous patrol, so its automatic comparison is skipped with
a 422 warning; that is expected. The changed patrol produces the report.

## View the result

On the VM, or through an SSH tunnel:

```bash
ssh -L 8000:localhost:8000 <user>@<vm>
```

Open `http://localhost:8000`.

## Optional SLAM

The default publishes `map -> odom` directly so the loop is deterministic and
fast. To exercise the real `slam_toolbox`:

```bash
docker compose build ros          # includes slam_toolbox in the image
docker compose run --rm ros bash
cd /ws
source install/setup.bash
ros2 launch go2_auditor sim_patrol.launch.py \
  scenario:=baseline name:=monday use_slam:=true n_passes:=2
```

When `use_slam:=true`, the simulator leaves `map -> odom` to `slam_toolbox`.
Inspect the tree with:

```bash
ros2 run tf2_tools view_frames
```

## Sanity checks

```bash
ros2 topic list -t
ros2 topic hz /scan
ros2 topic echo /odom --once
ros2 run tf2_tools view_frames
```

Expected rates: `/scan` about 10 Hz, `/odom` about 20 Hz. The `laser` frame is
0.2 m forward and 0.3 m above `base_link`.

## Rehearse the lab preflight

The real-robot preflight works against the simulator too, so the day-one flow is
rehearsed before you reach the lab. Start `sim_patrol.launch.py` in one shell,
then in another:

```bash
docker compose run --rm ros bash -lc \
  'cd /ws && source install/setup.bash && \
   ros2 run go2_auditor preflight --check-upload'
```

It writes `robot_discovery/preflight-<stamp>/{preflight.md,run_sheet.md}` on the
host. Expect one `WARN` for `/clock`: the simulator publishes it, the real robot
should not.

## Gotchas

- **Run one launch at a time.** The `ros` service uses host networking, so a
  second launch on the same machine joins the same DDS domain. Its uploader would
  subscribe to the same passes and upload them twice. The API de-duplicates by
  patrol/pass uid, but the logs get confusing. If you killed a launch with
  `timeout`, check that no container is still running:

  ```bash
  docker ps --format '{{.Names}} {{.Status}}'
  ```

- **`map_version` must match.** Patrols default to `sim-<lab_seed>`. Comparison
  across a different `map_version` is refused with a 422 (`map version changed
  (sim-7 -> remapped-v2); ...`) because a re-map moves the origin and every
  position would look changed. Re-map, then promote a patrol from the new map.

- **The first patrol in a new `area` has no baseline.** Its automatic comparison
  is skipped with a 422 warning. That is expected; the second patrol produces the
  report.

- **Change counts include noise.** With the default `miss_rate=0.03` and
  `fp_rate=0.02`, a run can report one extra new/missing item. Pass
  `miss_rate:=0 fp_rate:=0` for exactly the planted changes.

- **`colcon build` writes as root.** `ros_ws/{build,install,log}` end up
  root-owned on the host. Clean with
  `docker compose run --rm ros bash -lc 'rm -rf /ws/build /ws/install /ws/log'`
  or `sudo chown -R "$USER" ros_ws`.


## Resource notes

- Close heavy desktop apps during a run. The simulator plus ROS, SLAM, and the
  backend use roughly 2–3 GB.
- Do not run RViz or Gazebo on the 4-core / 8 GB VM.
- The same commands work unchanged on a larger stock cloud VM; only speed
  differs.
- No host packages are required. If `ros2` exists on the host, it is ignored.

## What it does not test

- Gazebo physics, contact, or camera rendering
- real LiDAR/camera noise or extrinsics
- real Go2 DDS/network behaviour
- Nav2 autonomous navigation (the route is scripted)

Use this to de-risk TF, frames, patrol orchestration, and the uploader. Measure
real perception and drift on the robot or on recorded rosbags.
