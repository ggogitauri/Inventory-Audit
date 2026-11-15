# Go2 Site Auditor

A Unitree Go2 patrols a lab, records what it sees, and reports what **moved**,
went **missing**, or is **new** compared with the lab's known state.

The repository is deliberately split so the whole pipeline runs **without the
robot**:

```
mock perception / JSONL replay / real detector
        │  /auditor/detections (JSON)
        ▼
patrol_uploader  ──HTTP──▶  FastAPI backend  ──▶  SQLite + media
        │                         │
        │                         ├─ drift-corrected change detection
        │                         ├─ known-state inventory + ignore zones
        │                         └─ HTML/JSON reports + review UI
        └─ queue + retry, at-least-once
```

## Quick start (no robot)

```bash
cd backend
pip install -r requirements.txt
pytest -s                                  # 48+ tests
uvicorn auditor.api:app --port 8000        # http://localhost:8000/
python demo_seed.py                        # simulated patrols + report URL
```

The review UI is at `http://localhost:8000/`; API docs at `/docs`.

Or run the API in Docker (SQLite and media under `./data`):

```bash
docker compose up --build api
```

Evaluate the detector offline:

```bash
python evaluate.py --trials 200
python evaluate.py --trials 100 --drift-sweep 0,0.1,0.2,0.3 --correct-drift
python evaluate.py --trials 100 --appearance-weight 0.6 --confusion --clutter 0.1
```

Record a synthetic patrol for ROS replay:

```bash
python make_replay.py --out ../bags/monday.jsonl --scenario baseline --name monday
```

## ROS path

```bash
docker compose run --rm ros bash
cd /ws && colcon build --symlink-install && source install/setup.bash
ros2 launch go2_auditor mock_patrol.launch.py scenario:=baseline name:=monday
ros2 launch go2_auditor mock_patrol.launch.py scenario:=changed  name:=tuesday

# or replay a recording through the same uploader
ros2 launch go2_auditor replay.launch.py messages:=/ws/bags/tuesday.jsonl
```

## Simulated patrol (no Gazebo, no GPU)

The 2D simulator publishes `/scan`, `/odom`, TF, and schema-v2 passes, so the
real uploader/backend/SLAM path runs on a stock 4-core / 8 GB Ubuntu VM:

```bash
docker compose up --build -d api
docker compose run --rm ros bash -lc 'cd /ws && colcon build --symlink-install'
docker compose run --rm ros bash
cd /ws && source install/setup.bash

ros2 launch go2_auditor sim_patrol.launch.py scenario:=baseline name:=monday
# Ctrl-C after "patrol complete"
ros2 launch go2_auditor sim_patrol.launch.py scenario:=changed  name:=tuesday
```

Add `use_slam:=true` to exercise the real `slam_toolbox`. See
`docs/SIM.md` for resource notes and limitations.

## Robot day (real Go2)

Read-only preflight writes a facts report and a one-page run sheet:

```bash
docker compose up -d api
./tools/preflight.sh --check-upload
```

Then start the passive coordinator (it counts loops from odometry, it does
**not** drive) and use teleop to make the loop:

```bash
ros2 launch go2_auditor patrol.launch.py \
    route_file:=/ws/src/go2_auditor/config/patrol_route.yaml
```

Edit `config/patrol_route.yaml` to your map frame first. Day one collects
bags and camera frames; the real detector comes after those images exist, so
there is nothing to promote yet. Full checklist, network notes and failures to
watch for: `docs/ROBOT_DAY.md`.

## Layout

| Path | Purpose |
|---|---|
| `backend/auditor/` | change detection, drift alignment, storage, API, reports, eval |
| `backend/evaluate.py` | offline metrics / drift sweep / calibration |
| `backend/make_replay.py` | simulator → JSONL recording for ROS replay |
| `ros_ws/src/go2_auditor/` | mock_perception, sim_robot, patrol_uploader, replay_node, launch files |
| `app/` | dependency-free review UI served by the API |
| `docs/ROBOT_DAY.md` | integration contract and first-physical-session checklist |
| `docs/SIM.md` | no-Gazebo simulated patrol for a stock Ubuntu VM |

## Before the robot arrives

The work that matters most is already executable offline:

1. **Versioned contract + idempotency** — patrol/pass uids, timestamps, pose,
   sigma, map version, stored comparison config. Retries cannot duplicate data.
2. **Replay harness** — the real ROS graph is exercised by replayed recordings,
   not only in-process mocks.
3. **Drift correction** — a RANSAC SE(2) estimate before matching. Simulated
   precision stays ~0.91 at ±0.3 m per-patrol drift (it falls to ~0.28 without).
4. **Appearance matching + image evidence** — embeddings disambiguate close
   same-class objects; crop uploads are shown in the report.
5. **Known-state inventory** — promote a reviewed patrol; compare against
   expected assets rather than "whatever the last patrol happened to see".
   Ignore zones and report review states are supported.
6. **Ops** — optional API key, locked-down CORS config, Docker image, metrics,
   and a small review UI.

See `docs/ROBOT_DAY.md` for the first physical session.
