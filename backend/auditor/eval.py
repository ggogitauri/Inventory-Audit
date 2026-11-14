"""Monte-Carlo evaluation of the change detector.

`run_evaluation()` is the reusable core; `backend/evaluate.py` is the CLI.
The important upgrade over the old test-only loop is per-class metrics plus
drift/clutter scenarios, so you can see *which* objects and *which* failure
mode is responsible instead of one aggregate number.
"""
from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, replace

from .change_detection import ChangeConfig, compare_patrols, consolidate
from .metrics import evaluate
from .sim import drift_detections, make_lab, observe, plant_changes, random_drift


@dataclass
class Scenario:
    trials: int = 100
    passes: int = 3
    min_votes: int = 2
    objects: int = 40
    size: tuple[float, float] = (12.0, 8.0)
    pose_sigma: float = 0.10
    miss_rate: float = 0.03
    fp_rate: float = 0.02
    clutter_rate: float = 0.0
    drift_shift: float = 0.0      # uniform +/- metres per patrol
    drift_rotation: float = 0.0   # uniform +/- radians per patrol
    correct_drift: bool = False   # estimate/remove global SE(2) drift before matching
    appearance_weight: float = 0.0
    embedding_noise: float = 0.02
    confusion: dict[str, dict[str, float]] | None = None


@dataclass
class EvalResult:
    scenario: Scenario
    trials: int
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    per_class: dict
    counts: dict
    planted: dict

    def as_dict(self) -> dict:
        return {
            "scenario": {
                "trials": self.scenario.trials, "passes": self.scenario.passes,
                "min_votes": self.scenario.min_votes, "objects": self.scenario.objects,
                "pose_sigma": self.scenario.pose_sigma, "miss_rate": self.scenario.miss_rate,
                "fp_rate": self.scenario.fp_rate, "clutter_rate": self.scenario.clutter_rate,
                "drift_shift": self.scenario.drift_shift,
                "drift_rotation": self.scenario.drift_rotation,
                "correct_drift": self.scenario.correct_drift,
                "appearance_weight": self.scenario.appearance_weight,
                "embedding_noise": self.scenario.embedding_noise,
            },
            "trials": self.trials,
            "tp": self.tp, "fp": self.fp, "fn": self.fn,
            "precision": round(self.precision, 4), "recall": round(self.recall, 4),
            "per_class": self.per_class,
            "reported": self.counts,
            "planted": self.planted,
        }


def _consolidate_passes(passes: list[list], min_votes: int):
    votes = max(1, min(min_votes, len(passes)))
    return consolidate(passes, min_votes=votes)


def run_trial(scenario: Scenario, seed: int, config: ChangeConfig | None = None) -> dict:
    rng = random.Random(seed)
    truth = make_lab(n=scenario.objects, size=scenario.size, seed=seed)
    after, planted = plant_changes(truth, rng)

    def record(truth_state):
        passes = []
        offset = random_drift(rng, scenario.drift_shift, scenario.drift_rotation)
        for _ in range(scenario.passes):
            dets = observe(
                truth_state, rng, pose_sigma=scenario.pose_sigma,
                miss_rate=scenario.miss_rate, fp_rate=scenario.fp_rate,
                clutter_rate=scenario.clutter_rate, confusion=scenario.confusion,
                embedding_noise=scenario.embedding_noise,
                size=scenario.size,
            )
            passes.append(drift_detections(dets, *offset))
        return _consolidate_passes(passes, scenario.min_votes)

    base = record(truth)
    curr = record(after)
    cfg = config or ChangeConfig()
    if scenario.correct_drift:
        cfg = replace(cfg, correct_drift=True)
    if scenario.appearance_weight:
        cfg = replace(cfg, appearance_weight=scenario.appearance_weight)
    report = compare_patrols(base, curr, cfg)
    out = evaluate(report, truth, after, planted)
    out["alignment"] = report.alignment
    return out


def run_evaluation(scenario: Scenario | None = None, config: ChangeConfig | None = None) -> EvalResult:
    sc = scenario or Scenario()
    total_tp = total_fp = total_fn = 0
    per = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    counts = defaultdict(int)
    planted = defaultdict(int)

    for seed in range(sc.trials):
        m = run_trial(sc, seed, config)
        total_tp += m["tp"]
        total_fp += m["fp"]
        total_fn += m["fn"]
        for cls, v in m["per_class"].items():
            for k in ("tp", "fp", "fn"):
                per[cls][k] += v[k]
        for k, v in m["counts"].items():
            counts[k] += v
        for k, v in m["planted"].items():
            planted[k] += v

    per_class = {}
    for cls, v in sorted(per.items()):
        per_class[cls] = {
            **v,
            "support": v["tp"] + v["fn"],
            "precision": round(v["tp"] / (v["tp"] + v["fp"]), 4) if v["tp"] + v["fp"] else 1.0,
            "recall": round(v["tp"] / (v["tp"] + v["fn"]), 4) if v["tp"] + v["fn"] else 1.0,
        }

    return EvalResult(
        scenario=sc, trials=sc.trials, tp=total_tp, fp=total_fp, fn=total_fn,
        precision=total_tp / (total_tp + total_fp) if total_tp + total_fp else 1.0,
        recall=total_tp / (total_tp + total_fn) if total_tp + total_fn else 1.0,
        per_class=per_class,
        counts={k: counts.get(k, 0) for k in ("moved", "missing", "new", "unchanged")},
        planted={k: planted.get(k, 0) for k in ("moved", "removed", "added")},
    )


def drift_sweep(shifts: list[float], scenario: Scenario | None = None,
                config: ChangeConfig | None = None) -> list[EvalResult]:
    sc = scenario or Scenario()
    out = []
    for shift in shifts:
        s = Scenario(**{**sc.__dict__, "drift_shift": shift})
        out.append(run_evaluation(s, config))
    return out
