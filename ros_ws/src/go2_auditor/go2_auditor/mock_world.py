"""A fake lab and a noisy fake detector. Pure Python, no ROS, so it can be unit-tested anywhere.

Same idea as backend/auditor/sim.py, deliberately duplicated: this package must run inside the
ROS container, which only has the standard library (and ROS) available.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

CLASSES = ["chair", "desk", "monitor", "cabinet", "fire_extinguisher", "box"]
SIZE = (12.0, 8.0)
EMBEDDING_DIM = 8


def _unit(values) -> tuple[float, ...]:
    n = math.sqrt(sum(v * v for v in values)) or 1.0
    return tuple(round(v / n, 6) for v in values)


def embedding_for(key: str, dim: int = EMBEDDING_DIM) -> tuple[float, ...]:
    rng = random.Random(f"embedding:{key}")
    return _unit([rng.gauss(0, 1) for _ in range(dim)])


@dataclass(frozen=True)
class Obj:
    id: str
    cls: str
    x: float
    y: float
    embedding: tuple[float, ...] | None = None


def make_lab(seed: int = 7, n: int = 40, size: tuple[float, float] = SIZE) -> list[Obj]:
    """Ground-truth objects, at least 0.8 m apart."""
    rng = random.Random(seed)
    objs: list[Obj] = []
    while len(objs) < n:
        x, y = rng.uniform(0, size[0]), rng.uniform(0, size[1])
        if all((x - o.x) ** 2 + (y - o.y) ** 2 > 0.8 ** 2 for o in objs):
            oid = f"obj{len(objs)}"
            objs.append(Obj(oid, rng.choice(CLASSES), x, y,
                            embedding=embedding_for(f"{seed}:{oid}")))
    return objs


def plant_changes(truth: list[Obj], seed: int = 1, n_moved: int = 3, n_removed: int = 2,
                  n_added: int = 2, size: tuple[float, float] = SIZE):
    """Return (changed_truth, description). Deterministic for a given seed."""
    rng = random.Random(seed)
    ids = [o.id for o in truth]
    rng.shuffle(ids)
    moved, removed = set(ids[:n_moved]), set(ids[n_moved:n_moved + n_removed])
    new: list[Obj] = []
    for o in truth:
        if o.id in removed:
            continue
        if o.id in moved:
            new.append(Obj(o.id, o.cls,
                           o.x + rng.choice([-1, 1]) * rng.uniform(0.8, 2.0),
                           o.y + rng.choice([-1, 1]) * rng.uniform(0.0, 1.0),
                           embedding=o.embedding))
        else:
            new.append(o)
    added = [Obj(f"new{i}", rng.choice(CLASSES), rng.uniform(0, size[0]), rng.uniform(0, size[1]),
                 embedding=_unit([rng.gauss(0, 1) for _ in range(EMBEDDING_DIM)]))
             for i in range(n_added)]
    desc = {
        "moved": sorted(o.cls for o in truth if o.id in moved),
        "removed": sorted(o.cls for o in truth if o.id in removed),
        "added": sorted(o.cls for o in added),
    }
    return new + added, desc


def observe(truth: list[Obj], rng: random.Random, pose_sigma: float = 0.10, miss_rate: float = 0.03,
            fp_rate: float = 0.02, embedding_noise: float = 0.02,
            size: tuple[float, float] = SIZE) -> list[dict]:
    """One noisy pass over the lab: position noise, missed objects, a few false positives."""
    out: list[dict] = []
    for o in truth:
        if rng.random() < miss_rate:
            continue
        emb = None
        if o.embedding is not None:
            emb = _unit([v + rng.gauss(0, embedding_noise) for v in o.embedding])
        out.append({"cls": o.cls, "x": round(o.x + rng.gauss(0, pose_sigma), 3),
                    "y": round(o.y + rng.gauss(0, pose_sigma), 3),
                    "conf": round(rng.uniform(0.55, 0.98), 3),
                    "pose_sigma": pose_sigma,
                    "embedding": list(emb) if emb else None})
    for _ in range(sum(rng.random() < fp_rate for _ in truth)):
        out.append({"cls": rng.choice(CLASSES), "x": round(rng.uniform(0, size[0]), 3),
                    "y": round(rng.uniform(0, size[1]), 3), "conf": round(rng.uniform(0.4, 0.6), 3),
                    "pose_sigma": pose_sigma, "embedding": None})
    return out
