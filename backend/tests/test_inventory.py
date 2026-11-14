from auditor.change_detection import Detection
from auditor.service import promote_patrol, run_comparison
from auditor.store import Store


def patrol(store, name, passes, area="lab"):
    pid = store.create_patrol(area, name)
    for dets in passes:
        store.add_pass(pid, dets)
    return pid


def test_state_mode_compares_against_expected_inventory():
    s = Store()
    baseline = patrol(s, "monday", [[Detection("chair", 1, 1), Detection("box", 5, 5)]])
    state = promote_patrol(s, baseline)
    assert state["patrol_id"] == baseline and len(state["assets"]) == 2

    current = patrol(s, "tuesday", [[Detection("chair", 1.1, 1)]])
    rep = run_comparison(s, current, baseline_mode="state")
    assert rep["baseline_id"] == baseline
    assert rep["baseline_mode"] == "state"
    assert rep["summary"]["missing"] == 1 and rep["summary"]["new"] == 0
    assert rep["changes"][0]["cls"] == "box"


def test_state_mode_requires_promoted_state():
    s = Store()
    pid = patrol(s, "only", [[Detection("chair", 1, 1)]])
    try:
        run_comparison(s, pid, baseline_mode="state")
    except ValueError as e:
        assert "no known state" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_repeated_promotion_keeps_asset_ids():
    s = Store()
    a = patrol(s, "a", [[Detection("chair", 1, 1, id="chair-a"), Detection("box", 5, 5, id="box-a")]])
    first = promote_patrol(s, a)
    by_cls = {asset["cls"]: asset["asset_uid"] for asset in first["assets"]}

    b = patrol(s, "b", [[Detection("chair", 1.2, 1), Detection("box", 5.1, 5)]])
    second = promote_patrol(s, b)
    for asset in second["assets"]:
        assert asset["asset_uid"] == by_cls[asset["cls"]]


def test_ignore_zones_suppress_changes_in_that_area():
    s = Store()
    base = patrol(s, "base", [[Detection("chair", 1, 1), Detection("box", 5, 5)]])
    cur = patrol(s, "cur", [[Detection("chair", 2, 4)]])
    s.add_ignore_zone("lab", 1, 1, 0.6, "messy corner")

    rep = run_comparison(s, cur, base)
    assert rep["summary"]["ignored"] == 1
    assert rep["summary"]["missing"] == 1
    assert [c["cls"] for c in rep["changes"]] == ["box"]


def test_report_review_and_filtering():
    s = Store()
    a = patrol(s, "a", [[Detection("chair", 1, 1)]])
    b = patrol(s, "b", [[Detection("desk", 2, 2)]])
    rep = run_comparison(s, b, a)
    assert rep["status"] == "open"
    assert rep["area"] == "lab"
    reviewed = s.review_report(rep["id"], "confirmed", "checked on site")
    assert reviewed["status"] == "confirmed" and reviewed["note"] == "checked on site"
    assert [r["id"] for r in s.list_reports(status="confirmed")] == [rep["id"]]
    assert s.list_reports(status="dismissed") == []
    try:
        s.review_report(rep["id"], "nonsense")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
