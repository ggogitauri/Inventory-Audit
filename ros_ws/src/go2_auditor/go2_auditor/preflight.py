"""One-command lab preflight for the real robot.

Read-only by default: it inspects the ROS graph, TF, sensor rates, clock skew and
the backend, then writes a markdown report plus a one-page lab run sheet.

    ros2 run go2_auditor preflight
    ros2 run go2_auditor preflight --check-upload --api-url http://localhost:8000

`--check-upload` publishes one synthetic schema-v2 pass (area `preflight-test`)
and waits for it to appear in the backend, proving ROS -> uploader -> API end to
end. It never commands the robot.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.time import Time
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String
from tf2_ros import Buffer, TransformListener

from .api_client import ApiClient, ApiError, ApiUnavailable
from .contract import CONTRACT_VERSION
from .preflight_core import (Check, build_test_pass, failed, render_report,
                             render_run_sheet, scan_stats, stamp)
from .topics import DETECTIONS_TOPIC, detections_qos


def _http(url: str, timeout: float = 3.0) -> tuple[int, str, dict]:
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (resp.status, resp.read().decode(errors="replace"),
                    {k.lower(): v for k, v in resp.headers.items()})
    except urllib.error.HTTPError as exc:
        return (exc.code, exc.read().decode(errors="replace"),
                {k.lower(): v for k, v in exc.headers.items()})
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
        raise ApiUnavailable(str(getattr(exc, "reason", exc))) from exc


class Preflight(Node):
    def __init__(self, opts: argparse.Namespace):
        super().__init__("preflight")
        self.opts = opts
        self.checks: list[Check] = []
        self.facts: dict[str, str] = {}
        generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        domain = os.environ.get("ROS_DOMAIN_ID", "0")
        rmw = os.environ.get("RMW_IMPLEMENTATION", "default")
        self.report = {
            "generated_at": generated,
            "host": socket.gethostname(),
            "domain": domain,
            "rmw": rmw,
            "api_url": opts.api_url,
            "map_frame": opts.map_frame,
            "base_frame": opts.base_frame,
            "checks": self.checks,
        }
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

    # -- reporting -------------------------------------------------------
    def _check(self, name: str, status: str, detail: str = "", hint: str = "") -> None:
        self.checks.append(Check(name, status, detail, hint))
        text = f"[{status.upper()}] {name}: {detail}" if detail else f"[{status.upper()}] {name}"
        # Separate call sites: rclpy keys logger state by caller, so routing every
        # severity through one line raises "Logger severity cannot be changed".
        if status == "fail":
            self.get_logger().error(text)
        elif status == "warn":
            self.get_logger().warning(text)
        elif status == "skip":
            self.get_logger().info(text)
        else:
            self.get_logger().info(text)

    # -- ROS helpers -----------------------------------------------------
    def _topic_types(self) -> dict[str, str]:
        """Topics published by someone other than this preflight node.

        `get_topic_names_and_types()` also lists topics this node merely
        subscribes to, so the TF listener alone made /tf and /tf_static look
        present on an otherwise silent robot. Counting only foreign publishers
        keeps a dead graph looking dead.
        """
        me = self.get_name()
        found: dict[str, str] = {}
        for name, types in self.get_topic_names_and_types():
            others = [info for info in self.get_publishers_info_by_topic(name)
                      if info.node_name != me]
            if others:
                found[name] = types[0] if types else "?"
        return found

    def _sample(self, topic: str, msg_type, seconds: float) -> tuple[float, list]:
        msgs: list = []
        qos = QoSProfile(depth=50, reliability=ReliabilityPolicy.BEST_EFFORT,
                         durability=DurabilityPolicy.VOLATILE)
        sub = self.create_subscription(msg_type, topic, msgs.append, qos)
        start = time.monotonic()
        end = start + seconds
        while time.monotonic() < end:
            rclpy.spin_once(self, timeout_sec=0.1)
        self.destroy_subscription(sub)
        return len(msgs) / max(1e-6, time.monotonic() - start), msgs

    def _wait_tf(self, target: str, source: str, timeout: float = 3.0):
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return self.tf_buffer.lookup_transform(target, source, Time()), None
            except Exception as exc:      # Lookup/Connectivity/Extrapolation
                last = exc
                rclpy.spin_once(self, timeout_sec=0.1)
        return None, last

    # -- checks ----------------------------------------------------------
    def check_environment(self) -> None:
        topics = self._topic_types()
        self.report["topic_count"] = len(topics)
        self._check("ROS graph", "ok" if topics else "fail",
                    f"{len(topics)} topic(s) published by other nodes on domain "
                    f"{self.report['domain']}",
                    "" if topics else "No other ROS node is publishing. Check "
                                      "ROS_DOMAIN_ID, the network cable, and that the "
                                      "Unitree bridge is running. Rehearsing on the VM? "
                                      "Start `ros2 launch go2_auditor sim_patrol.launch.py` "
                                      "first.")

        camera = sorted(t for t in topics if any(k in t.lower() for k in
                                                 ("camera", "image", "rgb", "video")))
        lidar = sorted(t for t in topics if any(k in t.lower() for k in
                                                ("lidar", "points", "cloud", "scan")))
        self.facts["lidar/scan topics"] = ", ".join(lidar) or "-"
        self.facts["camera topics"] = ", ".join(camera) or "-"
        self.report["topics"] = {"scan": self.opts.scan_topic,
                                 "odom": self.opts.odom_topic,
                                 "camera": camera, "lidar": lidar}
        self._check("TF topics", "ok" if "/tf" in topics else "fail",
                    ", ".join(t for t in ("/tf", "/tf_static") if t in topics) or "none",
                    "No /tf: the robot's TF publisher is not running.")

    def check_scan(self) -> None:
        topic = self.opts.scan_topic
        topics = self._topic_types()
        if topic not in topics:
            # Common on the Go2: the 4D LiDAR publishes PointCloud2, not LaserScan.
            clouds = sorted(t for t in (self.report["topics"].get("lidar") or [])
                            if topics.get(t) == "sensor_msgs/msg/PointCloud2")
            if clouds:
                self.report["topics"]["lidar_source"] = clouds[0]
                self.facts["scan topic"] = f"{clouds[0]} (PointCloud2, no {topic})"
                self._check("Laser scan topic", "warn",
                            f"{topic} not found; LiDAR publishes {clouds[0]}",
                            "Run pointcloud_to_laserscan for SLAM, or record the "
                            "cloud for offline use. Day-one loop counting only "
                            "needs odometry.")
                return
            self._check("Laser scan topic", "fail", f"{topic} not found",
                        "If the LiDAR publishes PointCloud2, run "
                        "pointcloud_to_laserscan to create /scan, or pass "
                        "--scan-topic with the real scan topic.")
            return
        self.facts["scan topic"] = f"{topic} ({topics[topic]})"
        self.report["topics"]["lidar_source"] = topic
        rate, msgs = self._sample(topic, LaserScan, self.opts.sample_seconds)
        if not msgs:
            self._check("Laser scan", "fail", f"no messages on {topic} in "
                        f"{self.opts.sample_seconds:.0f} s",
                        "Check the sensor driver and that the topic type matches "
                        "sensor_msgs/LaserScan.")
            return
        stats = scan_stats(msgs[-1].header.frame_id, msgs[-1].ranges,
                           msgs[-1].range_min, msgs[-1].range_max)
        self.report["scan"] = {**stats, "rate_hz": round(rate, 1)}
        self.facts["scan frame"] = stats["frame_id"] or "(empty)"
        if rate < 1.0:
            self._check("Laser scan rate", "warn", f"{rate:.1f} Hz",
                        "Below 1 Hz SLAM and the detector will struggle.")
        else:
            self._check("Laser scan rate", "ok", f"{rate:.1f} Hz on {topic}")
        if stats["in_range"] < max(10, stats["beams"] // 10):
            self._check("Laser scan content", "warn",
                        f"only {stats['in_range']}/{stats['beams']} beams in range",
                        "Check range_min/range_max and that the LiDAR is not covered.")
        else:
            self._check("Laser scan content", "ok",
                        f"{stats['in_range']}/{stats['beams']} beams, "
                        f"{stats['min']}–{stats['max']} m")
        if not stats["frame_id"]:
            self._check("Scan frame_id", "warn", "empty frame_id",
                        "TF cannot connect the scan without a frame.")
        else:
            self.scan_frame = stats["frame_id"]
            self._check("Scan frame_id", "ok", stats["frame_id"])

    def check_odom(self) -> None:
        topic = self.opts.odom_topic
        topics = self._topic_types()
        if topic not in topics:
            self._check("Odometry topic", "fail", f"{topic} not found",
                        "The passive coordinator needs odometry to count loops. "
                        "Pass --odom-topic with the robot's real topic.")
            return
        rate, msgs = self._sample(topic, Odometry, self.opts.sample_seconds)
        if not msgs:
            self._check("Odometry", "fail", f"no messages on {topic}")
            return
        msg = msgs[-1]
        self.report["odom"] = {
            "frame_id": msg.header.frame_id,
            "child_frame_id": msg.child_frame_id,
            "rate_hz": round(rate, 1),
            "x": round(msg.pose.pose.position.x, 3),
            "y": round(msg.pose.pose.position.y, 3),
        }
        self.facts["odom topic"] = f"{topic} ({msg.header.frame_id} -> {msg.child_frame_id})"
        self._check("Odometry rate", "ok" if rate >= 1 else "warn",
                    f"{rate:.1f} Hz on {topic}")

    def check_tf(self) -> None:
        map_frame, base = self.opts.map_frame, self.opts.base_frame
        tf, err = self._wait_tf(map_frame, base)
        if tf is not None:
            t = tf.transform.translation
            self.facts["map -> base"] = f"{map_frame} -> {base} at ({t.x:.2f}, {t.y:.2f})"
            self._check("TF chain", "ok", f"{map_frame} -> {base} "
                        f"({t.x:.2f}, {t.y:.2f})")
        else:
            odom_tf, _ = self._wait_tf("odom", base)
            if odom_tf is not None:
                self._check("Map frame", "warn", f"{map_frame} -> {base} unavailable; "
                            f"odom -> {base} works",
                            "Start SLAM, or publish an identity map->odom so detections "
                            "share a stable frame across patrols. Short session? Run "
                            "with --map-frame odom and keep baseline + changed together, "
                            "accepting odometry drift.")
            else:
                self._check("TF chain", "fail", f"no {map_frame} or odom -> {base}",
                            f"Check the robot's base frame name with --base-frame; "
                            f"it may be base_footprint.")
                return

        scan_frame = getattr(self, "scan_frame", "")
        if scan_frame:
            stf, verr = self._wait_tf(base, scan_frame)
            if stf is not None:
                t = stf.transform.translation
                self.facts[f"{base} -> scan"] = f"({t.x:.2f}, {t.y:.2f}, {t.z:.2f}) m"
                self._check("Sensor TF", "ok",
                            f"{base} -> {scan_frame} ({t.x:.2f}, {t.y:.2f}, {t.z:.2f}) m")
            else:
                self._check("Sensor TF", "warn", f"{base} -> {scan_frame} failed",
                            f"Detections cannot be placed without this transform: {verr}")

    def check_clock(self) -> None:
        topics = self._topic_types()
        if "/clock" in topics:
            self._check("Simulated clock", "warn", "/clock is being published",
                        "On the real robot keep use_sim_time false and stop the clock "
                        "publisher, or timestamps will not match wall time.")
        status, _, headers = _http(f"{self.opts.api_url}/health")
        date = headers.get("date")
        if not date:
            self._check("Clock skew", "skip", "backend sent no Date header")
            return
        server = parsedate_to_datetime(date)
        skew = (datetime.now(timezone.utc) - server).total_seconds()
        self.facts["backend clock skew"] = f"{skew:+.1f} s"
        # The HTTP Date header has one-second resolution, so a reading under ~2 s
        # is measurement granularity, not necessarily a real offset.
        if abs(skew) <= 2.0:
            self._check("Clock skew", "ok", f"{skew:+.1f} s vs backend "
                        "(1 s header resolution)")
        elif abs(skew) <= 10.0:
            self._check("Clock skew", "warn", f"{skew:+.1f} s vs backend",
                        "Enable NTP/chrony on the laptop and robot.")
        else:
            self._check("Clock skew", "fail", f"{skew:+.1f} s vs backend",
                        "Fix the laptop clock before recording; captured_at drives "
                        "patrol ordering.")

    def check_api(self) -> None:
        base = self.opts.api_url
        try:
            status, body, _ = _http(f"{base}/health")
        except ApiUnavailable as exc:
            self._check("Backend", "fail", f"{base} unreachable: {exc}",
                        "Start it with `docker compose up -d api`, or pass --api-url.")
            return
        if status != 200 or "ok" not in body:
            self._check("Backend", "fail", f"HTTP {status}: {body[:120]}")
            return
        self._check("Backend", "ok", f"{base}/health -> {body.strip()[:60]}")
        try:
            status, body, _ = _http(f"{base}/schema")
            version = json.loads(body).get("contract_version")
            if version == CONTRACT_VERSION:
                self._check("Contract version", "ok", f"schema v{version}")
            else:
                self._check("Contract version", "fail",
                            f"backend v{version}, software expects v{CONTRACT_VERSION}",
                            "Update the robot side or the backend so versions match.")
        except (ApiUnavailable, ValueError) as exc:
            self._check("Contract version", "warn", f"cannot read /schema: {exc}")

    def check_upload(self) -> None:
        if not self.opts.check_upload:
            self._check("Upload path", "skip", "not requested (use --check-upload)")
            return
        uid = f"preflight-{stamp()}"
        payload = build_test_pass(uid=uid, now=time.time())
        pub = self.create_publisher(String, DETECTIONS_TOPIC, detections_qos())
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and pub.get_subscription_count() == 0:
            rclpy.spin_once(self, timeout_sec=0.1)
        subs = pub.get_subscription_count()
        if subs == 0:
            self._check("Upload path", "fail", "no uploader subscribed to "
                        f"{DETECTIONS_TOPIC}",
                        "Start the uploader: `ros2 run go2_auditor patrol_uploader` "
                        "(or the full `patrol.launch.py`).")
            return
        msg = String()
        msg.data = json.dumps(payload)
        for _ in range(3):
            pub.publish(msg)
            rclpy.spin_once(self, timeout_sec=0.1)

        client = ApiClient(self.opts.api_url)
        found = None
        for _ in range(24):
            try:
                patrols = client.request(
                    "GET", f"/patrols?area={payload['area']}") or []
                for p in patrols:
                    if p.get("uid") == uid:
                        found = p
                        break
                if found is not None:
                    break
            except ApiUnavailable as exc:
                self._check("Upload path", "fail", f"API went away: {exc}")
                return
            time.sleep(0.5)
        if found is None:
            self._check("Upload path", "fail", "pass was published but never stored",
                        "Check the uploader log on this terminal; the API may be "
                        "rejecting the payload.")
            return
        try:
            passes = client.request("GET", f"/patrols/{found['id']}/passes") or []
        except (ApiUnavailable, ApiError) as exc:
            self._check("Upload path", "warn", f"patrol stored, passes unreadable: {exc}")
            return
        if passes:
            self._check("Upload path", "ok", f"patrol {found['id']} stored with "
                        f"{len(passes)} pass(es) — full chain works")
            self.facts["preflight patrol id"] = str(found["id"])
        else:
            self._check("Upload path", "warn", f"patrol {found['id']} stored without passes")

    # -- run -------------------------------------------------------------
    def run(self) -> int:
        self.check_environment()
        self.check_scan()
        self.check_odom()
        self.check_tf()
        self.check_api()
        try:
            self.check_clock()
        except ApiUnavailable as exc:
            self._check("Clock skew", "skip", f"API unreachable: {exc}")
        self.check_upload()

        self.report["facts"] = self.facts
        report = render_report(self.report)
        run_sheet = render_run_sheet(self.report)

        out_dir = self.opts.out or os.path.join(
            "/ws/robot_discovery" if os.path.isdir("/ws") else "robot_discovery",
            f"preflight-{stamp()}")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "preflight.md"), "w", encoding="utf-8") as fh:
            fh.write(report)
        if not self.opts.no_run_sheet:
            with open(os.path.join(out_dir, "run_sheet.md"), "w", encoding="utf-8") as fh:
                fh.write(run_sheet)

        print()
        print(report)
        print(f"Report:    {os.path.join(out_dir, 'preflight.md')}")
        if not self.opts.no_run_sheet:
            print(f"Run sheet: {os.path.join(out_dir, 'run_sheet.md')}")
        return 1 if failed(self.checks) else 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Read-only lab preflight for the Go2 auditor")
    p.add_argument("--api-url", default=os.environ.get("AUDITOR_API_URL",
                                                       "http://localhost:8000"))
    p.add_argument("--scan-topic", default="/scan")
    p.add_argument("--odom-topic", default="/odom")
    p.add_argument("--map-frame", default="map")
    p.add_argument("--base-frame", default="base_link")
    p.add_argument("--sample-seconds", type=float, default=5.0)
    p.add_argument("--check-upload", action="store_true",
                   help="publish one synthetic pass and verify it reaches the backend")
    p.add_argument("--out", default="", help="output directory for the reports")
    p.add_argument("--no-run-sheet", action="store_true")
    return p.parse_args(argv)


def main(args=None):
    opts = parse_args(sys.argv[1:])
    rclpy.init(args=args)
    node = Preflight(opts)
    code = 1
    try:
        code = node.run()
    except KeyboardInterrupt:
        code = 130
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    sys.exit(code)
