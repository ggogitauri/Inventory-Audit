"""SQLite persistence for patrols, raw detection passes, and change reports.

Standard library only. One connection per Store; use one Store per thread/request
(the FastAPI layer opens a short-lived Store per request).

Schema evolution: `_migrate()` adds columns that are missing from older databases,
so an existing `auditor.db` keeps working after an upgrade. Every write that has a
client-supplied uid is idempotent: a retried request returns the original row
instead of creating a duplicate.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .change_detection import Detection
from .contract import ALGORITHM_VERSION, CONTRACT_VERSION

SCHEMA = """
CREATE TABLE IF NOT EXISTS patrols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    area TEXT NOT NULL,            -- e.g. "lab-main"; baselines are chosen within an area
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    uid TEXT,                      -- client-generated idempotency key
    map_version TEXT,              -- maps are only compared within the same version
    schema_version INTEGER,
    meta TEXT                      -- JSON: detector version, robot, notes, ...
);
CREATE TABLE IF NOT EXISTS passes (
    patrol_id INTEGER NOT NULL REFERENCES patrols(id) ON DELETE CASCADE,
    pass_idx INTEGER NOT NULL,
    uid TEXT,                      -- client-generated idempotency key
    captured_at REAL,
    pose TEXT,                     -- JSON: {x,y,theta,sigma,...} in map frame
    meta TEXT,                     -- JSON
    PRIMARY KEY (patrol_id, pass_idx)
);
CREATE TABLE IF NOT EXISTS detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patrol_id INTEGER NOT NULL REFERENCES patrols(id) ON DELETE CASCADE,
    pass_idx INTEGER NOT NULL,
    cls TEXT NOT NULL,
    x REAL NOT NULL, y REAL NOT NULL,
    conf REAL NOT NULL,
    image TEXT,                    -- media key or path to the supporting photo
    pose_sigma REAL,               -- 1-sigma position uncertainty in metres
    embedding TEXT                 -- JSON array of floats, optional appearance feature
);
CREATE INDEX IF NOT EXISTS idx_det_patrol ON detections(patrol_id, pass_idx);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    baseline_id INTEGER NOT NULL REFERENCES patrols(id),
    current_id INTEGER NOT NULL REFERENCES patrols(id),
    created_at REAL NOT NULL,
    summary TEXT NOT NULL,         -- JSON
    changes TEXT NOT NULL,         -- JSON
    config TEXT,                   -- JSON ChangeConfig used for this report
    algorithm_version TEXT,
    baseline_mode TEXT,            -- "previous", "explicit", "state"
    alignment TEXT,                -- JSON drift transform applied before matching
    status TEXT DEFAULT 'open',    -- open | confirmed | dismissed
    note TEXT,
    reviewed_at REAL
);
CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    area TEXT NOT NULL,
    asset_uid TEXT NOT NULL,       -- stable id for this expected object
    cls TEXT NOT NULL,
    x REAL NOT NULL, y REAL NOT NULL,
    conf REAL NOT NULL DEFAULT 1.0,
    pose_sigma REAL,
    map_version TEXT,
    source_patrol_id INTEGER,
    updated_at REAL NOT NULL,
    meta TEXT,
    UNIQUE(area, asset_uid)
);
CREATE INDEX IF NOT EXISTS idx_assets_area ON assets(area);
CREATE TABLE IF NOT EXISTS area_state (
    area TEXT PRIMARY KEY,
    patrol_id INTEGER NOT NULL REFERENCES patrols(id),
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ignore_zones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    area TEXT NOT NULL,
    x REAL NOT NULL, y REAL NOT NULL, radius REAL NOT NULL,
    label TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zones_area ON ignore_zones(area);
"""

# Columns added after the first prototype. Table -> {column: declaration}.
_ADDED_COLUMNS = {
    "patrols": {
        "uid": "TEXT",
        "map_version": "TEXT",
        "schema_version": "INTEGER",
        "meta": "TEXT",
    },
    "passes": {
        "uid": "TEXT",
        "captured_at": "REAL",
        "pose": "TEXT",
        "meta": "TEXT",
    },
    "detections": {
        "pose_sigma": "REAL",
        "embedding": "TEXT",
    },
    "reports": {
        "config": "TEXT",
        "algorithm_version": "TEXT",
        "baseline_mode": "TEXT",
        "alignment": "TEXT",
        "status": "TEXT DEFAULT 'open'",
        "note": "TEXT",
        "reviewed_at": "REAL",
    },
}


class NotFound(Exception):
    pass


class Conflict(Exception):
    """A client uid belongs to a different row than the one being written."""


def _json(value):
    return None if value is None else json.dumps(value, separators=(",", ":"), sort_keys=True)


def _load(value, default=None):
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _det_from_row(r: sqlite3.Row) -> Detection:
    emb = _load(r["embedding"])
    return Detection(r["cls"], r["x"], r["y"], r["conf"], image=r["image"],
                     pose_sigma=r["pose_sigma"],
                     embedding=tuple(emb) if emb is not None else None)


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        for table, columns in _ADDED_COLUMNS.items():
            have = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns.items():
                if name not in have:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        # Partial unique indexes: NULL uids (old clients) are allowed many times.
        self.db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_patrol_uid ON patrols(uid) WHERE uid IS NOT NULL"
        )
        self.db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_pass_uid ON passes(uid) WHERE uid IS NOT NULL"
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ---- patrols -------------------------------------------------------
    def create_patrol(self, area: str, name: str, uid: str | None = None,
                      map_version: str | None = None,
                      schema_version: int = CONTRACT_VERSION,
                      meta: dict | None = None) -> int:
        if uid:
            existing = self.find_patrol_by_uid(uid)
            if existing is not None:
                return existing["id"]
        cur = self.db.execute(
            "INSERT INTO patrols(area, name, created_at, uid, map_version, schema_version, meta) "
            "VALUES (?,?,?,?,?,?,?)",
            (area, name, time.time(), uid, map_version, schema_version, _json(meta)),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def find_patrol_by_uid(self, uid: str) -> dict | None:
        row = self.db.execute("SELECT id FROM patrols WHERE uid=?", (uid,)).fetchone()
        return None if row is None else self.get_patrol(int(row["id"]))

    def get_patrol(self, patrol_id: int) -> dict:
        row = self.db.execute("SELECT * FROM patrols WHERE id=?", (patrol_id,)).fetchone()
        if row is None:
            raise NotFound(f"patrol {patrol_id}")
        n_passes = self.db.execute(
            "SELECT COUNT(*) FROM passes WHERE patrol_id=?", (patrol_id,)
        ).fetchone()[0]
        d = dict(row)
        d["meta"] = _load(d.get("meta"))
        return {**d, "passes": n_passes}

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

    # ---- known state / expected inventory ------------------------------
    def get_area_assets(self, area: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM assets WHERE area=? ORDER BY asset_uid", (area,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["meta"] = _load(d.get("meta"))
            out.append(d)
        return out

    def get_area_state(self, area: str) -> dict | None:
        row = self.db.execute("SELECT * FROM area_state WHERE area=?", (area,)).fetchone()
        if row is None:
            return None
        return {"area": area, "patrol_id": int(row["patrol_id"]),
                "updated_at": row["updated_at"], "assets": self.get_area_assets(area)}

    def set_area_state(self, area: str, patrol_id: int, assets: list[dict]) -> None:
        """Replace the expected inventory for an area with `assets`."""
        self.db.execute("DELETE FROM assets WHERE area=?", (area,))
        now = time.time()
        self.db.executemany(
            "INSERT INTO assets(area, asset_uid, cls, x, y, conf, pose_sigma, map_version, "
            "source_patrol_id, updated_at, meta) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(area, a["asset_uid"], a["cls"], a["x"], a["y"], a.get("conf", 1.0),
              a.get("pose_sigma"), a.get("map_version"), a.get("source_patrol_id", patrol_id),
              now, _json(a.get("meta"))) for a in assets],
        )
        self.db.execute(
            "INSERT INTO area_state(area, patrol_id, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(area) DO UPDATE SET patrol_id=excluded.patrol_id, updated_at=excluded.updated_at",
            (area, patrol_id, now),
        )
        self.db.commit()

    def clear_area_state(self, area: str) -> None:
        self.db.execute("DELETE FROM assets WHERE area=?", (area,))
        self.db.execute("DELETE FROM area_state WHERE area=?", (area,))
        self.db.commit()

    def add_ignore_zone(self, area: str, x: float, y: float, radius: float,
                        label: str | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO ignore_zones(area, x, y, radius, label, created_at) VALUES (?,?,?,?,?,?)",
            (area, x, y, radius, label, time.time()),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def list_ignore_zones(self, area: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM ignore_zones WHERE area=? ORDER BY id", (area,))]

    def delete_ignore_zone(self, zone_id: int) -> bool:
        cur = self.db.execute("DELETE FROM ignore_zones WHERE id=?", (zone_id,))
        self.db.commit()
        return cur.rowcount > 0

    # ---- detections ----------------------------------------------------
    def add_pass(self, patrol_id: int, detections: list[Detection], uid: str | None = None,
                 captured_at: float | None = None, pose: dict | None = None,
                 meta: dict | None = None) -> int:
        self.get_patrol(patrol_id)  # raises NotFound
        if uid:
            row = self.db.execute(
                "SELECT patrol_id, pass_idx FROM passes WHERE uid=?", (uid,)
            ).fetchone()
            if row is not None:
                if int(row["patrol_id"]) != patrol_id:
                    raise Conflict(f"pass uid {uid} belongs to patrol {row['patrol_id']}")
                return int(row["pass_idx"])
        row = self.db.execute(
            "SELECT COALESCE(MAX(pass_idx), -1) + 1 FROM passes WHERE patrol_id=?", (patrol_id,)
        ).fetchone()
        idx = int(row[0])
        # Recorded even when empty: "saw nothing" is real evidence for the voting step.
        self.db.execute(
            "INSERT INTO passes(patrol_id, pass_idx, uid, captured_at, pose, meta) VALUES (?,?,?,?,?,?)",
            (patrol_id, idx, uid, captured_at, _json(pose), _json(meta)),
        )
        self.db.executemany(
            "INSERT INTO detections(patrol_id, pass_idx, cls, x, y, conf, image, pose_sigma, embedding) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [(patrol_id, idx, d.cls, d.x, d.y, d.conf, d.image, d.pose_sigma, _json(d.embedding))
             for d in detections],
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
            "SELECT pass_idx, cls, x, y, conf, image, pose_sigma, embedding FROM detections "
            "WHERE patrol_id=? ORDER BY pass_idx, id",
            (patrol_id,),
        ):
            passes[r["pass_idx"]].append(_det_from_row(r))
        return [passes[k] for k in sorted(passes)]

    def get_pass_records(self, patrol_id: int) -> list[dict]:
        """Pass metadata plus its detections, for inspection/debugging."""
        self.get_patrol(patrol_id)
        out: list[dict] = []
        for r in self.db.execute(
            "SELECT * FROM passes WHERE patrol_id=? ORDER BY pass_idx", (patrol_id,)
        ):
            dets = [
                {"cls": d["cls"], "x": d["x"], "y": d["y"], "conf": d["conf"],
                 "image": d["image"], "pose_sigma": d["pose_sigma"]}
                for d in self.db.execute(
                    "SELECT cls, x, y, conf, image, pose_sigma FROM detections "
                    "WHERE patrol_id=? AND pass_idx=? ORDER BY id",
                    (patrol_id, r["pass_idx"]),
                )
            ]
            out.append({"pass_idx": r["pass_idx"], "uid": r["uid"],
                        "captured_at": r["captured_at"], "pose": _load(r["pose"]),
                        "meta": _load(r["meta"]), "detections": dets})
        return out

    # ---- reports -------------------------------------------------------
    def save_report(self, baseline_id: int, current_id: int, summary: dict, changes: list[dict],
                    config: dict | None = None, algorithm_version: str | None = ALGORITHM_VERSION,
                    baseline_mode: str | None = None, alignment: dict | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO reports(baseline_id, current_id, created_at, summary, changes, config, "
            "algorithm_version, baseline_mode, alignment) VALUES (?,?,?,?,?,?,?,?,?)",
            (baseline_id, current_id, time.time(), _json(summary), _json(changes),
             _json(config), algorithm_version, baseline_mode, _json(alignment)),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def get_report(self, report_id: int) -> dict:
        r = self.db.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        if r is None:
            raise NotFound(f"report {report_id}")
        d = dict(r)
        d["summary"] = _load(d["summary"], {})
        d["changes"] = _load(d["changes"], [])
        d["config"] = _load(d.get("config"))
        d["alignment"] = _load(d.get("alignment"))
        p = self.db.execute("SELECT area FROM patrols WHERE id=?", (d["current_id"],)).fetchone()
        d["area"] = p["area"] if p else None
        return d

    def list_reports(self, area: str | None = None, status: str | None = None,
                     limit: int = 100) -> list[dict]:
        q = ("SELECT r.id FROM reports r JOIN patrols p ON p.id=r.current_id")
        where, args = [], []
        if area:
            where.append("p.area=?"); args.append(area)
        if status:
            where.append("r.status=?"); args.append(status)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY r.id DESC LIMIT ?"
        args.append(int(limit))
        return [self.get_report(r["id"]) for r in self.db.execute(q, args)]

    def review_report(self, report_id: int, status: str, note: str | None = None) -> dict:
        if status not in ("open", "confirmed", "dismissed"):
            raise ValueError("status must be open, confirmed or dismissed")
        self.get_report(report_id)
        self.db.execute("UPDATE reports SET status=?, note=?, reviewed_at=? WHERE id=?",
                        (status, note, time.time() if status != "open" else None, report_id))
        self.db.commit()
        return self.get_report(report_id)
