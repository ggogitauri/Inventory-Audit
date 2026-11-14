"""Evaluate the change detector without a robot.

    python evaluate.py                          # default scenario
    python evaluate.py --trials 200 --passes 1
    python evaluate.py --drift-sweep 0,0.05,0.1,0.15,0.25
    python evaluate.py --clutter 0.1 --confusion
    python evaluate.py --json > eval.json

Metrics are micro-averaged over trials; per-class numbers make it obvious whether a
regression is global or limited to one detector class.
"""
from __future__ import annotations

import argparse
import json

from auditor.change_detection import ChangeConfig
from auditor.eval import Scenario, drift_sweep, run_evaluation

DEFAULT_CONFUSION = {
    "chair": {"box": 0.05},
    "box": {"chair": 0.05},
    "monitor": {"desk": 0.03},
}


def _print_one(res) -> None:
    sc = res.scenario
    print(f"\n## trials={res.trials} passes={sc.passes} min_votes={sc.min_votes} "
          f"objects={sc.objects} sigma={sc.pose_sigma} drift=+/-{sc.drift_shift}m")
    print(f"precision={res.precision:.3f} recall={res.recall:.3f} "
          f"tp={res.tp} fp={res.fp} fn={res.fn}")
    print(f"reported  moved={res.counts['moved']} missing={res.counts['missing']} "
          f"new={res.counts['new']} unchanged={res.counts['unchanged']}")
    print(f"planted   moved={res.planted['moved']} removed={res.planted['removed']} "
          f"added={res.planted['added']}")
    print("\n| class | support | precision | recall | tp | fp | fn |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for cls, v in res.per_class.items():
        print(f"| {cls} | {v['support']} | {v['precision']:.3f} | {v['recall']:.3f} "
              f"| {v['tp']} | {v['fp']} | {v['fn']} |")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=100)
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--min-votes", type=int, default=2)
    ap.add_argument("--objects", type=int, default=40)
    ap.add_argument("--pose-sigma", type=float, default=0.10)
    ap.add_argument("--miss-rate", type=float, default=0.03)
    ap.add_argument("--fp-rate", type=float, default=0.02)
    ap.add_argument("--clutter", type=float, default=0.0)
    ap.add_argument("--drift", type=float, default=0.0)
    ap.add_argument("--drift-rot", type=float, default=0.0)
    ap.add_argument("--correct-drift", action="store_true",
                    help="estimate and remove global SE(2) drift before matching")
    ap.add_argument("--appearance-weight", type=float, default=0.0,
                    help="metres of matching cost for a completely different embedding")
    ap.add_argument("--embedding-noise", type=float, default=0.02)
    ap.add_argument("--drift-sweep", default=None,
                    help="comma-separated drift shifts, e.g. 0,0.05,0.1,0.2")
    ap.add_argument("--confusion", action="store_true", help="enable a small class-confusion model")
    ap.add_argument("--match-radius", type=float, default=None)
    ap.add_argument("--move-threshold", type=float, default=None)
    ap.add_argument("--relocate-radius", type=float, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cfg = ChangeConfig()
    for name in ("match_radius", "move_threshold", "relocate_radius"):
        v = getattr(args, name)
        if v is not None:
            setattr(cfg, name, v)

    scenario = Scenario(
        trials=args.trials, passes=args.passes, min_votes=args.min_votes,
        objects=args.objects, pose_sigma=args.pose_sigma, miss_rate=args.miss_rate,
        fp_rate=args.fp_rate, clutter_rate=args.clutter, drift_shift=args.drift,
        drift_rotation=args.drift_rot, correct_drift=args.correct_drift,
        appearance_weight=args.appearance_weight, embedding_noise=args.embedding_noise,
        confusion=DEFAULT_CONFUSION if args.confusion else None,
    )

    if args.drift_sweep:
        shifts = [float(x) for x in args.drift_sweep.split(",") if x.strip()]
        results = drift_sweep(shifts, scenario, cfg)
        if args.json:
            print(json.dumps([r.as_dict() for r in results], indent=2))
        else:
            for r in results:
                _print_one(r)
        return

    res = run_evaluation(scenario, cfg)
    if args.json:
        print(json.dumps(res.as_dict(), indent=2))
    else:
        _print_one(res)


if __name__ == "__main__":
    main()
