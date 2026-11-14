from auditor.change_detection import Detection
from auditor.eval import Scenario, drift_sweep, run_evaluation
from auditor.metrics import detection_calibration


def test_evaluation_reports_overall_and_per_class():
    res = run_evaluation(Scenario(trials=12, min_votes=2))
    assert res.tp + res.fp > 0
    assert 0.0 < res.precision <= 1.0 and 0.0 < res.recall <= 1.0
    assert res.per_class and all("precision" in v and "support" in v for v in res.per_class.values())
    d = res.as_dict()
    assert d["scenario"]["trials"] == 12 and d["per_class"] == res.per_class


def test_single_pass_is_worse_than_multi_pass():
    one = run_evaluation(Scenario(trials=20, passes=1, min_votes=1))
    three = run_evaluation(Scenario(trials=20, passes=3, min_votes=2))
    assert three.precision > one.precision and three.recall > one.recall


def test_drift_sweep_degrades_precision():
    results = drift_sweep([0.0, 0.30], Scenario(trials=20, min_votes=2))
    assert results[0].precision > results[1].precision


def test_detection_calibration_returns_bins():
    truth = [Detection("chair", 1, 1), Detection("desk", 5, 5)]
    dets = [Detection("chair", 1.05, 1.0, 0.9), Detection("chair", 8, 8, 0.45)]
    bins = detection_calibration(dets, truth, bins=4)
    assert len(bins) == 4 and sum(b["n"] for b in bins) == 2
    high = [b for b in bins if b["mean_conf"] is not None and b["mean_conf"] >= 0.75][0]
    assert high["accuracy"] == 1.0
