"""Estimate and remove patrol-to-patrol drift before comparing detections.

A patrol's map is never perfectly aligned with the previous one. The dominant
error is a global SE(2) offset. We estimate it from same-class nearest-neighbour
pairs with a small deterministic RANSAC, so a few genuinely moved/removed objects
do not drag the estimate. Only inlier pairs vote on the final transform.

This is deliberately a pre-comparison correction: the report still shows every
change, but coordinates are put back into the baseline frame first.
"""
from __future__ import annotations

import math
from dataclasses import replace
from itertools import combinations

import numpy as np

from .change_detection import Detection


def apply_transform(dets: list[Detection], dx: float, dy: float, dtheta: float) -> list[Detection]:
    """Apply the SE(2) transform (rotation then translation) to detections."""
    c, s = math.cos(dtheta), math.sin(dtheta)
    return [replace(d, x=c * d.x - s * d.y + dx, y=s * d.x + c * d.y + dy) for d in dets]


def kabsch_2d(src: list[tuple[float, float]], dst: list[tuple[float, float]]):
    """Least-squares rigid transform mapping src -> dst. Returns (dx, dy, theta)."""
    a = np.asarray(src, dtype=float)
    b = np.asarray(dst, dtype=float)
    ca, cb = a.mean(axis=0), b.mean(axis=0)
    h = (a - ca).T @ (b - cb)
    u, _, vt = np.linalg.svd(h)
    r = vt.T @ u.T
    if np.linalg.det(r) < 0:                 # reflection guard for noisy degenerate input
        vt[-1, :] *= -1.0
        r = vt.T @ u.T
    theta = math.atan2(r[1, 0], r[0, 0])
    t = cb - r @ ca
    return float(t[0]), float(t[1]), float(theta)


def _nearest_pairs(base: list[Detection], curr: list[Detection],
                   search_radius: float) -> list[tuple[Detection, Detection]]:
    """For each current detection, its nearest baseline detection of the same class."""
    pairs = []
    for c in curr:
        best, best_d = None, search_radius
        for b in base:
            if b.cls != c.cls:
                continue
            d = math.hypot(b.x - c.x, b.y - c.y)
            if d < best_d:
                best, best_d = b, d
        if best is not None:
            pairs.append((best, c))
    return pairs


def estimate_patrol_drift(base: list[Detection], curr: list[Detection],
                          max_shift: float = 2.0, max_rotation: float = 0.35,
                          min_pairs: int = 6, inlier_radius: float = 0.5,
                          search_radius: float | None = None):
    """Estimate the transform that maps `curr` into the `base` frame.

    Returns (dx, dy, dtheta, n_inliers) or None if there is not enough evidence.
    """
    if len(base) < min_pairs or len(curr) < min_pairs:
        return None
    radius = search_radius if search_radius is not None else max_shift + 1.0
    pairs = _nearest_pairs(base, curr, radius)
    if len(pairs) < min_pairs:
        return None

    def residual(pair, tf):
        b, c = pair
        dx, dy, th = tf
        co, si = math.cos(th), math.sin(th)
        px, py = co * c.x - si * c.y + dx, si * c.x + co * c.y + dy
        return math.hypot(px - b.x, py - b.y)

    best_tf, best_inliers, best_err = None, [], float("inf")
    # Deterministic RANSAC over two-pair hypotheses (<= ~50 objects, so cheap).
    for p, q in combinations(pairs, 2):
        (b1, c1), (b2, c2) = p, q
        if math.hypot(c1.x - c2.x, c1.y - c2.y) < 0.5:
            continue
        tf = kabsch_2d([(c1.x, c1.y), (c2.x, c2.y)], [(b1.x, b1.y), (b2.x, b2.y)])
        if math.hypot(tf[0], tf[1]) > max_shift or abs(tf[2]) > max_rotation:
            continue
        inliers = [pair for pair in pairs if residual(pair, tf) <= inlier_radius]
        if len(inliers) < min_pairs:
            continue
        if len(inliers) < len(best_inliers):
            continue
        refined = kabsch_2d([(c.x, c.y) for _, c in inliers],
                            [(b.x, b.y) for b, _ in inliers])
        if math.hypot(refined[0], refined[1]) > max_shift or abs(refined[2]) > max_rotation:
            continue
        err = sum(residual(pair, refined) for pair in inliers) / len(inliers)
        if len(inliers) > len(best_inliers) or (len(inliers) == len(best_inliers) and err < best_err):
            best_tf, best_inliers, best_err = refined, inliers, err

    if best_tf is None or len(best_inliers) < min_pairs:
        return None
    return (*best_tf, len(best_inliers))


def correct_drift(base: list[Detection], curr: list[Detection],
                  max_shift: float = 2.0, max_rotation: float = 0.35,
                  min_pairs: int = 6, inlier_radius: float = 0.5):
    """Align `curr` to `base`. Returns (aligned_dets, transform_dict|None, n_inliers)."""
    est = estimate_patrol_drift(base, curr, max_shift=max_shift, max_rotation=max_rotation,
                                min_pairs=min_pairs, inlier_radius=inlier_radius)
    if est is None:
        return list(curr), None, 0
    dx, dy, dtheta, n = est
    tf = {"dx": round(dx, 4), "dy": round(dy, 4), "dtheta": round(dtheta, 5), "pairs": n}
    return apply_transform(curr, dx, dy, dtheta), tf, n
