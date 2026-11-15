"""Turn simulated loop completions into schema-v2 detection passes.

This node knows the ground-truth world, so it "cheats" perception. That is
deliberate for Level 2: it isolates the ROS graph, TF/map frame, patrol/pass
orchestration, uploader and backend from the vision model.
"""
from __future__ import annotations

import json
import random
import time
import uuid

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .contract import CONTRACT_VERSION
from .mock_world import observe
from .sim_world import make_world
from .topics import DETECTIONS_TOPIC, detections_qos


class GroundTruthDetector(Node):
    def __init__(self):
        super().__init__("ground_truth_detector")
        defaults = {
            "area": "sim-lab",
            "patrol_name": "sim-patrol",
            "scenario": "baseline",
            "lab_seed": 7,
            "change_seed": 1,
            "n_passes": 2,
            "pose_sigma": 0.05,
            "miss_rate": 0.03,
            "fp_rate": 0.02,
            "embedding_noise": 0.02,
            "obs_seed": -1,
            "map_version": "",
        }
        for key, value in defaults.items():
            self.declare_parameter(key, value)
        p = {key: self.get_parameter(key).value for key in defaults}

        self.area = str(p["area"])
        self.patrol_name = str(p["patrol_name"])
        self.scenario = str(p["scenario"])
        self.n_passes = max(1, int(p["n_passes"]))
        self.pose_sigma = float(p["pose_sigma"])
        self.miss_rate = float(p["miss_rate"])
        self.fp_rate = float(p["fp_rate"])
        self.embedding_noise = float(p["embedding_noise"])
        self.map_version = str(p["map_version"]) or f"sim-{int(p['lab_seed'])}"

        seed = int(p["obs_seed"])
        if seed < 0:
            seed = random.randrange(2 ** 31)
        self.rng = random.Random(seed)

        self.world = make_world(seed=int(p["lab_seed"]), scenario=self.scenario,
                                change_seed=int(p["change_seed"]))
        self.patrol_uid = f"{self.area}:{self.patrol_name}:{uuid.uuid4().hex}"

        self.pub = self.create_publisher(String, DETECTIONS_TOPIC, detections_qos())
        self.create_subscription(String, "/sim/pass_complete", self._on_pass, 20)
        self.get_logger().info(
            f"ground truth: scenario={self.scenario} objects={len(self.world.truth)} "
            f"area={self.area} patrol={self.patrol_name} map_version={self.map_version}"
        )

    def _on_pass(self, msg: String) -> None:
        try:
            event = json.loads(msg.data)
            idx = int(event["pass_idx"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            self.get_logger().error(f"bad pass event: {exc}")
            return
        if idx < 0 or idx >= self.n_passes:
            return

        dets = observe(self.world.truth, self.rng,
                       pose_sigma=self.pose_sigma,
                       miss_rate=self.miss_rate,
                       fp_rate=self.fp_rate,
                       embedding_noise=self.embedding_noise)
        payload = {
            "area": self.area,
            "patrol": self.patrol_name,
            "patrol_uid": self.patrol_uid,
            "pass_uid": f"{self.patrol_uid}:{idx}",
            "schema_version": CONTRACT_VERSION,
            "map_version": self.map_version,
            "captured_at": time.time(),
            "pass_idx": idx,
            "n_passes": self.n_passes,
            "last": idx == self.n_passes - 1,
            "pose": event.get("pose"),
            "detections": dets,
            "patrol_meta": {"sim": True, "scenario": self.scenario,
                            "source": "ground_truth_detector"},
            "pass_meta": {"sim_time": event.get("sim_time")},
        }
        out = String()
        out.data = json.dumps(payload)
        self.pub.publish(out)
        self.get_logger().info(
            f"published pass {idx + 1}/{self.n_passes} ({len(dets)} detections)"
        )


def main(args=None):
    rclpy.init(args=args)
    node = GroundTruthDetector()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
