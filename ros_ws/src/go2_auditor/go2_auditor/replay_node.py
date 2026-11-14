"""Replay a JSONL file of detection passes on /auditor/detections.

This is the no-robot path that exercises the *real* ROS graph (QoS, serialization,
uploader queue) rather than calling the backend in-process:

    ros2 launch go2_auditor replay.launch.py messages:=bags/monday.jsonl

For a rosbag2 recording use `bag_path:=bags/monday` instead (the launch file runs
`ros2 bag play` for you).
"""
import json

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .replay_core import load_messages
from .topics import DETECTIONS_TOPIC, detections_qos


class ReplayNode(Node):
    def __init__(self):
        super().__init__("detection_replay")
        self.declare_parameter("messages", "")
        self.declare_parameter("period_s", 1.0)
        self.declare_parameter("loop", False)
        path = str(self.get_parameter("messages").value)
        if not path:
            raise ValueError("parameter 'messages' must point to a JSONL replay file")
        self.messages = load_messages(path)
        self.loop = bool(self.get_parameter("loop").value)
        self.i = 0
        self.pub = self.create_publisher(String, DETECTIONS_TOPIC, detections_qos())
        self.timer = self.create_timer(float(self.get_parameter("period_s").value), self._tick)
        self.get_logger().info(f"replaying {len(self.messages)} pass message(s) from {path}")

    def _tick(self):
        msg = String()
        msg.data = json.dumps(self.messages[self.i])
        self.pub.publish(msg)
        self.get_logger().info(f"replayed message {self.i + 1}/{len(self.messages)}")
        self.i += 1
        if self.i >= len(self.messages):
            if self.loop:
                self.i = 0
            else:
                self.timer.cancel()
                self.get_logger().info("replay complete (node stays up so late subscribers can receive messages)")


def main(args=None):
    rclpy.init(args=args)
    node = ReplayNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
