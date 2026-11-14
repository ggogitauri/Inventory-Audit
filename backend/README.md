# Backend: change detection

    cd backend
    pip install numpy scipy pytest
    pytest -s            # or: python -m tests.test_change_detection

## Pipeline
1. Robot observes each area several times per patrol -> several lists of `Detection` (map frame).
2. `consolidate(passes, min_votes=2)` keeps only detections most passes agree on.
3. `compare_patrols(baseline, current)` -> moved / missing / new items.

## Known limits (measured in simulation, 200 trials)
- Single pass is unusable (precision ~0.5): detector misses and false positives look like changes.
  Multi-pass voting brings it to precision ~0.91, recall ~0.91.
- Matching must gate distances *before* solving the assignment (`_gated_assignment`). Gating after
  the solve let the optimiser pair a close couple with far-away partners and then discard both.
  This was found from a real report (see `test_close_pair_not_sacrificed_to_far_pairing`).
- Some remaining error is probably genuine position-only ambiguity: with several same-class items
  close together the system knows something changed there but may pair the wrong two. Not yet
  re-diagnosed after the fix. Appearance embeddings (crop features) as an extra matching cost
  would address it.
- Localisation drift dominates: at 0.15 m position noise precision drops to ~0.74, at 0.25 m to ~0.23.
  Measure real SLAM drift before trusting reports.
- `relocate_radius` trades "big moves detected" against "remove + add mislabelled as a move".
- Simulated density (40 objects, 6 classes, 12x8 m) may be harsher than a real lab; re-measure on
  real rosbags before quoting numbers.

# Backend API

    pip install -r requirements.txt
    pytest -s                                   # API tests skip themselves if fastapi/httpx are missing
    uvicorn auditor.api:app --port 8000         # then open http://localhost:8000/docs
    python demo_seed.py                         # fills the DB with simulated patrols and prints a report

| Method | Path | Purpose |
|---|---|---|
| POST | `/patrols` `{area, name}` | start a patrol |
| POST | `/patrols/{id}/passes` `{detections:[{cls,x,y,conf,image}]}` | robot uploads one observation pass (empty list allowed) |
| POST | `/patrols/{id}/compare` `{baseline_id?, match_radius?, ...}` | diff against baseline (default: previous patrol in same area) |
| GET | `/patrols`, `/patrols/{id}`, `/reports/{id}` | read back |

Storage is SQLite (`AUDITOR_DB`, default `./auditor.db`). CORS is wide open for local development.

## HTML report
`GET /reports/{id}/html` returns a self-contained page (summary cards, SVG map of changes, table).
`python demo_seed.py` prints the URL. Map legend: dashed circle + arrow = moved (from -> to),
red X = missing, green + = new. Numbers on the map match the table rows.
Not yet included: photos (needs the perception stage to supply images), PDF export.
