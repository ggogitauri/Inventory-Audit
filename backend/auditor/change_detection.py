"""Patrol-to-patrol change detection for the Go2 site auditor.

Each patrol is a list of Detection objects in the *map frame*. We compare a
baseline patrol against a current patrol and report items that moved, went
missing, or appeared.

Matching is class-aware and uses the Hungarian algorithm with a distance gate,
so two identical chairs next to each other are paired optimally instead of
greedily.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import hypot
from typing import Iterable

import numpy as np
from scipy.optimize import linear_sum_assignment


@dataclass(frozen=True)
class Detection:
    cls: str            # e.g. "chair", "fire_extinguisher"
    x: float            # map-frame metres
    y: float
    conf: float = 1.0
    image: str | None = None   # path to the supporting photo, for reports
    id: str | None = None


class ChangeType(str, Enum):
    MOVED = "moved"
    MISSING = "missing"
    NEW = "new"


@dataclass
class Change:
    type: ChangeType
    cls: str
    before: Detection | None
    after: Detection | None
    distance: float | None = None   # metres moved (MOVED only)


@dataclass
class ChangeConfig:
    min_conf: float = 0.4          # ignore weak detections
    match_radius: float = 2.0      # max distance (m) to consider two detections the same object
    move_threshold: float = 0.35   # moved if displacement exceeds this (m); above localisation noise
    # Second pass: an unmatched "missing" and "new" item of the same class within this
    # distance are reported as one MOVED item. Position alone cannot tell "chair moved 3 m"
    # from "chair removed + different chair added"; set to 0 to disable, or add appearance
    # embeddings later to disambiguate.
    relocate_radius: float = 4.0
    # Classes that should never be reported as "moved" (e.g. wall-mounted items would be noise)
    ignore_move_for: frozenset[str] = field(default_factory=frozenset)


@dataclass
class ChangeReport:
    changes: list[Change]
    unchanged: int

    def by_type(self, t: ChangeType) -> list[Change]:
        return [c for c in self.changes if c.type == t]

    def summary(self) -> dict[str, int]:
        return {
            "moved": len(self.by_type(ChangeType.MOVED)),
            "missing": len(self.by_type(ChangeType.MISSING)),
            "new": len(self.by_type(ChangeType.NEW)),
            "unchanged": self.unchanged,
        }


def _gated_assignment(cost: np.ndarray, radius: float) -> list[tuple[int, int, float]]:
    """Optimal one-to-one matching using only pairs within `radius`.

    Pairs beyond the radius get a prohibitive cost *before* solving. (Gating after solving is a
    bug: the solver may spend the good close pair on a far-away partner because that lowers the
    global total, and the gate then discards both.) The prohibitive cost exceeds any feasible
    total, so the solver first maximises the number of valid matches, then minimises distance.
    """
    big = float(cost.size) * (radius + 1.0) + 1e6
    gated = np.where(cost <= radius, cost, big)
    rows, cols = linear_sum_assignment(gated)
    return [(int(i), int(j), float(cost[i, j])) for i, j in zip(rows, cols) if cost[i, j] <= radius]


def compare_patrols(
    baseline: Iterable[Detection],
    current: Iterable[Detection],
    config: ChangeConfig | None = None,
) -> ChangeReport:
    cfg = config or ChangeConfig()
    base = [d for d in baseline if d.conf >= cfg.min_conf]
    curr = [d for d in current if d.conf >= cfg.min_conf]

    changes: list[Change] = []
    unchanged = 0

    classes = {d.cls for d in base} | {d.cls for d in curr}
    for cls in sorted(classes):
        b = [d for d in base if d.cls == cls]
        c = [d for d in curr if d.cls == cls]
        matched_b: set[int] = set()
        matched_c: set[int] = set()

        if b and c:
            cost = np.array(
                [[hypot(bi.x - cj.x, bi.y - cj.y) for cj in c] for bi in b]
            )
            for i, j, dist in _gated_assignment(cost, cfg.match_radius):
                matched_b.add(i)
                matched_c.add(j)
                if dist > cfg.move_threshold and cls not in cfg.ignore_move_for:
                    changes.append(Change(ChangeType.MOVED, cls, b[i], c[j], dist))
                else:
                    unchanged += 1

        left_b = [i for i in range(len(b)) if i not in matched_b]
        left_c = [j for j in range(len(c)) if j not in matched_c]

        if left_b and left_c and cfg.relocate_radius > cfg.match_radius and cls not in cfg.ignore_move_for:
            cost = np.array([[hypot(b[i].x - c[j].x, b[i].y - c[j].y) for j in left_c] for i in left_b])
            for r, k, dist in _gated_assignment(cost, cfg.relocate_radius):
                i, j = left_b[r], left_c[k]
                matched_b.add(i)
                matched_c.add(j)
                changes.append(Change(ChangeType.MOVED, cls, b[i], c[j], dist))

        for i, d in enumerate(b):
            if i not in matched_b:
                changes.append(Change(ChangeType.MISSING, cls, d, None))
        for j, d in enumerate(c):
            if j not in matched_c:
                changes.append(Change(ChangeType.NEW, cls, None, d))

    return ChangeReport(changes=changes, unchanged=unchanged)


def consolidate(
    passes: Iterable[Iterable[Detection]],
    min_votes: int = 2,
    cluster_radius: float = 0.5,
    min_conf: float = 0.4,
) -> list[Detection]:
    """Merge several observation passes of the same patrol into one list.

    Detections of the same class within `cluster_radius` are treated as the
    same object. A cluster is kept only if at least `min_votes` passes saw it,
    which suppresses one-off misses and false positives. The merged position is
    the mean of its members, the confidence is their mean.
    """
    clusters: list[dict] = []   # each: {cls, pts:[(x,y)], confs:[], passes:set, image}
    for p_idx, dets in enumerate(passes):
        for d in dets:
            if d.conf < min_conf:
                continue
            best, best_d = None, cluster_radius
            for c in clusters:
                if c["cls"] != d.cls or p_idx in c["passes"]:
                    continue
                cx = sum(p[0] for p in c["pts"]) / len(c["pts"])
                cy = sum(p[1] for p in c["pts"]) / len(c["pts"])
                dist = hypot(cx - d.x, cy - d.y)
                if dist < best_d:
                    best, best_d = c, dist
            if best is None:
                best = {"cls": d.cls, "pts": [], "confs": [], "passes": set(), "image": d.image, "id": d.id}
                clusters.append(best)
            best["pts"].append((d.x, d.y))
            best["confs"].append(d.conf)
            best["passes"].add(p_idx)

    out = []
    for c in clusters:
        if len(c["passes"]) >= min_votes:
            n = len(c["pts"])
            out.append(Detection(c["cls"], sum(p[0] for p in c["pts"]) / n,
                                 sum(p[1] for p in c["pts"]) / n,
                                 sum(c["confs"]) / n, image=c["image"], id=c["id"]))
    return out
