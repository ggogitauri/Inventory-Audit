"""Synthetic lab + noisy patrols, for testing change detection with no robot."""
from __future__ import annotations

import random
from dataclasses import dataclass

from .change_detection import Detection

CLASSES = ["chair", "desk", "monitor", "cabinet", "fire_extinguisher", "box"]


def make_lab(n: int = 40, size: tuple[float, float] = (12.0, 8.0), seed: int = 0) -> list[Detection]:
    """Ground-truth objects, kept >=0.8 m apart so identity is unambiguous."""
    rng = random.Random(seed)
    objs: list[Detection] = []
    while len(objs) < n:
        x, y = rng.uniform(0, size[0]), rng.uniform(0, size[1])
        if all((x - o.x) ** 2 + (y - o.y) ** 2 > 0.8**2 for o in objs):
            objs.append(Detection(rng.choice(CLASSES), x, y, 1.0, id=f"obj{len(objs)}"))
    return objs


def observe(
    truth: list[Detection],
    rng: random.Random,
    pose_sigma: float = 0.10,   # localisation + detection position noise (m)
    miss_rate: float = 0.03,    # detector misses an object
    fp_rate: float = 0.02,      # spurious detection probability per object
    size: tuple[float, float] = (12.0, 8.0),
) -> list[Detection]:
    out: list[Detection] = []
    for o in truth:
        if rng.random() < miss_rate:
            continue
        out.append(Detection(o.cls, o.x + rng.gauss(0, pose_sigma), o.y + rng.gauss(0, pose_sigma),
                             rng.uniform(0.55, 0.98), id=o.id))
    for _ in range(sum(rng.random() < fp_rate for _ in truth)):
        out.append(Detection(rng.choice(CLASSES), rng.uniform(0, size[0]), rng.uniform(0, size[1]),
                             rng.uniform(0.4, 0.6)))
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
                                       o.y + rng.choice([-1, 1]) * rng.uniform(0.0, 1.0), 1.0, id=o.id))
        else:
            new_truth.append(o)
    added = [Detection(rng.choice(CLASSES), rng.uniform(0, 12), rng.uniform(0, 8), 1.0, id=f"new{i}")
             for i in range(n_added)]
    return new_truth + added, PlantedChanges(moved, removed, added)
