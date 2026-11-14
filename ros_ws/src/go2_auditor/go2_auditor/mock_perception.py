"""Publishes fake detection passes, standing in for the real detector.

Each message (std_msgs/String, JSON) is one observation pass:
  {"area", "patrol", "pass_idx", "n_passes", "last", "detections": [{"cls","x","y","conf"}, ...]}
Positions are map-frame metres. The real detector node will publish the same schema.
"""
import json
import random
import time
import uuid

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from . import mock_world as world
from .topics import DETECTIONS_TOPIC, detections_qos
from .contract import CONTRACT_VERSION


class MockPerception(Node):
    def __init__(self):
        super().__init__("mock_perception")
        defaults = {"area": "mock-lab", "patrol_name": "patrol", "scenario": "baseline",
                    "lab_seed": 7, "change_seed": 1, "obs_seed": -1, "n_passes": 3,
                    "period_s": 1.0, "pose_sigma": 0.10}
        for k, v in defaults.items():
            self.declare_parameter(k, v)
        p = {k: self.get_parameter(k).value for k in defaults}

        if p["scenario"] not in ("baseline", "changed"):
            raise ValueError("scenario must be 'baseline' or 'changed'")
        truth = world.make_lab(seed=p["lab_seed"])
        if p["scenario"] == "changed":
            truth, planted = world.plant_changes(truth, seed=p["change_seed"])
            self.get_logger().info(f"planted changes -> moved {planted['moved']}, "
                                   f"removed {planted['removed']}, added {planted['added']}")
        self.truth = truth
        self.p = p
        # Stable for the lifetime of the node: retries and duplicate delivery reuse it,
        # so the backend can de-duplicate instead of creating a second patrol.
        self.patrol_uid = f"{p['area']}:{p['patrol_name']}:{uuid.uuid4().hex}"
        self.map_version = f"mock-{p['lab_seed']}"
        seed = p["obs_seed"] if p["obs_seed"] >= 0 else random.randrange(2 ** 31)
        self.rng = random.Random(seed)
        self.pub = self.create_publisher(String, DETECTIONS_TOPIC, detections_qos())
        self.i = 0
        self.timer = self.create_timer(float(p["period_s"]), self._tick)
        self.get_logger().info(f"scenario={p['scenario']} patrol='{p['patrol_name']}' area='{p['area']}': "
                               f"publishing {p['n_passes']} passes on {DETECTIONS_TOPIC}")

    def _tick(self):
        n = int(self.p["n_passes"])
        dets = world.observe(self.truth, self.rng, pose_sigma=float(self.p["pose_sigma"]))
        msg = String()
        msg.data = json.dumps({"area": self.p["area"], "patrol": self.p["patrol_name"],
                               "patrol_uid": self.patrol_uid,
                               "pass_uid": f"{self.patrol_uid}:{self.i}",
                               "schema_version": CONTRACT_VERSION,
                               "map_version": self.map_version,
                               "captured_at": time.time(),
                               "pass_idx": self.i, "n_passes": n, "last": self.i == n - 1,
                               "detections": dets})
        self.pub.publish(msg)
        self.get_logger().info(f"published pass {self.i + 1}/{n} ({len(dets)} detections)")
        self.i += 1
        if self.i >= n:
            self.timer.cancel()
            self.get_logger().info("all passes published (node stays up so late subscribers can still receive them)")


def main(args=None):
    rclpy.init(args=args)
    node = MockPerception()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
