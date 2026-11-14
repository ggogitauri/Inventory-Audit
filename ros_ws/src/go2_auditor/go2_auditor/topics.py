"""Shared topic name and QoS so publisher and subscriber can never disagree."""
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

DETECTIONS_TOPIC = "/auditor/detections"


def detections_qos() -> QoSProfile:
    # TRANSIENT_LOCAL: a subscriber that starts a moment late still receives recent passes.
    return QoSProfile(depth=20, reliability=ReliabilityPolicy.RELIABLE,
                      durability=DurabilityPolicy.TRANSIENT_LOCAL)
