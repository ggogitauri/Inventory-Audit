"""Scoring helpers for simulated (and later real, annotated) patrols."""
from __future__ import annotations

from collections import defaultdict
from math import hypot

from .change_detection import ChangeReport, ChangeType, Detection


def _truth_classes(truth_before, planted) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    before = {d.id: d.cls for d in truth_before if d.id is not None}
    moved = {i: before[i] for i in planted.moved if i in before}
    removed = {i: before[i] for i in planted.removed if i in before}
    added = {d.id: d.cls for d in planted.added if d.id is not None}
    return moved, removed, added


def _near_any(det: Detection, candidates: list[Detection], radius: float = 0.7) -> bool:
    return any(det.cls == c.cls and hypot(det.x - c.x, det.y - c.y) <= radius for c in candidates)


def evaluate(report: ChangeReport, truth_before, truth_after, planted) -> dict:
    """Aggregate + per-class TP/FP/FN against the planted ground truth.

    Uses stable ids when the simulator supplies them (it does), and falls back to a
    proximity check for additions so the harness also works on hand-made fixtures.
    """
    moved, removed, added = _truth_classes(truth_before, planted)
    planted_by_class: dict[str, int] = defaultdict(int)
    for cls in list(moved.values()) + list(removed.values()) + list(added.values()):
        planted_by_class[cls] += 1

    per = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for ch in report.changes:
        if ch.type == ChangeType.NEW:
            real = (ch.after.id in added) if ch.after.id is not None else _near_any(ch.after, planted.added)
        elif ch.type == ChangeType.MOVED:
            real = ch.before.id in moved
        else:
            real = ch.before.id in removed
        bucket = per[ch.cls]
        bucket["tp" if real else "fp"] += 1

    for cls, total in planted_by_class.items():
        per[cls]["fn"] = max(0, total - per[cls]["tp"])

    tp = sum(v["tp"] for v in per.values())
    fp = sum(v["fp"] for v in per.values())
    fn = sum(v["fn"] for v in per.values())
    for v in per.values():
        v["precision"] = v["tp"] / (v["tp"] + v["fp"]) if v["tp"] + v["fp"] else 1.0
        v["recall"] = v["tp"] / (v["tp"] + v["fn"]) if v["tp"] + v["fn"] else 1.0
        v["support"] = v["tp"] + v["fn"]
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else 1.0,
        "recall": tp / (tp + fn) if tp + fn else 1.0,
        "counts": {"moved": len(report.by_type(ChangeType.MOVED)),
                   "missing": len(report.by_type(ChangeType.MISSING)),
                   "new": len(report.by_type(ChangeType.NEW)),
                   "unchanged": report.unchanged},
        "planted": {"moved": len(moved), "removed": len(removed), "added": len(added)},
        "per_class": {k: dict(v) for k, v in sorted(per.items())},
    }


def score(report: ChangeReport, truth_before, truth_after, planted) -> dict:
    """Backward-compatible aggregate score."""
    return evaluate(report, truth_before, truth_after, planted)


def detection_calibration(dets: list[Detection], truth: list[Detection],
                          radius: float = 0.6, bins: int = 5) -> list[dict]:
    """Reliability of detector confidence: bucket detections by conf, measure precision."""
    rows: list[tuple[float, bool]] = []
    for d in dets:
        if not truth:
            break
        near = min(truth, key=lambda t: hypot(d.x - t.x, d.y - t.y))
        rows.append((d.conf, near.cls == d.cls and hypot(d.x - near.x, d.y - near.y) <= radius))
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        bucket = [c for conf, c in rows if lo <= conf < hi or (b == bins - 1 and conf == 1.0)]
        n = len(bucket)
        out.append({"lo": round(lo, 2), "hi": round(hi, 2), "n": n,
                    "mean_conf": round(sum(conf for conf, _ in rows if lo <= conf < hi
                                           or (b == bins - 1 and conf == 1.0)) / n, 3) if n else None,
                    "accuracy": round(sum(bucket) / n, 3) if n else None})
    return out
