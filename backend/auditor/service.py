"""Glue between the store and the change-detection algorithm."""
from __future__ import annotations

from dataclasses import asdict
from math import hypot

from .change_detection import Change, ChangeConfig, Detection, compare_patrols, consolidate
from .contract import ALGORITHM_VERSION
from .store import Store


def _det(d):
    return None if d is None else {
        "cls": d.cls, "x": round(d.x, 3), "y": round(d.y, 3),
        "conf": round(d.conf, 3), "image": d.image,
        "pose_sigma": None if d.pose_sigma is None else round(d.pose_sigma, 3),
    }


def _change(c: Change) -> dict:
    return {"type": c.type.value, "cls": c.cls, "before": _det(c.before), "after": _det(c.after),
            "distance": None if c.distance is None else round(c.distance, 3)}


def config_to_dict(config: ChangeConfig) -> dict:
    """JSON-safe snapshot of the comparison settings, stored with each report."""
    d = asdict(config)
    d["ignore_move_for"] = sorted(d["ignore_move_for"])
    d["algorithm_version"] = ALGORITHM_VERSION
    return d


def consolidated(store: Store, patrol_id: int, min_votes: int | None = None):
    passes = store.get_passes(patrol_id)
    if not passes:
        raise ValueError(f"patrol {patrol_id} has no detections yet")
    # With 1 pass we cannot vote; with 2 require both; otherwise a simple majority-ish 2.
    votes = min_votes if min_votes is not None else (1 if len(passes) == 1 else 2)
    return consolidate(passes, min_votes=votes)


def _asset_detection(a: dict) -> Detection:
    return Detection(a["cls"], a["x"], a["y"], a.get("conf", 1.0),
                     id=a["asset_uid"], pose_sigma=a.get("pose_sigma"))


def _in_zone(det: Detection, zone: dict) -> bool:
    return hypot(det.x - zone["x"], det.y - zone["y"]) <= zone["radius"]


def promote_patrol(store: Store, patrol_id: int, min_votes: int | None = None) -> dict:
    """Make a patrol's consolidated detections the known state for its area.

    Existing asset ids are preserved by nearest same-class match, so repeated
    promotions keep a stable inventory instead of renaming everything.
    """
    p = store.get_patrol(patrol_id)
    dets = consolidated(store, patrol_id, min_votes)
    existing = store.get_area_assets(p["area"])
    existing_uids = {a["asset_uid"] for a in existing}
    used: set[str] = set()
    numeric = [int(a["asset_uid"][1:]) for a in existing
               if a["asset_uid"].startswith("a") and a["asset_uid"][1:].isdigit()]
    next_id = max(numeric, default=0)

    assets = []
    for d in dets:
        uid = d.id if d.id and d.id not in existing_uids and d.id not in used else None
        if uid is None:
            best, best_d = None, 1.0
            for a in existing:
                if a["asset_uid"] in used or a["cls"] != d.cls:
                    continue
                dist = hypot(a["x"] - d.x, a["y"] - d.y)
                if dist < best_d:
                    best, best_d = a, dist
            if best is not None:
                uid = best["asset_uid"]
        if uid is None:
            next_id += 1
            uid = f"a{next_id}"
        used.add(uid)
        assets.append({"asset_uid": uid, "cls": d.cls, "x": d.x, "y": d.y,
                       "conf": d.conf, "pose_sigma": d.pose_sigma,
                       "map_version": p.get("map_version"), "source_patrol_id": patrol_id})
    store.set_area_state(p["area"], patrol_id, assets)
    return store.get_area_state(p["area"])


def run_comparison(store: Store, current_id: int, baseline_id: int | None = None,
                   config: ChangeConfig | None = None,
                   baseline_mode: str | None = None) -> dict:
    cfg = config or ChangeConfig()
    current = store.get_patrol(current_id)
    area = current["area"]

    if baseline_mode == "state":
        state = store.get_area_state(area)
        if state is None or not state["assets"]:
            raise ValueError(f"area {area} has no known state; promote a patrol first")
        baseline_id = state["patrol_id"]
        base = [_asset_detection(a) for a in state["assets"]]
    else:
        if baseline_id is None:
            baseline_id = store.previous_patrol(current_id)
            baseline_mode = baseline_mode or "previous"
            if baseline_id is None:
                raise ValueError("no earlier patrol in this area to compare against")
        else:
            baseline_mode = baseline_mode or "explicit"
        if baseline_id == current_id:
            raise ValueError("baseline and current must be different patrols")
        base = consolidated(store, baseline_id)

    curr = consolidated(store, current_id)
    report = compare_patrols(base, curr, cfg)

    changes = report.changes
    zones = store.list_ignore_zones(area)
    ignored = 0
    if zones:
        kept = []
        for c in changes:
            points = [d for d in (c.before, c.after) if d is not None]
            if any(_in_zone(d, z) for d in points for z in zones):
                ignored += 1
            else:
                kept.append(c)
        changes = kept

    summary = {
        "moved": sum(1 for c in changes if c.type.value == "moved"),
        "missing": sum(1 for c in changes if c.type.value == "missing"),
        "new": sum(1 for c in changes if c.type.value == "new"),
        "unchanged": report.unchanged,
    }
    if ignored:
        summary["ignored"] = ignored
    rows = [_change(c) for c in changes]
    rid = store.save_report(baseline_id, current_id, summary, rows,
                            config=config_to_dict(cfg), algorithm_version=ALGORITHM_VERSION,
                            baseline_mode=baseline_mode, alignment=report.alignment)
    return store.get_report(rid)
