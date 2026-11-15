"""Kinematic 2D robot + ray-cast LiDAR for the no-Gazebo simulation.

Publishes the same interfaces the real robot stack needs:
  /scan, /odom, /tf, /tf_static, /clock, /auditor/pass_complete

The robot follows a closed waypoint route. Each completed loop emits one
pass event, which ground_truth_detector turns into a schema-v2 detection pass.
"""
from __future__ import annotations

import json
import math
import time

import rclpy
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String
from tf2_ros import StaticTransformBroadcaster, TransformBroadcaster

from .sim_world import DEFAULT_ROUTE, make_world, normalize_angle, scan
from .topics import PASS_TOPIC


def _yaw_quat(yaw: float) -> tuple[float, float, float, float]:
    return (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))


class SimRobot(Node):
    def __init__(self):
        super().__init__("sim_robot")
        defaults = {
            "area": "sim-lab",
            "patrol_name": "sim-patrol",
            "scenario": "baseline",
            "lab_seed": 7,
            "change_seed": 1,
            "n_passes": 2,
            "speed": 1.5,
            "scan_rate": 10.0,
            "n_beams": 360,
            "pose_sigma": 0.05,
            "map_version": "",
            "publish_map_odom": True,
            "route": ";".join(f"{x:g},{y:g}" for x, y in DEFAULT_ROUTE),
        }
        for key, value in defaults.items():
            self.declare_parameter(key, value)
        p = {key: self.get_parameter(key).value for key in defaults}

        self.world = make_world(seed=int(p["lab_seed"]), scenario=str(p["scenario"]),
                                change_seed=int(p["change_seed"]))
        self.route = self._parse_route(str(p["route"]))
        self.x, self.y = self.route[0]
        self.theta = 0.0
        self.target = 1
        self.completed = 0
        self.stopped = False

        self.area = str(p["area"])
        self.patrol_name = str(p["patrol_name"])
        self.n_passes = max(1, int(p["n_passes"]))
        self.speed = max(0.05, float(p["speed"]))
        self.scan_rate = max(1.0, float(p["scan_rate"]))
        self.n_beams = max(8, int(p["n_beams"]))
        self.pose_sigma = float(p["pose_sigma"])
        self.map_version = str(p["map_version"]) or f"sim-{int(p['lab_seed'])}"
        self.publish_map_odom = bool(p["publish_map_odom"])
        self.sim_time = 0.0
        self._last_time = time.monotonic()
        self._ticks = 0
        self._scan_every = max(1, int(round(20.0 / self.scan_rate)))

        self.scan_pub = self.create_publisher(LaserScan, "/scan", 10)
        self.odom_pub = self.create_publisher(Odometry, "/odom", 10)
        self.clock_pub = self.create_publisher(Clock, "/clock", 10)
        self.pass_pub = self.create_publisher(String, PASS_TOPIC, 20)
        self.tf_broadcaster = TransformBroadcaster(self)
        self.static_broadcaster = StaticTransformBroadcaster(self)

        self._publish_static_laser()
        self.timer = self.create_timer(1.0 / 20.0, self._tick)
        self.get_logger().info(
            f"scenario={p['scenario']} area={self.area} patrol={self.patrol_name}: "
            f"{self.n_passes} loop(s), route={self.route}, map_version={self.map_version}, "
            f"map->odom={'published' if self.publish_map_odom else 'left to SLAM'}"
        )

    @staticmethod
    def _parse_route(raw: str) -> list[tuple[float, float]]:
        route: list[tuple[float, float]] = []
        for pair in raw.split(";"):
            pair = pair.strip()
            if not pair:
                continue
            x_str, y_str = pair.split(",", 1)
            route.append((float(x_str), float(y_str)))
        if len(route) < 2:
            raise ValueError("route needs at least two points")
        if route[0] != route[-1]:
            route.append(route[0])
        if len(route) < 3:
            raise ValueError("route needs at least three points after closing the loop")
        return route

    def _publish_static_laser(self) -> None:
        t = TransformStamped()
        t.header.stamp = self.get_clock().now().to_msg()
        t.header.frame_id = "base_link"
        t.child_frame_id = "laser"
        t.transform.translation.x = 0.20
        t.transform.translation.z = 0.30
        t.transform.rotation.w = 1.0
        self.static_broadcaster.sendTransform(t)

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(max(0.0, now - self._last_time), 0.2)
        self._last_time = now
        self.sim_time += dt

        self._publish_clock()
        if not self.stopped:
            self._move(dt)
        self._publish_odom()
        self._publish_tf()
        if self._ticks % self._scan_every == 0:
            self._publish_scan()
        self._ticks += 1

    def _move(self, dt: float) -> None:
        tx, ty = self.route[self.target]
        dx = tx - self.x
        dy = ty - self.y
        distance = math.hypot(dx, dy)
        heading_error = normalize_angle(math.atan2(dy, dx) - self.theta)

        max_turn = 1.8 * dt
        if abs(heading_error) > max_turn:
            self.theta = normalize_angle(self.theta + math.copysign(max_turn, heading_error))
        else:
            self.theta = normalize_angle(self.theta + heading_error)

        if abs(heading_error) < 0.45:
            travel = self.speed * dt
            if travel >= distance:
                self.x, self.y = tx, ty
                self._advance_waypoint()
            else:
                self.x += math.cos(self.theta) * travel
                self.y += math.sin(self.theta) * travel

    def _advance_waypoint(self) -> None:
        if self.target == len(self.route) - 1:
            self._publish_pass(self.completed)
            self.completed += 1
            if self.completed >= self.n_passes:
                self.stopped = True
                self.get_logger().info(
                    f"patrol complete: {self.completed} pass(es); robot stopped"
                )
                return
            self.target = 1
        else:
            self.target += 1

    def _publish_pass(self, idx: int) -> None:
        msg = String()
        msg.data = json.dumps({
            "pass_idx": idx,
            "n_passes": self.n_passes,
            "pose": {
                "x": round(self.x, 3),
                "y": round(self.y, 3),
                "theta": round(self.theta, 5),
                "sigma": self.pose_sigma,
            },
            "sim_time": round(self.sim_time, 3),
        })
        self.pass_pub.publish(msg)
        self.get_logger().info(f"completed loop {idx + 1}/{self.n_passes}")

    def _publish_clock(self) -> None:
        msg = Clock()
        msg.clock.sec = int(self.sim_time)
        msg.clock.nanosec = int((self.sim_time % 1.0) * 1_000_000_000)
        self.clock_pub.publish(msg)

    def _publish_odom(self) -> None:
        msg = Odometry()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "odom"
        msg.child_frame_id = "base_link"
        msg.pose.pose.position.x = self.x
        msg.pose.pose.position.y = self.y
        msg.pose.pose.orientation.z = math.sin(self.theta / 2.0)
        msg.pose.pose.orientation.w = math.cos(self.theta / 2.0)
        msg.pose.covariance[0] = self.pose_sigma ** 2
        msg.pose.covariance[7] = self.pose_sigma ** 2
        msg.pose.covariance[35] = (2.0 * self.pose_sigma) ** 2
        self.odom_pub.publish(msg)

    def _publish_tf(self) -> None:
        stamp = self.get_clock().now().to_msg()
        transforms = []
        if self.publish_map_odom:
            t = TransformStamped()
            t.header.stamp = stamp
            t.header.frame_id = "map"
            t.child_frame_id = "odom"
            t.transform.rotation.w = 1.0
            transforms.append(t)
        t = TransformStamped()
        t.header.stamp = stamp
        t.header.frame_id = "odom"
        t.child_frame_id = "base_link"
        t.transform.translation.x = self.x
        t.transform.translation.y = self.y
        t.transform.rotation.z = math.sin(self.theta / 2.0)
        t.transform.rotation.w = math.cos(self.theta / 2.0)
        transforms.append(t)
        self.tf_broadcaster.sendTransform(transforms)

    def _publish_scan(self) -> None:
        msg = LaserScan()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "laser"
        msg.angle_min = -math.pi
        msg.angle_max = math.pi
        msg.angle_increment = 2.0 * math.pi / self.n_beams
        msg.time_increment = 0.0
        msg.scan_time = 1.0 / self.scan_rate
        msg.range_min = 0.05
        msg.range_max = 8.0
        msg.ranges = scan(self.world, self.x, self.y, self.theta,
                          n_beams=self.n_beams, max_range=msg.range_max)
        self.scan_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = SimRobot()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
