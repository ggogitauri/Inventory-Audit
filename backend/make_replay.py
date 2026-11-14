"""Write a JSONL detection-pass recording from the simulator.

This is the bridge between the offline simulator and the real ROS graph:

    python make_replay.py --out bags/monday.jsonl --scenario baseline --name monday
    python make_replay.py --out bags/tuesday.jsonl --scenario changed --name tuesday
    ros2 launch go2_auditor replay.launch.py messages:=bags/tuesday.jsonl

The messages use the same contract as mock_perception, including patrol/pass uids,
timestamps, pose, per-detection sigma and appearance embeddings.
"""
from __future__ import annotations

import argparse
import json
import random
import time

from auditor.contract import CONTRACT_VERSION
from auditor.sim import drift_detections, make_lab, observe, plant_changes, random_drift


def build_replay(path: str, scenario: str = "changed", name: str = "patrol",
                 area: str = "mock-lab", passes: int = 3, seed: int = 7,
                 change_seed: int = 1, pose_sigma: float = 0.10,
                 drift_shift: float = 0.0, drift_rotation: float = 0.0) -> dict:
    if scenario not in ("baseline", "changed"):
        raise ValueError("scenario must be 'baseline' or 'changed'")
    rng = random.Random(seed)
    truth = make_lab(seed=seed)
    planted = None
    if scenario == "changed":
        truth, planted = plant_changes(truth, random.Random(change_seed))

    patrol_uid = f"replay:{area}:{name}:{seed}:{change_seed}"
    map_version = f"sim-{seed}"
    now = time.time()
    with open(path, "w", encoding="utf-8") as fh:
        for i in range(passes):
            dets = drift_detections(observe(truth, rng, pose_sigma=pose_sigma),
                                    *random_drift(rng, drift_shift, drift_rotation))
            msg = {
                "area": area, "patrol": name,
                "patrol_uid": patrol_uid, "pass_uid": f"{patrol_uid}:{i}",
                "schema_version": CONTRACT_VERSION, "map_version": map_version,
                "captured_at": now + i, "pass_idx": i, "n_passes": passes,
                "last": i == passes - 1,
                "pose": {"x": 0.0, "y": 0.0, "theta": 0.0, "sigma": pose_sigma},
                "detections": [{"cls": d.cls, "x": round(d.x, 3), "y": round(d.y, 3),
                                "conf": round(d.conf, 3), "pose_sigma": d.pose_sigma,
                                "embedding": list(d.embedding) if d.embedding else None}
                               for d in dets],
            }
            fh.write(json.dumps(msg) + "\n")

    planted_desc = None
    if planted is not None:
        planted_desc = {"moved": sorted(planted.moved), "removed": sorted(planted.removed),
                        "added": sorted(d.cls for d in planted.added)}
    return {"path": path, "messages": passes, "planted": planted_desc, "patrol_uid": patrol_uid}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--scenario", choices=["baseline", "changed"], default="changed")
    ap.add_argument("--name", default="patrol")
    ap.add_argument("--area", default="mock-lab")
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--change-seed", type=int, default=1)
    ap.add_argument("--pose-sigma", type=float, default=0.10)
    ap.add_argument("--drift", type=float, default=0.0)
    ap.add_argument("--drift-rot", type=float, default=0.0)
    args = ap.parse_args()
    info = build_replay(args.out, args.scenario, args.name, args.area, args.passes,
                        args.seed, args.change_seed, args.pose_sigma, args.drift, args.drift_rot)
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
