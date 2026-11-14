"""Subscribes to detection passes and uploads them to the auditor API (with queue + retry)."""
import json

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .api_client import ApiClient
from .topics import DETECTIONS_TOPIC, detections_qos
from .uploader_core import UploaderCore


class _RosLog:
    def __init__(self, logger):
        self._l = logger
    def info(self, m): self._l.info(m)
    def warn(self, m): self._l.warning(m)
    def error(self, m): self._l.error(m)


class PatrolUploader(Node):
    def __init__(self):
        super().__init__("patrol_uploader")
        self.declare_parameter("api_url", "http://localhost:8000")
        self.declare_parameter("auto_compare", True)
        self.declare_parameter("retry_period_s", 2.0)
        url = self.get_parameter("api_url").value
        self.core = UploaderCore(ApiClient(url), auto_compare=bool(self.get_parameter("auto_compare").value),
                                 log=_RosLog(self.get_logger()))
        self.create_subscription(String, DETECTIONS_TOPIC, self._on_msg, detections_qos())
        self.create_timer(float(self.get_parameter("retry_period_s").value), self.core.pump)
        self.get_logger().info(f"uploading to {url}")

    def _on_msg(self, msg: String):
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError as e:
            self.get_logger().error(f"bad JSON on {DETECTIONS_TOPIC}: {e}")
            return
        self.core.handle(data)


def main(args=None):
    rclpy.init(args=args)
    node = PatrolUploader()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
