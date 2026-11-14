import sqlite3

import pytest

from auditor.change_detection import Detection
from auditor.service import run_comparison
from auditor.store import Store


def test_duplicate_patrol_uid_is_idempotent(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    monkeypatch.setenv("AUDITOR_DB", str(tmp_path / "idem.db"))
    from auditor.api import app
    c = TestClient(app)

    body = {"area": "lab", "name": "monday", "uid": "patrol-abc",
            "map_version": "map-1", "meta": {"detector": "yolo-v8"}}
    a = c.post("/patrols", json=body)
    b = c.post("/patrols", json=body)
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] == b.json()["id"]
    assert len(c.get("/patrols").json()) == 1

    clash = c.post("/patrols", json={**body, "name": "tuesday"})
    assert clash.status_code == 409


def test_duplicate_pass_uid_is_idempotent(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    monkeypatch.setenv("AUDITOR_DB", str(tmp_path / "idem2.db"))
    from auditor.api import app
    c = TestClient(app)

    pid = c.post("/patrols", json={"area": "lab", "name": "p", "uid": "p1"}).json()["id"]
    body = {"uid": "pass-1", "captured_at": 123.0, "pose": {"x": 1.0, "y": 2.0, "theta": 0.1},
            "detections": [{"cls": "chair", "x": 1, "y": 2, "conf": 0.9, "pose_sigma": 0.12}]}
    a = c.post(f"/patrols/{pid}/passes", json=body)
    b = c.post(f"/patrols/{pid}/passes", json=body)
    assert a.status_code == b.status_code == 201
    assert a.json()["pass_idx"] == b.json()["pass_idx"] == 0
    passes = c.get(f"/patrols/{pid}/passes").json()
    assert len(passes) == 1 and passes[0]["uid"] == "pass-1"
    assert passes[0]["pose"]["theta"] == 0.1
    assert passes[0]["detections"][0]["pose_sigma"] == 0.12


def test_duplicate_pass_uid_on_other_patrol_conflicts(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    monkeypatch.setenv("AUDITOR_DB", str(tmp_path / "idem3.db"))
    from auditor.api import app
    c = TestClient(app)
    a = c.post("/patrols", json={"area": "lab", "name": "a", "uid": "a"}).json()["id"]
    b = c.post("/patrols", json={"area": "lab", "name": "b", "uid": "b"}).json()["id"]
    body = {"uid": "shared", "detections": []}
    assert c.post(f"/patrols/{a}/passes", json=body).status_code == 201
    assert c.post(f"/patrols/{b}/passes", json=body).status_code == 409


def test_report_stores_config_and_versions():
    s = Store()
    a = s.create_patrol("lab", "a")
    b = s.create_patrol("lab", "b")
    s.add_pass(a, [Detection("chair", 1, 1, embedding=(0.6, 0.8))])
    s.add_pass(b, [Detection("chair", 1.1, 1, embedding=(0.6, 0.8))])
    assert s.get_passes(a)[0][0].embedding == (0.6, 0.8)
    rep = run_comparison(s, b, a)
    assert rep["config"]["match_radius"] == 2.0
    assert rep["config"]["algorithm_version"] == "2.0"
    assert rep["algorithm_version"] == "2.0"
    assert rep["baseline_mode"] == "explicit"


def test_old_database_is_migrated(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE patrols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            area TEXT NOT NULL, name TEXT NOT NULL, created_at REAL NOT NULL);
    """)
    db.commit()
    db.close()

    s = Store(path)
    pid = s.create_patrol("lab", "p", uid="u1", map_version="m1", meta={"x": 1})
    s.add_pass(pid, [Detection("desk", 2, 2, 0.9, pose_sigma=0.1)], uid="pass1")
    s.close()

    s2 = Store(path)
    assert s2.find_patrol_by_uid("u1")["map_version"] == "m1"
    rec = s2.get_pass_records(pid)[0]
    assert rec["uid"] == "pass1" and rec["detections"][0]["pose_sigma"] == 0.1
