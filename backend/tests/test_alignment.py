import math

from auditor.alignment import (apply_transform, correct_drift, estimate_patrol_drift,
                               kabsch_2d)
from auditor.change_detection import Detection
from auditor.eval import Scenario, run_evaluation
from auditor.sim import drift_detections


def test_kabsch_recovers_known_transform():
    src = [(0, 0), (1, 0), (0, 1), (2, 3)]
    theta = 0.2
    dx, dy = 0.7, -0.4
    dst = [(math.cos(theta) * x - math.sin(theta) * y + dx,
            math.sin(theta) * x + math.cos(theta) * y + dy) for x, y in src]
    rdx, rdy, rtheta = kabsch_2d(src, dst)
    assert abs(rdx - dx) < 1e-9 and abs(rdy - dy) < 1e-9 and abs(rtheta - theta) < 1e-9


def test_apply_transform_matches_sim_drift():
    dets = [Detection("chair", 1.0, 2.0), Detection("desk", -1.0, 0.5)]
    a = apply_transform(dets, 0.3, -0.2, 0.1)
    b = drift_detections(dets, 0.3, -0.2, 0.1)
    assert all(abs(x.x - y.x) < 1e-9 and abs(x.y - y.y) < 1e-9 for x, y in zip(a, b))


def test_estimate_and_correct_drift_with_outliers():
    base = [Detection("chair", x, y) for x, y in
            [(0, 0), (2, 0), (4, 0), (6, 0), (0, 2), (2, 2), (4, 2), (6, 2)]]
    shifted = drift_detections(base, 0.4, -0.25, 0.0)
    # Two genuine changes should not win the RANSAC vote.
    shifted[0] = Detection("chair", 8, 7)
    shifted[1] = Detection("box", 9, 1)
    est = estimate_patrol_drift(base, shifted, min_pairs=5)
    assert est is not None
    dx, dy, th, n = est
    assert abs(dx + 0.4) < 0.12 and abs(dy - 0.25) < 0.12 and n >= 5


def test_drift_correction_improves_simulated_precision():
    without = run_evaluation(Scenario(trials=15, drift_shift=0.25, correct_drift=False))
    with_fix = run_evaluation(Scenario(trials=15, drift_shift=0.25, correct_drift=True))
    assert with_fix.precision > without.precision + 0.2
    assert with_fix.recall >= without.recall - 0.05
