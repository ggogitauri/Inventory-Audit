"""Synthetic lab + noisy patrols, for testing change detection with no robot."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace

from .change_detection import Detection

CLASSES = ["chair", "desk", "monitor", "cabinet", "fire_extinguisher", "box"]
EMBEDDING_DIM = 8


def _unit(values) -> tuple[float, ...]:
    n = sum(v * v for v in values) ** 0.5 or 1.0
    return tuple(round(v / n, 6) for v in values)


def embedding_for(key: str, dim: int = EMBEDDING_DIM) -> tuple[float, ...]:
    """Deterministic unit-length appearance vector, stable per object id."""
    rng = random.Random(f"embedding:{key}")
    return _unit([rng.gauss(0, 1) for _ in range(dim)])


def make_lab(n: int = 40, size: tuple[float, float] = (12.0, 8.0), seed: int = 0) -> list[Detection]:
    """Ground-truth objects, kept >=0.8 m apart so identity is unambiguous."""
    rng = random.Random(seed)
    objs: list[Detection] = []
    while len(objs) < n:
        x, y = rng.uniform(0, size[0]), rng.uniform(0, size[1])
        if all((x - o.x) ** 2 + (y - o.y) ** 2 > 0.8**2 for o in objs):
            oid = f"obj{len(objs)}"
            objs.append(Detection(rng.choice(CLASSES), x, y, 1.0, id=oid,
                                  embedding=embedding_for(f"{seed}:{oid}")))
    return objs


def drift_detections(dets: list[Detection], dx: float = 0.0, dy: float = 0.0,
                     dtheta: float = 0.0) -> list[Detection]:
    """Apply a patrol-level SE(2) drift to every detection.

    This models the dominant real failure mode: the whole current map is shifted
    relative to the baseline because the SLAM estimate drifted.
    """
    c, s = math.cos(dtheta), math.sin(dtheta)
    return [replace(d, x=c * d.x - s * d.y + dx, y=s * d.x + c * d.y + dy) for d in dets]


def random_drift(rng: random.Random, max_shift: float = 0.0,
                 max_rotation: float = 0.0) -> tuple[float, float, float]:
    if max_shift <= 0 and max_rotation <= 0:
        return (0.0, 0.0, 0.0)
    return (rng.uniform(-max_shift, max_shift),
            rng.uniform(-max_shift, max_shift),
            rng.uniform(-max_rotation, max_rotation))


def observe(
    truth: list[Detection],
    rng: random.Random,
    pose_sigma: float = 0.10,   # localisation + detection position noise (m)
    miss_rate: float = 0.03,    # detector misses an object
    fp_rate: float = 0.02,      # spurious detection probability per object
    clutter_rate: float = 0.0,  # transient objects (people, carts) per real object
    confusion: dict[str, dict[str, float]] | None = None,
    embedding_noise: float = 0.02,
    size: tuple[float, float] = (12.0, 8.0),
) -> list[Detection]:
    """One noisy pass over the lab.

    `confusion` is e.g. {"chair": {"box": 0.1}} meaning 10% of chairs are reported
    as boxes. This is deliberately class-aware like the real detector will be.
    """
    out: list[Detection] = []
    for o in truth:
        if rng.random() < miss_rate:
            continue
        cls = o.cls
        if confusion and cls in confusion:
            roll, acc = rng.random(), 0.0
            for other, p in confusion[cls].items():
                acc += p
                if roll < acc:
                    cls = other
                    break
        emb = None
        if o.embedding is not None and embedding_noise >= 0:
            emb = _unit([v + rng.gauss(0, embedding_noise) for v in o.embedding])
        out.append(Detection(cls, o.x + rng.gauss(0, pose_sigma), o.y + rng.gauss(0, pose_sigma),
                             rng.uniform(0.55, 0.98), id=o.id, pose_sigma=pose_sigma,
                             embedding=emb))
    n_noise = sum(rng.random() < fp_rate for _ in truth) + sum(rng.random() < clutter_rate for _ in truth)
    for _ in range(n_noise):
        out.append(Detection(rng.choice(CLASSES), rng.uniform(0, size[0]), rng.uniform(0, size[1]),
                             rng.uniform(0.4, 0.6), pose_sigma=pose_sigma))
    return out


@dataclass
class PlantedChanges:
    moved: set[str]
    removed: set[str]
    added: list[Detection]


def plant_changes(truth: list[Detection], rng: random.Random, n_moved=3, n_removed=2, n_added=2):
    """Return (new_truth, record of what was planted)."""
    ids = [o.id for o in truth]
    rng.shuffle(ids)
    moved, removed = set(ids[:n_moved]), set(ids[n_moved:n_moved + n_removed])
    new_truth: list[Detection] = []
    for o in truth:
        if o.id in removed:
            continue
        if o.id in moved:
            new_truth.append(Detection(o.cls, o.x + rng.choice([-1, 1]) * rng.uniform(0.8, 2.0),
                                       o.y + rng.choice([-1, 1]) * rng.uniform(0.0, 1.0), 1.0,
                                       id=o.id, embedding=o.embedding))
        else:
            new_truth.append(o)
    added = [Detection(rng.choice(CLASSES), rng.uniform(0, 12), rng.uniform(0, 8), 1.0,
                       id=f"new{i}",
                       embedding=_unit([rng.gauss(0, 1) for _ in range(EMBEDDING_DIM)]))
             for i in range(n_added)]
    return new_truth + added, PlantedChanges(moved, removed, added)
