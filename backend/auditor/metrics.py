from __future__ import annotations
from .change_detection import ChangeReport, ChangeType


def score(report: ChangeReport, truth_before, truth_after, planted) -> dict:
    """Compare reported changes against planted ones, using ground-truth ids to decide
    whether a reported change is real. Returns TP/FP/FN, precision and recall."""
    planted_ids = {
        ChangeType.MOVED: planted.moved,
        ChangeType.MISSING: planted.removed,
    }
    tp = fp = 0
    found_moved, found_missing, found_new = set(), set(), 0
    for ch in report.changes:
        det = ch.before or ch.after
        if ch.type == ChangeType.NEW:
            # real if it is near a planted addition
            real = any(abs(a.x - ch.after.x) < 0.7 and abs(a.y - ch.after.y) < 0.7 and a.cls == ch.cls
                       for a in planted.added)
            if real: found_new += 1
        elif ch.type == ChangeType.MOVED:
            real = ch.before.id in planted_ids[ChangeType.MOVED]
            if real: found_moved.add(ch.before.id)
        else:
            real = ch.before.id in planted_ids[ChangeType.MISSING]
            if real: found_missing.add(ch.before.id)
        tp += real
        fp += not real
    total = len(planted.moved) + len(planted.removed) + len(planted.added)
    fn = total - tp
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else 1.0,
        "recall": tp / total if total else 1.0,
    }
