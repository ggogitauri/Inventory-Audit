import json
import os
import tempfile

from go2_auditor.replay_core import load_messages, validate_message


def write(lines):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    for line in lines:
        f.write((line if isinstance(line, str) else json.dumps(line)) + "\n")
    f.close()
    return f.name


def test_loads_and_skips_comments_and_blanks():
    path = write(["# a comment", "", {"area": "lab", "patrol": "p", "pass_idx": 0, "detections": []}])
    try:
        msgs = load_messages(path)
        assert len(msgs) == 1 and msgs[0]["area"] == "lab"
    finally:
        os.unlink(path)


def test_rejects_bad_json():
    path = write(["{not json"])
    try:
        try:
            load_messages(path)
        except ValueError as e:
            assert "bad JSON" in str(e)
        else:
            raise AssertionError("expected ValueError")
    finally:
        os.unlink(path)


def test_rejects_missing_fields_and_bad_detections():
    path = write([{"area": "lab", "patrol": "p"}])
    try:
        try:
            load_messages(path)
        except ValueError as e:
            assert "missing fields" in str(e)
        else:
            raise AssertionError("expected ValueError")
    finally:
        os.unlink(path)

    path = write([{"area": "lab", "patrol": "p", "pass_idx": 0, "detections": [{"cls": "chair"}]}])
    try:
        try:
            load_messages(path)
        except ValueError as e:
            assert "cls, x, y" in str(e)
        else:
            raise AssertionError("expected ValueError")
    finally:
        os.unlink(path)


def test_validate_accepts_contract_v2_message():
    validate_message({"area": "lab", "patrol": "p", "pass_idx": 0,
                      "patrol_uid": "u", "pass_uid": "u:0", "schema_version": 2,
                      "captured_at": 1.0, "pose": {"x": 0, "y": 0},
                      "detections": [{"cls": "chair", "x": 1, "y": 2, "conf": 0.9}]})


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)
