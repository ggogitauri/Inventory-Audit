#!/usr/bin/env python3
"""Summarize a Go2 rosbag without ROS: duration, odom travel, pass events.

    ./tools/bag_summary.py bags/baseline_loops
    ./tools/bag_summary.py bags/baseline_loops/baseline_loops_0.db3

Reads the sqlite3 bag directly and decodes nav_msgs/Odometry (CDR) for the
distance travelled. Useful for rejecting an "empty" baseline: a full 6 m loop
should show several metres of travel and at least one pass event.
"""
from __future__ import annotations

import argparse
import glob
import math
import sqlite3
import struct
import sys
from pathlib import Path

LOOP_METRES = 6.0


def _odom_pose(body: bytes):
    """Decode one CDR-serialized nav_msgs/Odometry (body = after encap)."""
    off = 0

    def align(n: int) -> None:
        nonlocal off
        off += (-off) % n

    def string() -> str:
        nonlocal off
        align(4)
        ln = struct.unpack_from("<I", body, off)[0]
        off += 4
        value = body[off:off + ln - 1].decode(errors="replace")
        off += ln
        return value

    align(4)
    sec, nsec = struct.unpack_from("<iI", body, off)
    off += 8
    string()  # frame_id
    string()  # child_frame_id
    align(8)
    x, y, z = struct.unpack_from("<ddd", body, off)
    return sec + nsec * 1e-9, (x, y, z)


def _bag_db(path: Path) -> Path:
    if path.is_dir():
        dbs = sorted(path.glob("*.db3"))
        if not dbs:
            raise SystemExit(f"no .db3 in {path}")
        return dbs[0]
    return path


def summarize(path: Path) -> int:
    db = _bag_db(path)
    con = sqlite3.connect(db)
    topics = {r[1]: r[0] for r in
              con.execute("select id, name from topics").fetchall()}
    counts = {}
    spans = {}
    for name, tid in topics.items():
        rows = con.execute(
            "select count(*), min(timestamp), max(timestamp) "
            "from messages where topic_id=?", (tid,)).fetchone()
        counts[name] = rows[0]
        if rows[0]:
            spans[name] = (rows[1], rows[2])

    pts = []
    if "/utlidar/robot_odom" in topics:
        tid = topics["/utlidar/robot_odom"]
        for (blob,) in con.execute(
                "select data from messages where topic_id=? order by timestamp",
                (tid,)):
            try:
                pts.append(_odom_pose(blob[4:]))
            except Exception:
                continue
    con.close()

    if pts:
        t0, t1 = pts[0][0], pts[-1][0]
        travel = sum(math.dist(pts[i][1][:2], pts[i + 1][1][:2])
                     for i in range(len(pts) - 1))
        xy = [p[1] for p in pts]
        xs = [p[0] for p in xy]
        ys = [p[1] for p in xy]
        print(f"bag:       {db.parent}")
        print(f"duration:  {t1 - t0:.1f} s")
        print(f"odom:      {len(pts)} msgs, travel {travel:.2f} m")
        print(f"extent:    x {min(xs):+.2f}..{max(xs):+.2f} m, "
              f"y {min(ys):+.2f}..{max(ys):+.2f} m")
    else:
        print(f"bag:       {db.parent}")
        print("odom:      MISSING (cannot measure travel)")
        travel = None

    passes = counts.get("/auditor/pass_complete", 0)
    print(f"passes:    {passes}")
    for topic in ("/utlidar/cloud_base", "/utlidar/imu", "/tf", "/tf_static"):
        print(f"  {topic}: {counts.get(topic, 0)}")

    problems = []
    if travel is not None and travel < LOOP_METRES:
        problems.append(f"travel {travel:.2f} m < loop length {LOOP_METRES:.0f} m")
    if passes == 0:
        problems.append("no /auditor/pass_complete")
    if problems:
        print("NOT A BASELINE: " + "; ".join(problems))
        return 1
    print("baseline looks usable")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("bag", nargs="?", default="", help="bag dir or .db3 path")
    args = p.parse_args(argv)
    if not args.bag:
        bags = sorted(glob.glob("bags/*/"))
        if not bags:
            print("no bags under bags/", file=sys.stderr)
            return 2
        print("bags:\n  " + "\n  ".join(b.rstrip("/") for b in bags))
        print("\npass a bag path to summarize it")
        return 0
    return summarize(Path(args.bag))


if __name__ == "__main__":
    raise SystemExit(main())
