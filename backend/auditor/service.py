"""Glue between the store and the change-detection algorithm."""
from __future__ import annotations

from dataclasses import asdict

from .change_detection import Change, ChangeConfig, compare_patrols, consolidate
from .store import NotFound, Store


def _det(d):
    return None if d is None else {"cls": d.cls, "x": round(d.x, 3), "y": round(d.y, 3),
                                  "conf": round(d.conf, 3), "image": d.image}


def _change(c: Change) -> dict:
    return {"type": c.type.value, "cls": c.cls, "before": _det(c.before), "after": _det(c.after),
            "distance": None if c.distance is None else round(c.distance, 3)}


def consolidated(store: Store, patrol_id: int, min_votes: int | None = None):
    passes = store.get_passes(patrol_id)
    if not passes:
        raise ValueError(f"patrol {patrol_id} has no detections yet")
    # With 1 pass we cannot vote; with 2 require both; otherwise a simple majority-ish 2.
    votes = min_votes if min_votes is not None else (1 if len(passes) == 1 else 2)
    return consolidate(passes, min_votes=votes)


def run_comparison(store: Store, current_id: int, baseline_id: int | None = None,
                   config: ChangeConfig | None = None) -> dict:
    if baseline_id is None:
        baseline_id = store.previous_patrol(current_id)
        if baseline_id is None:
            raise ValueError("no earlier patrol in this area to compare against")
    if baseline_id == current_id:
        raise ValueError("baseline and current must be different patrols")
    base = consolidated(store, baseline_id)
    curr = consolidated(store, current_id)
    report = compare_patrols(base, curr, config)
    changes = [_change(c) for c in report.changes]
    rid = store.save_report(baseline_id, current_id, report.summary(), changes)
    return store.get_report(rid)
