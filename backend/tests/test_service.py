import random

import pytest

from auditor.service import run_comparison
from auditor.sim import make_lab, observe, plant_changes
from auditor.store import NotFound, Store
from auditor.change_detection import Detection


def record_patrol(store, area, name, truth, rng, passes=3):
    pid = store.create_patrol(area, name)
    for _ in range(passes):
        store.add_pass(pid, observe(truth, rng))
    return pid


def test_end_to_end_with_planted_changes():
    s = Store()
    rng = random.Random(1)
    truth = make_lab(seed=1)
    after, planted = plant_changes(truth, rng)
    a = record_patrol(s, "lab", "monday", truth, rng)
    b = record_patrol(s, "lab", "tuesday", after, rng)
    rep = run_comparison(s, b)                      # baseline defaults to previous patrol
    assert rep["baseline_id"] == a and rep["current_id"] == b
    found = rep["summary"]["moved"] + rep["summary"]["missing"] + rep["summary"]["new"]
    assert found >= 5                               # 3 moved + 2 removed + 2 added planted; allow some slack
    assert s.get_report(rep["id"])["changes"] == rep["changes"]


def test_default_baseline_is_same_area_only():
    s = Store()
    rng = random.Random(2)
    truth = make_lab(seed=2)
    record_patrol(s, "other-room", "x", truth, rng)
    b = record_patrol(s, "lab", "first", truth, rng)
    with pytest.raises(ValueError):
        run_comparison(s, b)                        # no earlier patrol in "lab"


def test_empty_pass_is_recorded_and_counts():
    s = Store()
    pid = s.create_patrol("lab", "p")
    s.add_pass(pid, [Detection("chair", 1, 1)])
    s.add_pass(pid, [])
    assert s.get_patrol(pid)["passes"] == 2
    assert [len(p) for p in s.get_passes(pid)] == [1, 0]


def test_unknown_ids():
    s = Store()
    with pytest.raises(NotFound):
        s.get_patrol(99)
    with pytest.raises(NotFound):
        s.add_pass(99, [])
    with pytest.raises(NotFound):
        s.get_report(99)


def test_compare_requires_detections():
    s = Store()
    a, b = s.create_patrol("lab", "a"), s.create_patrol("lab", "b")
    with pytest.raises(ValueError):
        run_comparison(s, b, a)


def test_persists_to_disk(tmp_path):
    path = tmp_path / "t.db"
    s = Store(path)
    pid = s.create_patrol("lab", "p")
    s.add_pass(pid, [Detection("desk", 2, 2, 0.9)])
    s.close()
    s2 = Store(path)
    assert s2.get_passes(pid)[0][0].cls == "desk"
