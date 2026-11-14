# Backend: change detection + known state

```bash
pip install -r requirements.txt
pytest -s                                   # API tests skip if fastapi/httpx missing
uvicorn auditor.api:app --port 8000         # http://localhost:8000/  (review UI), /docs (API)
python demo_seed.py                         # simulated patrols, state promotion, report
```

## Pipeline

1. A patrol is created (with a client `uid`), then uploads one or more **passes**.
   Each pass is a list of detections in the map frame.
2. `consolidate(passes, min_votes=2)` keeps detections that enough passes agree on.
3. Optional **drift correction** estimates a global SE(2) offset from same-class
   nearest pairs (deterministic RANSAC) and moves the current patrol into the
   baseline frame.
4. Class-aware Hungarian matching, gated before the solve, produces
   **moved / missing / new**. Optional appearance embeddings add to the cost.
5. A report is stored with the exact `ChangeConfig`, algorithm version, alignment
   transform and baseline mode, so it can always be reproduced.
6. Ignore zones suppress changes in known-messy areas; review state is tracked per
   report.

### Known-state mode

The default comparison is against the previous patrol in the same area. That is
fragile: one bad patrol poisons the next comparison. Promote a reviewed patrol
instead:

```bash
curl -X POST localhost:8000/patrols/7/promote
curl -X POST localhost:8000/patrols/9/compare \
     -H 'content-type: application/json' \
     -d '{"baseline_mode":"state","correct_drift":true,"appearance_weight":0.6}'
```

`missing` then means *expected but absent from the known inventory*, and `new`
means *unexpected*. Asset ids are preserved across promotions by nearest
same-class matching.

## Wire contract (v2)

A patrol message is still JSON-in-`std_msgs/String` for the prototype:

```json
{
  "area": "lab-main",
  "patrol": "monday",
  "patrol_uid": "lab-main:monday:8f3c...",
  "pass_uid": "lab-main:monday:8f3c...:0",
  "schema_version": 2,
  "map_version": "sim-7",
  "captured_at": 1760000000.0,
  "pass_idx": 0,
  "n_passes": 3,
  "last": false,
  "pose": {"x": 1.0, "y": 2.0, "theta": 0.1, "sigma": 0.1},
  "detections": [
    {"cls": "chair", "x": 3.2, "y": 4.1, "conf": 0.91,
     "pose_sigma": 0.1, "image": "crops/chair-1.jpg",
     "embedding": [0.1, -0.4, "..."]}
  ]
}
```

`patrol_uid` and `pass_uid` are idempotency keys: retrying the same request
returns the original row. `image` is a media key uploaded to `POST /media/{key}`.
`embedding` is optional; `appearance_weight` only applies when both sides have one.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness |
| GET | `/schema` | contract summary for the uploader |
| GET | `/` , `/app/*` | review UI |
| GET | `/metrics` | Prometheus text counters |
| POST | `/patrols` | create patrol (idempotent by `uid`) |
| GET | `/patrols` , `/patrols/{id}` | list / read |
| GET | `/patrols/{id}/passes` | pass metadata + detections |
| POST | `/patrols/{id}/passes` | upload one pass (idempotent by `uid`) |
| POST | `/patrols/{id}/promote` | make this patrol the area's known state |
| POST | `/patrols/{id}/compare` | diff against previous/explicit/state baseline |
| GET | `/areas/{area}/state` | expected inventory + source patrol |
| DELETE | `/areas/{area}/state` | clear expected inventory |
| GET/POST | `/areas/{area}/ignore-zones` | list/add zones |
| DELETE | `/ignore-zones/{id}` | remove a zone |
| GET | `/reports` | list, filter by `area` / `status` |
| GET | `/reports/{id}` | JSON report |
| GET | `/reports/{id}/html` | self-contained HTML report |
| POST | `/reports/{id}/review` | `open` / `confirmed` / `dismissed` + note |
| POST/GET | `/media/{key}` | upload/read crop evidence (raw image bytes) |

`POST /compare` accepts `baseline_mode` (`previous` or `state`), `correct_drift`,
`drift_max_shift`, `drift_max_rotation`, `drift_min_pairs`, `appearance_weight`,
`match_radius`, `move_threshold`, `relocate_radius`, `min_conf`.

Storage is SQLite (`AUDITOR_DB`, default `./auditor.db`). Images live under
`AUDITOR_MEDIA` (default `./media`). Older databases are migrated in place.

## Offline evaluation

```bash
python evaluate.py --trials 200
python evaluate.py --trials 100 --drift-sweep 0,0.1,0.2,0.3 --correct-drift
python evaluate.py --trials 100 --appearance-weight 0.6 --confusion --clutter 0.1
python evaluate.py --json > eval.json
```

Reports per-class precision/recall, TP/FP/FN, reported vs planted counts, and
drift sensitivity. `detection_calibration()` in `auditor/metrics.py` buckets
detector confidence to check calibration.

### Measured in simulation (50 trials, 40 objects, 3 passes, sigma 0.10 m)

| Scenario | Precision | Recall |
|---|---:|---:|
| no drift | 0.91 | 0.93 |
| ±0.3 m patrol drift, no correction | 0.28 | 0.92 |
| ±0.3 m patrol drift + correction | 0.91 | 0.93 |
| appearance weight 0.6, no drift | 0.95 | 0.97 |
| ±0.3 m drift + correction + appearance 0.6 | 0.93 | 0.95 |

These are simulator numbers. Re-measure on real rosbags before quoting them.

## Known limits / next measurements

- **Real SLAM drift is unknown.** The correction assumes a mostly rigid global
  offset; a badly distorted map needs loop closure or fiducial anchors.
- **Multi-pass still matters.** A real patrol must revisit each area 2–3× (or use
  per-zone dwell) or `min_votes` falls back to 1.
- **Class confusion still surfaces as missing + new.** If the detector calls a
  chair a box, the class-aware matcher cannot pair them. Add a reclassification
  pass (same position/appearance, different class) before treating reports as
  final; `evaluate.py --confusion` measures the size of the problem.
- **Embeddings are synthetic here.** Plug in a real crop model (DINO/CLIP/OSNet)
  and re-tune `appearance_weight`.
- **At-least-once delivery.** The API is idempotent by uid, but a restarted
  uploader generates a new patrol uid, which will create a new patrol.
- **Media has no retention policy.** Local filesystem only; add object storage and
  lifecycle rules before long deployments.
- **SQLite is single-writer.** Fine for one robot; move to Postgres when the UI
  and ingestion grow.
- **Auth is one optional shared key.** Put it behind a reverse proxy with real
  accounts before exposing it beyond the lab network.
