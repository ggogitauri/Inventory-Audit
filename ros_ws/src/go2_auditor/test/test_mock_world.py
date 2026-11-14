import random

from go2_auditor import mock_world as w


def test_lab_is_deterministic_and_spaced():
    a, b = w.make_lab(seed=7), w.make_lab(seed=7)
    assert a == b and len(a) == 40
    assert all((p.x - q.x) ** 2 + (p.y - q.y) ** 2 > 0.8 ** 2 for i, p in enumerate(a) for q in a[i + 1:])


def test_plant_changes_counts_and_determinism():
    truth = w.make_lab(seed=7)
    new1, d1 = w.plant_changes(truth, seed=1)
    new2, d2 = w.plant_changes(truth, seed=1)
    assert new1 == new2 and d1 == d2
    assert len(d1["moved"]) == 3 and len(d1["removed"]) == 2 and len(d1["added"]) == 2
    assert len(new1) == len(truth) - 2 + 2


def test_observe_shape_and_noise():
    truth = w.make_lab(seed=7)
    dets = w.observe(truth, random.Random(0))
    assert 30 <= len(dets) <= 45
    assert all(set(d) == {"cls", "x", "y", "conf", "pose_sigma", "embedding"} for d in dets)
    assert all(0 <= d["conf"] <= 1 and d["pose_sigma"] == 0.10 for d in dets)
    assert any(d["embedding"] for d in dets)
    assert w.observe(truth, random.Random(5)) != w.observe(truth, random.Random(6))


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)
