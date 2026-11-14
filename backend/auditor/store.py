"""SQLite persistence for patrols, raw detection passes, and change reports.

Standard library only. One connection per Store; use one Store per thread/request
(the FastAPI layer opens a short-lived Store per request).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .change_detection import Detection

SCHEMA = """
CREATE TABLE IF NOT EXISTS patrols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    area TEXT NOT NULL,            -- e.g. "lab-main"; baselines are chosen within an area
    name TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS passes (
    patrol_id INTEGER NOT NULL REFERENCES patrols(id) ON DELETE CASCADE,
    pass_idx INTEGER NOT NULL,
    PRIMARY KEY (patrol_id, pass_idx)
);
CREATE TABLE IF NOT EXISTS detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patrol_id INTEGER NOT NULL REFERENCES patrols(id) ON DELETE CASCADE,
    pass_idx INTEGER NOT NULL,
    cls TEXT NOT NULL,
    x REAL NOT NULL, y REAL NOT NULL,
    conf REAL NOT NULL,
    image TEXT
);
CREATE INDEX IF NOT EXISTS idx_det_patrol ON detections(patrol_id, pass_idx);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    baseline_id INTEGER NOT NULL REFERENCES patrols(id),
    current_id INTEGER NOT NULL REFERENCES patrols(id),
    created_at REAL NOT NULL,
    summary TEXT NOT NULL,         -- JSON
    changes TEXT NOT NULL          -- JSON
);
"""


class NotFound(Exception):
    pass


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ---- patrols -------------------------------------------------------
    def create_patrol(self, area: str, name: str) -> int:
        cur = self.db.execute(
            "INSERT INTO patrols(area, name, created_at) VALUES (?,?,?)", (area, name, time.time())
        )
        self.db.commit()
        return int(cur.lastrowid)

    def get_patrol(self, patrol_id: int) -> dict:
        row = self.db.execute("SELECT * FROM patrols WHERE id=?", (patrol_id,)).fetchone()
        if row is None:
            raise NotFound(f"patrol {patrol_id}")
        n_passes = self.db.execute(
            "SELECT COUNT(*) FROM passes WHERE patrol_id=?", (patrol_id,)
        ).fetchone()[0]
        return {**dict(row), "passes": n_passes}

    def list_patrols(self, area: str | None = None) -> list[dict]:
        q, args = "SELECT id FROM patrols", ()
        if area:
            q, args = q + " WHERE area=?", (area,)
        ids = [r["id"] for r in self.db.execute(q + " ORDER BY id DESC", args)]
        return [self.get_patrol(i) for i in ids]

    def previous_patrol(self, patrol_id: int) -> int | None:
        """Most recent earlier patrol in the same area, used as the default baseline."""
        p = self.get_patrol(patrol_id)
        row = self.db.execute(
            "SELECT id FROM patrols WHERE area=? AND id<? ORDER BY id DESC LIMIT 1", (p["area"], patrol_id)
        ).fetchone()
        return int(row["id"]) if row else None

    # ---- detections ----------------------------------------------------
    def add_pass(self, patrol_id: int, detections: list[Detection]) -> int:
        self.get_patrol(patrol_id)  # raises NotFound
        row = self.db.execute(
            "SELECT COALESCE(MAX(pass_idx), -1) + 1 FROM passes WHERE patrol_id=?", (patrol_id,)
        ).fetchone()
        idx = int(row[0])
        # Recorded even when empty: "saw nothing" is real evidence for the voting step.
        self.db.execute("INSERT INTO passes(patrol_id, pass_idx) VALUES (?,?)", (patrol_id, idx))
        self.db.executemany(
            "INSERT INTO detections(patrol_id, pass_idx, cls, x, y, conf, image) VALUES (?,?,?,?,?,?,?)",
            [(patrol_id, idx, d.cls, d.x, d.y, d.conf, d.image) for d in detections],
        )
        self.db.commit()
        return idx

    def get_passes(self, patrol_id: int) -> list[list[Detection]]:
        self.get_patrol(patrol_id)
        passes: dict[int, list[Detection]] = {
            r["pass_idx"]: [] for r in self.db.execute(
                "SELECT pass_idx FROM passes WHERE patrol_id=? ORDER BY pass_idx", (patrol_id,))
        }
        for r in self.db.execute(
            "SELECT pass_idx, cls, x, y, conf, image FROM detections WHERE patrol_id=? ORDER BY pass_idx, id",
            (patrol_id,),
        ):
            passes[r["pass_idx"]].append(Detection(r["cls"], r["x"], r["y"], r["conf"], image=r["image"]))
        return [passes[k] for k in sorted(passes)]

    # ---- reports -------------------------------------------------------
    def save_report(self, baseline_id: int, current_id: int, summary: dict, changes: list[dict]) -> int:
        cur = self.db.execute(
            "INSERT INTO reports(baseline_id, current_id, created_at, summary, changes) VALUES (?,?,?,?,?)",
            (baseline_id, current_id, time.time(), json.dumps(summary), json.dumps(changes)),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def get_report(self, report_id: int) -> dict:
        r = self.db.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        if r is None:
            raise NotFound(f"report {report_id}")
        d = dict(r)
        d["summary"] = json.loads(d["summary"])
        d["changes"] = json.loads(d["changes"])
        return d
