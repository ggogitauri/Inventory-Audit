import random
from statistics import mean

from auditor.change_detection import ChangeConfig, ChangeType, Detection, compare_patrols, consolidate
from auditor.metrics import score
from auditor.sim import make_lab, observe, plant_changes


def test_identical_patrols_have_no_changes():
    a = [Detection("chair", 1, 1), Detection("desk", 3, 2)]
    rep = compare_patrols(a, a)
    assert rep.changes == [] and rep.unchanged == 2


def test_moved_missing_new():
    base = [Detection("chair", 1, 1), Detection("desk", 3, 2), Detection("box", 6, 6)]
    curr = [Detection("chair", 2.2, 1), Detection("desk", 3.05, 2.02), Detection("monitor", 8, 3)]
    rep = compare_patrols(base, curr)
    s = rep.summary()
    assert s == {"moved": 1, "missing": 1, "new": 1, "unchanged": 1}
    moved = rep.by_type(ChangeType.MOVED)[0]
    assert moved.cls == "chair" and abs(moved.distance - 1.2) < 1e-6


def test_noise_below_threshold_is_not_a_move():
    base = [Detection("chair", 1, 1)]
    curr = [Detection("chair", 1.2, 1.1)]
    assert compare_patrols(base, curr).changes == []


def test_low_confidence_ignored():
    base = [Detection("chair", 1, 1)]
    curr = [Detection("chair", 1, 1), Detection("box", 5, 5, conf=0.2)]
    assert compare_patrols(base, curr).changes == []


def test_identical_neighbours_matched_optimally():
    # two chairs 1 m apart, both shift 0.3 m in +x; greedy matching can swap them
    base = [Detection("chair", 0, 0), Detection("chair", 1, 0)]
    curr = [Detection("chair", 0.3, 0), Detection("chair", 1.3, 0)]
    assert compare_patrols(base, curr).changes == []


def test_very_far_object_is_missing_plus_new_not_moved():
    base = [Detection("chair", 0, 0)]
    curr = [Detection("chair", 6, 6)]
    s = compare_patrols(base, curr).summary()
    assert s["missing"] == 1 and s["new"] == 1 and s["moved"] == 0


def test_relocated_object_beyond_match_radius_is_moved():
    base = [Detection("chair", 0, 0)]
    curr = [Detection("chair", 3, 0)]
    s = compare_patrols(base, curr).summary()
    assert s["moved"] == 1 and s["missing"] == 0 and s["new"] == 0


def test_relocate_can_be_disabled():
    base = [Detection("chair", 0, 0)]
    curr = [Detection("chair", 3, 0)]
    s = compare_patrols(base, curr, ChangeConfig(relocate_radius=0)).summary()
    assert s["moved"] == 0 and s["missing"] == 1 and s["new"] == 1


def test_close_pair_not_sacrificed_to_far_pairing():
    """Regression: with pure gate-after-solve, Hungarian paired (7.9,5.8)<->(4.3,1.9) and
    (10.1,7.6)<->(7.9,5.9) because that total is 0.1 cheaper than the obvious matching, and the
    gate then discarded both, leaving a 0.1 m pair unmatched."""
    base = [Detection("cabinet", 7.9, 5.8), Detection("cabinet", 10.1, 7.6)]
    curr = [Detection("cabinet", 7.9, 5.9), Detection("cabinet", 4.3, 1.9)]
    rep = compare_patrols(base, curr)
    assert rep.summary() == {"moved": 0, "missing": 1, "new": 1, "unchanged": 1}
    assert (rep.by_type(ChangeType.MISSING)[0].before.x, rep.by_type(ChangeType.NEW)[0].after.x) == (10.1, 4.3)


def run_trials(n=200, passes=3, **obs_kwargs):
    ps, rs = [], []
    for seed in range(n):
        rng = random.Random(seed)
        truth = make_lab(seed=seed)
        after, planted = plant_changes(truth, rng)
        base = consolidate([observe(truth, rng, **obs_kwargs) for _ in range(passes)], min_votes=2) if passes > 1 \
            else observe(truth, rng, **obs_kwargs)
        curr = consolidate([observe(after, rng, **obs_kwargs) for _ in range(passes)], min_votes=2) if passes > 1 \
            else observe(after, rng, **obs_kwargs)
        m = score(compare_patrols(base, curr, ChangeConfig()), truth, after, planted)
        ps.append(m["precision"]); rs.append(m["recall"])
    return mean(ps), mean(rs)


def test_consolidate_drops_one_off_detections():
    p1 = [Detection("chair", 1, 1), Detection("box", 5, 5)]       # box seen once only
    p2 = [Detection("chair", 1.1, 1.0)]
    p3 = [Detection("chair", 0.9, 1.0)]
    out = consolidate([p1, p2, p3], min_votes=2)
    assert [d.cls for d in out] == ["chair"]


def test_simulated_accuracy():
    p, r = run_trials()
    print(f"\nprecision={p:.3f} recall={r:.3f}")
    assert p > 0.85 and r > 0.88   # position-only identity; see README "Known limits"


if __name__ == "__main__":  # run without pytest:  python -m tests.test_change_detection
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
    print("single pass :", "precision %.3f recall %.3f" % run_trials(passes=1))
    print("3 passes    :", "precision %.3f recall %.3f" % run_trials(passes=3))
    for sigma in (0.05, 0.10, 0.15, 0.25):
        p, r = run_trials(pose_sigma=sigma)
        print(f"3 passes, pose noise {sigma:.2f} m -> precision {p:.3f}, recall {r:.3f}")
