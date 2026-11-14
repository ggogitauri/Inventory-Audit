import json

from make_replay import build_replay


def _load(path):
    return [json.loads(line) for line in open(path) if line.strip()]


def test_build_replay_writes_contract_messages(tmp_path):
    path = str(tmp_path / "monday.jsonl")
    info = build_replay(path, scenario="baseline", name="monday", passes=3)
    msgs = _load(path)
    assert info["messages"] == 3 and len(msgs) == 3
    assert [m["pass_idx"] for m in msgs] == [0, 1, 2]
    assert [m["last"] for m in msgs] == [False, False, True]
    assert msgs[0]["schema_version"] == 2
    assert msgs[0]["patrol_uid"] == msgs[1]["patrol_uid"] == info["patrol_uid"]
    assert msgs[0]["pass_uid"] != msgs[1]["pass_uid"]
    assert msgs[0]["detections"] and "embedding" in msgs[0]["detections"][0]
    assert info["planted"] is None


def test_changed_replay_records_planted_changes(tmp_path):
    path = str(tmp_path / "tuesday.jsonl")
    info = build_replay(path, scenario="changed", name="tuesday")
    assert info["planted"]["moved"] and info["planted"]["removed"] and info["planted"]["added"]
