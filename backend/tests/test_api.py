import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_DB", str(tmp_path / "api.db"))
    from auditor.api import app
    return TestClient(app)


def post_patrol(client, name, dets, passes=3):
    pid = client.post("/patrols", json={"area": "lab", "name": name}).json()["id"]
    for _ in range(passes):
        r = client.post(f"/patrols/{pid}/passes", json={"detections": dets})
        assert r.status_code == 201
    return pid


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_full_flow(client):
    base = [{"cls": "chair", "x": 1, "y": 1, "conf": 0.9}, {"cls": "box", "x": 5, "y": 5, "conf": 0.9}]
    curr = [{"cls": "chair", "x": 2.5, "y": 1, "conf": 0.9}, {"cls": "monitor", "x": 8, "y": 3, "conf": 0.9}]
    a = post_patrol(client, "mon", base)
    b = post_patrol(client, "tue", curr)
    r = client.post(f"/patrols/{b}/compare")
    assert r.status_code == 201
    body = r.json()
    assert body["baseline_id"] == a
    assert body["summary"] == {"moved": 1, "missing": 1, "new": 1, "unchanged": 0}
    assert client.get(f"/reports/{body['id']}").json()["summary"] == body["summary"]
    assert len(client.get("/patrols").json()) == 2


def test_errors(client):
    assert client.get("/patrols/99").status_code == 404
    assert client.get("/reports/99").status_code == 404
    assert client.post("/patrols/99/passes", json={"detections": []}).status_code == 404
    pid = post_patrol(client, "only", [{"cls": "chair", "x": 1, "y": 1}])
    assert client.post(f"/patrols/{pid}/compare").status_code == 422   # nothing to compare against
    assert client.post(f"/patrols/{pid}/passes", json={"detections": [{"cls": "c", "x": 0, "y": 0, "conf": 2}]}).status_code == 422
