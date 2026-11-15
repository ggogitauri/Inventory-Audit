# go2_auditor (ROS 2 Humble)

```
mock_perception / replay_node / real detector
        --/auditor/detections (std_msgs/String, JSON)-->  patrol_uploader  --HTTP--> backend API
                                                           (queue + retry)
```

JSON-in-a-String is a deliberate prototype shortcut; the schema is versioned
(`schema_version: 2`) and is meant to move to a typed message once the detector
settles. Positions are map-frame metres.

## Message

```json
{
  "area": "mock-lab", "patrol": "monday",
  "patrol_uid": "mock-lab:monday:8f3c...", "pass_uid": "mock-lab:monday:8f3c...:0",
  "schema_version": 2, "map_version": "mock-7",
  "captured_at": 1760000000.0,
  "pass_idx": 0, "n_passes": 3, "last": false,
  "pose": {"x": 0.0, "y": 0.0, "theta": 0.0, "sigma": 0.1},
  "detections": [
    {"cls": "chair", "x": 3.2, "y": 4.1, "conf": 0.91,
     "pose_sigma": 0.1, "image": "crops/chair-1.jpg", "embedding": [0.1, -0.4]}
  ]
}
```

- `patrol_uid` / `pass_uid` are stable for retries and make the backend idempotent.
- `image` is a media key; upload the bytes to `POST /media/{key}` first.
  From Python use `ApiClient.upload_media(key, jpeg_bytes)`.
- `embedding` is optional and only used when `appearance_weight > 0`.

## Run

```bash
# terminal 1, on the VM
cd ~/go2_site_auditor/backend && source ~/venvs/go2/bin/activate
uvicorn auditor.api:app --port 8000

# terminal 2, container
cd ~/go2_site_auditor && docker compose run --rm ros bash
cd /ws && colcon build --symlink-install && source install/setup.bash

# simulated patrols (fake detections)
ros2 launch go2_auditor mock_patrol.launch.py scenario:=baseline name:=monday
ros2 launch go2_auditor mock_patrol.launch.py scenario:=changed  name:=tuesday

# 2D sensor simulation: /scan, /odom, TF, then schema-v2 passes
# (no Gazebo; works on a 4-core / 8 GB VM)
ros2 launch go2_auditor sim_patrol.launch.py scenario:=baseline name:=monday
ros2 launch go2_auditor sim_patrol.launch.py scenario:=changed  name:=tuesday
# add use_slam:=true to run slam_toolbox on the simulated /scan

# or replay a JSONL recording through the real ROS graph
ros2 launch go2_auditor replay.launch.py messages:=/ws/bags/tuesday.jsonl

# or replay a rosbag2 recording
ros2 launch go2_auditor replay.launch.py bag_path:=/ws/bags/tuesday
```

Generate JSONL fixtures without ROS:

```bash
cd backend
python make_replay.py --out ../bags/monday.jsonl  --scenario baseline --name monday
python make_replay.py --out ../bags/tuesday.jsonl --scenario changed  --name tuesday
```

Record a real run with:

```bash
ros2 bag record -o bags/monday /auditor/detections
```

Planted changes for `scenario:=changed` are logged by `mock_perception`, so you
can check the report against them. Launch arguments: scenario, name, area,
api_url, lab_seed, change_seed, n_passes, period_s, record_dir (also record the
detection topic to rosbag2), auto_compare (set `false` for shadow mode).

## Tests (no ROS needed)

```bash
cd src/go2_auditor
PYTHONPATH=. python3 test/test_uploader_core.py
PYTHONPATH=. python3 test/test_mock_world.py
PYTHONPATH=. python3 test/test_replay.py
```

## Behaviour to know

- API down: jobs queue and are retried every `retry_period_s` (2 s); order is kept.
- API rejects a job (4xx/5xx): logged as an error and dropped so it cannot block
  later uploads. Exception: a refused comparison (HTTP 422) is only a warning.
- At-least-once delivery: the backend de-duplicates by `patrol_uid` / `pass_uid`.
  A restarted uploader gets a new patrol uid and therefore a new patrol.
- `last` triggers at most one compare per patrol even if a source repeats it.
