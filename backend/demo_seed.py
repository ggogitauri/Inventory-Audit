"""Fill the API with two simulated patrols, promote the first to known state, and print the report.

    uvicorn auditor.api:app --port 8000        # terminal 1
    python demo_seed.py                        # terminal 2   (needs: pip install httpx)
"""
import random
import time
import uuid

import httpx

from auditor.sim import make_lab, observe, plant_changes

BASE = "http://localhost:8000"


def post_patrol(c, name, truth, rng, passes=3):
    uid = f"demo:{name}:{uuid.uuid4().hex}"
    pid = c.post("/patrols", json={"area": "lab-main", "name": name, "uid": uid,
                                   "map_version": "sim-map-1",
                                   "meta": {"source": "demo_seed"}}).json()["id"]
    for i in range(passes):
        dets = [{"cls": d.cls, "x": d.x, "y": d.y, "conf": d.conf,
                 "pose_sigma": d.pose_sigma,
                 "embedding": list(d.embedding) if d.embedding else None}
                for d in observe(truth, rng)]
        c.post(f"/patrols/{pid}/passes", json={"uid": f"{uid}:{i}", "captured_at": time.time(),
                                               "detections": dets}).raise_for_status()
    return pid


with httpx.Client(base_url=BASE) as c:
    rng = random.Random(7)
    truth = make_lab(seed=7)
    after, planted = plant_changes(truth, rng)
    monday = post_patrol(c, "monday", truth, rng)
    state = c.post(f"/patrols/{monday}/promote").json()
    print(f"known state: {len(state['assets'])} expected objects from patrol {monday}")

    tuesday = post_patrol(c, "tuesday", after, rng)
    rep = c.post(f"/patrols/{tuesday}/compare",
                 json={"baseline_mode": "state", "correct_drift": True,
                       "appearance_weight": 0.6}).json()
    cls = {o.id: o.cls for o in truth}
    print("planted moved  :", sorted(cls[i] for i in planted.moved))
    print("planted removed:", sorted(cls[i] for i in planted.removed))
    print("planted added  :", sorted(a.cls for a in planted.added))
    print("summary:", rep["summary"])
    for ch in rep["changes"]:
        print(" ", ch["type"], ch["cls"], ch["distance"])
    print(f"\nOpen the report in a browser: {BASE}/reports/{rep['id']}/html")
    print(f"Review UI: {BASE}/")
