"""Read recorded detection passes from JSONL. Pure standard library, unit-testable without ROS.

A JSONL file is one JSON object per line, in the same schema mock_perception publishes.
Record a real run with:

    ros2 bag record -o bags/monday /auditor/detections

or, for a portable text fixture, save the JSON payloads one per line and replay them.
"""
from __future__ import annotations

import json

REQUIRED = ("area", "patrol", "pass_idx", "detections")


def validate_message(msg: dict) -> None:
    missing = [k for k in REQUIRED if k not in msg]
    if missing:
        raise ValueError(f"missing fields: {missing}")
    if not isinstance(msg["detections"], list):
        raise ValueError("detections must be a list")
    for i, d in enumerate(msg["detections"]):
        if not isinstance(d, dict) or not {"cls", "x", "y"} <= set(d):
            raise ValueError(f"detection {i} must contain cls, x, y")


def load_messages(path: str) -> list[dict]:
    """Load and validate a JSONL replay file."""
    out: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{n}: bad JSON: {e}") from e
            try:
                validate_message(msg)
            except ValueError as e:
                raise ValueError(f"{path}:{n}: {e}") from e
            out.append(msg)
    if not out:
        raise ValueError(f"{path}: no messages")
    return out
