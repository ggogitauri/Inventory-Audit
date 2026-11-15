"""Passive patrol coordinator for the real robot.

It does **not** command motion. You drive with teleop, Nav2, or the Unitree app;
the coordinator watches odometry, counts one pass per completed loop, and
publishes a pass event that the detector turns into a schema-v2 pass.

    ros2 launch go2_auditor patrol.launch.py route_file:=/ws/src/go2_auditor/config/patrol_route.yaml

Why passive: on day one the Go2 motion interface (Unitree sport API vs a
velocity topic) is unknown and differs from Nav2. Counting loops from odom
removes that guess entirely, and teleop is the safest way to make the first
recordings.

The event topic is neutral (`/auditor/pass_complete`), not simulator-specific.
"""
from __future__ import annotations

import json
import math
import time

import rclpy
import yaml
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String
from tf2_ros import Buffer, TransformListener

from .patrol_loop import LoopTracker, Route, route_from_dict
from .topics import PASS_TOPIC


def _yaw(q) -> float:
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                      1.0 - 2.0 * (q.y * q.y + q.z * q.z))


def _apply(tf, x: float, y: float, yaw: float) -> tuple[float, float, float]:
    """Apply a planar map->odom transform to an odom pose."""
    t = tf.transform.translation
    r = _yaw(tf.transform.rotation)
    return (t.x + math.cos(r) * x - math.sin(r) * y,
            t.y + math.sin(r) * x + math.cos(r) * y,
            math.atan2(math.sin(yaw + r), math.cos(yaw + r)))


def _number(data: dict, key: str, default: float) -> float:
    try:
        return float(data.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"route file: {key} must be a number, got {data.get(key)!r}") from exc


def _positive_int(data: dict, key: str, default: int) -> int:
    value = _number(data, key, default)
    if value < 1 or int(value) != value:
        raise ValueError(f"route file: {key} must be a positive integer, got {value!r}")
    return int(value)


def _optional_point(data: dict, key: str) -> tuple[float, float] | None:
    raw = data.get(key)
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise ValueError(f"route file: {key} must be [x, y], got {raw!r}")
    try:
        return float(raw[0]), float(raw[1])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"route file: {key} must be numeric, got {raw!r}") from exc


class PatrolCoordinator(Node):
    def __init__(self):
        super().__init__("patrol_coordinator")
        defaults = {
            "route_file": "",
            "odom_topic": "/odom",
            "pass_topic": PASS_TOPIC,
            "map_frame": "map",
            "area": "",
            "patrol_name": "",
            "n_passes": 0,              # 0 = take from the route file
            "loop_radius": 0.0,         # 0 = take from the route file
            "min_loop_distance": 0.0,   # 0 = 60% of the route length
            "pose_sigma": 0.0,          # 0 = take from the route file
            "start_x": math.nan,
            "start_y": math.nan,
        }
        for key, value in defaults.items():
            self.declare_parameter(key, value)
        p = {key: self.get_parameter(key).value for key in defaults}

        route_path = str(p["route_file"])
        if not route_path:
            raise ValueError("parameter 'route_file' must point to a YAML route")
        self.route, data = self._load_route(route_path)
        self.route_name = route_path.rsplit("/", 1)[-1]

        self.area = str(p["area"]) or str(data.get("area") or "lab-main")
        self.patrol_name = str(p["patrol_name"]) or str(data.get("patrol") or "patrol")
        self.n_passes = (int(p["n_passes"]) if int(p["n_passes"]) > 0
                         else _positive_int(data, "n_passes", 2))
        self.loop_radius = (float(p["loop_radius"]) if float(p["loop_radius"]) > 0
                            else _number(data, "loop_radius", 1.0))
        self.pose_sigma = (float(p["pose_sigma"]) if float(p["pose_sigma"]) > 0
                           else _number(data, "pose_sigma", 0.15))
        if self.loop_radius <= 0 or self.pose_sigma < 0:
            raise ValueError("loop_radius must be > 0 and pose_sigma >= 0")

        min_distance = float(p["min_loop_distance"])
        if min_distance <= 0:
            min_distance = _number(data, "min_loop_distance", 0.0)
        if min_distance <= 0:
            min_distance = 0.6 * self.route.length
        if min_distance > 1.5 * self.route.length:
            self.get_logger().warning(
                f"min_loop_distance={min_distance:.1f} m is longer than "
                f"1.5x the {self.route.length:.1f} m route; a pass may never trigger"
            )

        start: tuple[float, float] | None = None
        sx, sy = float(p["start_x"]), float(p["start_y"])
        if math.isfinite(sx) and math.isfinite(sy):
            start = (sx, sy)
        else:
            start = _optional_point(data, "start")

        self.tracker = LoopTracker(min_distance, self.loop_radius,
                                   n_passes=self.n_passes, start=start)
        self.map_frame = str(p["map_frame"])
        self.pass_topic = str(p["pass_topic"])
        self._warned_no_map = False

        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.pub = self.create_publisher(String, self.pass_topic, 20)
        qos = QoSProfile(depth=20, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.create_subscription(Odometry, str(p["odom_topic"]), self._on_odom, qos)

        self.get_logger().info(
            f"passive coordinator: area={self.area} patrol={self.patrol_name} "
            f"route={self.route_name} ({self.route.length:.1f} m) "
            f"passes={self.n_passes} loop_radius={self.loop_radius:.2f} m "
            f"min_distance={min_distance:.1f} m; it will NOT command motion"
        )
        if start is None:
            self.get_logger().info("start pose will be learned from the first odometry message")
        else:
            self.get_logger().info(f"start pose fixed at ({start[0]:.2f}, {start[1]:.2f})")

    @staticmethod
    def _load_route(path: str) -> tuple[Route, dict]:
        try:
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except OSError as exc:
            raise ValueError(f"cannot read route file {path}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid YAML in {path}: {exc}") from exc
        return route_from_dict(data), data

    def _pose_in_map(self, x: float, y: float, yaw: float,
                     odom_frame: str) -> tuple[float, float, float, str]:
        try:
            tf = self.tf_buffer.lookup_transform(self.map_frame, odom_frame,
                                                 rclpy.time.Time())
        except Exception:  # no TF yet, or no map frame on this robot
            if not self._warned_no_map:
                self.get_logger().warning(
                    f"no {self.map_frame}->{odom_frame} transform; pass pose stays "
                    f"in the odometry frame"
                )
                self._warned_no_map = True
            return x, y, yaw, odom_frame
        mx, my, myaw = _apply(tf, x, y, yaw)
        return mx, my, myaw, self.map_frame

    def _on_odom(self, msg: Odometry) -> None:
        pos = msg.pose.pose.position
        event = self.tracker.update(pos.x, pos.y)
        if event is None:
            return
        yaw = _yaw(msg.pose.pose.orientation)
        odom_frame = msg.header.frame_id or "odom"
        x, y, yaw, frame = self._pose_in_map(pos.x, pos.y, yaw, odom_frame)
        payload = {
            "pass_idx": event.pass_idx,
            "n_passes": event.n_passes,
            "last": event.last,
            "pose": {"x": round(x, 3), "y": round(y, 3), "theta": round(yaw, 5),
                     "sigma": self.pose_sigma, "frame": frame},
            "source": "patrol_coordinator",
            "route": self.route_name,
            "captured_at": time.time(),
        }
        out = String()
        out.data = json.dumps(payload)
        self.pub.publish(out)
        self.get_logger().info(
            f"loop complete ({event.distance:.1f} m since last pass): "
            f"pass {event.pass_idx + 1}/{event.n_passes}"
            + (" (last)" if event.last else "")
        )
        if self.tracker.finished:
            self.get_logger().info(
                f"patrol complete: {self.n_passes} pass(es); no further passes "
                f"(coordinator stays up)"
            )


def main(args=None):
    rclpy.init(args=args)
    node = PatrolCoordinator()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
