"""Rendering and small helpers for the lab preflight. No ROS imports.

Keeping this pure makes the report, the synthetic pass, and the generated run
sheet testable on any machine, while `preflight.py` does the ROS/HTTP work.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .contract import CONTRACT_VERSION

STATUS_LABEL = {"ok": "OK", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str = ""
    hint: str = ""

    def __post_init__(self) -> None:
        if self.status not in STATUS_LABEL:
            raise ValueError(f"bad check status {self.status!r}")


def scan_stats(frame_id: str, ranges, range_min: float, range_max: float) -> dict:
    """Summarize a LaserScan: how many beams actually saw something."""
    total = 0
    finite = 0
    in_range = 0
    lo = math.inf
    hi = -math.inf
    for value in ranges:
        total += 1
        r = float(value)
        if not math.isfinite(r):
            continue
        finite += 1
        if range_min <= r <= range_max:
            in_range += 1
            lo = min(lo, r)
            hi = max(hi, r)
    return {
        "frame_id": frame_id,
        "beams": total,
        "finite": finite,
        "in_range": in_range,
        "min": None if in_range == 0 else round(lo, 3),
        "max": None if in_range == 0 else round(hi, 3),
    }


def build_test_pass(area: str = "preflight-test", patrol: str = "preflight",
                    uid: str | None = None, now: float | None = None) -> dict:
    """One schema-v2 pass with two obvious fake detections.

    `last` is false and `n_passes` is 2 on purpose: the uploader must not fire an
    automatic comparison for a test area that has no baseline.
    """
    uid = uid or f"preflight:{int((now if now is not None else 0) * 1000)}"
    return {
        "area": area,
        "patrol": patrol,
        "patrol_uid": uid,
        "pass_uid": f"{uid}:0",
        "schema_version": CONTRACT_VERSION,
        "map_version": "preflight",
        "captured_at": now,
        "pass_idx": 0,
        "n_passes": 2,
        "last": False,
        "pose": {"x": 0.0, "y": 0.0, "theta": 0.0, "sigma": 0.0, "frame": "preflight"},
        "detections": [
            {"cls": "chair", "x": 1.0, "y": 1.0, "conf": 0.9,
             "pose_sigma": 0.05, "embedding": [0.6, 0.0, 0.0, 0.0, 0.0, 0.0, 0.8, 0.0]},
            {"cls": "box", "x": 2.0, "y": 1.0, "conf": 0.9,
             "pose_sigma": 0.05, "embedding": [0.0, 0.6, 0.0, 0.0, 0.8, 0.0, 0.0, 0.0]},
        ],
        "patrol_meta": {"preflight": True},
        "pass_meta": {"preflight": True},
    }


def failed(checks: list[Check]) -> bool:
    return any(c.status == "fail" for c in checks)


def _cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_report(facts: dict) -> str:
    """Markdown facts + pass/fail table for the lab."""
    checks: list[Check] = facts.get("checks", [])
    lines = [f"# Preflight report — {facts.get('generated_at', '')}", ""]
    lines.append(f"- Host: `{facts.get('host', '?')}`")
    lines.append(f"- ROS_DOMAIN_ID: `{facts.get('domain', '0')}`  "
                 f"RMW: `{facts.get('rmw', 'default')}`")
    lines.append(f"- API: `{facts.get('api_url', '')}`")
    verdict = "FAIL — fix these before driving" if failed(checks) else "PASS"
    lines += [f"- Verdict: **{verdict}**", "", "## Checks", "",
              "| Check | Result | Detail |", "|---|---|---|"]
    for c in checks:
        lines.append(f"| {_cell(c.name)} | **{STATUS_LABEL[c.status]}** | {_cell(c.detail)} |")
    hints = [c for c in checks if c.hint and c.status in ("warn", "fail")]
    if hints:
        lines += ["", "## Hints", ""]
        for c in hints:
            lines.append(f"- **{c.name}:** {c.hint}")

    lines += ["", "## Discovered facts", "", "| Item | Value |", "|---|---|"]
    for key, value in (facts.get("facts") or {}).items():
        lines.append(f"| {_cell(key)} | `{_cell(value)}` |")

    scan = facts.get("scan")
    if scan:
        lines += ["", "## Scan sample", "",
                  f"- Frame: `{scan.get('frame_id', '?')}`",
                  f"- Beams: {scan.get('beams')} "
                  f"({scan.get('in_range')} in range, {scan.get('finite')} finite)",
                  f"- Range: {scan.get('min')} … {scan.get('max')} m"]
    odom = facts.get("odom")
    if odom:
        lines += ["", "## Odometry sample", "",
                  f"- `{odom.get('frame_id', '?')}` → `{odom.get('child_frame_id', '?')}`, "
                  f"{odom.get('rate_hz', '?')} Hz"]
    lines.append("")
    return "\n".join(lines)


def render_run_sheet(facts: dict) -> str:
    """One-page run sheet with the discovered facts filled in."""
    checks: list[Check] = facts.get("checks", [])
    bad = [c for c in checks if c.status == "fail"]
    topics = facts.get("topics") or {}
    scan = topics.get("scan", "/scan")
    odom = topics.get("odom", "/odom")
    api = facts.get("api_url", "http://localhost:8000")
    route = "/ws/src/go2_auditor/config/patrol_route.yaml"

    lines = [f"# Lab run sheet — {facts.get('generated_at', '')}", ""]
    if bad:
        lines += [f"> **PREFLIGHT FAILED:** {', '.join(c.name for c in bad)}. "
                  "Fix these first; do not start motion.", ""]
    lines += [
        f"- ROS_DOMAIN_ID `{facts.get('domain', '0')}` · RMW `{facts.get('rmw', 'default')}`",
        f"- Scan `{scan}` · Odom `{odom}` · API `{api}`",
        "",
        "## 0. Before any motion (10 min)",
        "- [ ] E-stop in hand, area clear, motion limits agreed",
        "- [ ] `ros2 topic list` shows the expected robot topics",
        f"- [ ] `ros2 run tf2_ros tf2_echo {facts.get('map_frame', 'map')} "
        f"{facts.get('base_frame', 'base_link')}` resolves",
        "- [ ] Robot and laptop clocks agree within 1 s (`date -u`)",
        f"- [ ] `curl -s {api}/health` returns ok from this laptop",
        "",
        "## 1. Static, sensors only (15 min)",
        "- [ ] No motion. If the detector is ready, confirm one detection reaches the UI",
        "- [ ] Put a box at a taped distance; reported x,y within ~0.3 m",
        "",
        "## 2. Teleop loop + recording (30 min)",
        "- [ ] Start recording first:",
        f"      `ros2 bag record -o /ws/bags/lab_$(date +%H%M) {scan} {odom} /tf /tf_static`",
        "- [ ] Drive one slow loop by hand; stop recording",
        "- [ ] Confirm the coordinator logged exactly one pass for that loop",
        "",
        "## 3. Baseline patrol (2–3 loops)",
        "- [ ] Start the coordinator (it never commands motion):",
        f"      `ros2 launch go2_auditor patrol.launch.py route_file:={route}`",
        "- [ ] Every loop produced exactly one pass",
        "- [ ] Promote it once detections are uploaded (note the patrol id first):",
        f"      `curl -s -X POST {api}/patrols/<patrol-id>/promote "
        f"-H 'content-type: application/json' -d '{{}}'`",
        "",
        "## 4. Plant changes, then a changed patrol",
        "- [ ] Move 2 chairs, remove 1 box, add 1 object (write it down)",
        "- [ ] Same route, same speed, 2–3 loops",
        "- [ ] Report lists the planted changes",
        "",
        "## 5. Record results before packing up",
        "- [ ] Measured drift between baseline and changed: ______ m",
        "- [ ] Detector mistakes (missed / wrong class): ______",
        "- [ ] Copy `data/`, `bags/`, `ros_ws/robot_discovery/` off the laptop",
        "- [ ] Note anything odd: ______________________________________",
        "",
        "If a step fails: stop, fix it, rerun `tools/preflight.sh`.",
        "",
    ]
    return "\n".join(lines)


def stamp(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
