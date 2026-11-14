"""Fill the API with two simulated patrols that differ by planted changes, then print the report.

    uvicorn auditor.api:app --port 8000        # terminal 1
    python demo_seed.py                        # terminal 2   (needs: pip install httpx)
"""
import random

import httpx

from auditor.sim import make_lab, observe, plant_changes

BASE = "http://localhost:8000"


def post_patrol(c, name, truth, rng, passes=3):
    pid = c.post("/patrols", json={"area": "lab-main", "name": name}).json()["id"]
    for _ in range(passes):
        dets = [{"cls": d.cls, "x": d.x, "y": d.y, "conf": d.conf} for d in observe(truth, rng)]
        c.post(f"/patrols/{pid}/passes", json={"detections": dets}).raise_for_status()
    return pid


with httpx.Client(base_url=BASE) as c:
    rng = random.Random(7)
    truth = make_lab(seed=7)
    after, planted = plant_changes(truth, rng)
    post_patrol(c, "monday", truth, rng)
    b = post_patrol(c, "tuesday", after, rng)
    rep = c.post(f"/patrols/{b}/compare").json()
    cls = {o.id: o.cls for o in truth}
    print("planted moved  :", sorted(cls[i] for i in planted.moved))
    print("planted removed:", sorted(cls[i] for i in planted.removed))
    print("planted added  :", sorted(a.cls for a in planted.added))
    print("summary:", rep["summary"])
    for ch in rep["changes"]:
        print(" ", ch["type"], ch["cls"], ch["distance"])
    print(f"\nOpen the report in a browser: {BASE}/reports/{rep['id']}/html")
