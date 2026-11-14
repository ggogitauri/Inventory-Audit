"""Replay recorded detection passes through the real uploader path.

  # JSONL fixture
  ros2 launch go2_auditor replay.launch.py messages:=bags/monday.jsonl

  # rosbag2 recording (recorded with: ros2 bag record -o bags/monday /auditor/detections)
  ros2 launch go2_auditor replay.launch.py bag_path:=bags/monday

The uploader accepts either source and keeps the normal queue/retry behaviour.
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction
from launch.substitutions import LaunchConfiguration as LC
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

from go2_auditor.topics import DETECTIONS_TOPIC


def _launch_setup(context, *args, **kwargs):
    bag_path = LC("bag_path").perform(context)
    messages = LC("messages").perform(context)
    period_s = ParameterValue(LC("period_s"), value_type=float)
    loop = ParameterValue(LC("loop"), value_type=bool)

    actions = [Node(package="go2_auditor", executable="patrol_uploader", output="screen",
                    parameters=[{"api_url": ParameterValue(LC("api_url"), value_type=str)}])]
    if bag_path:
        actions.insert(0, ExecuteProcess(
            cmd=["ros2", "bag", "play", bag_path, "--topics", DETECTIONS_TOPIC],
            output="screen"))
    elif messages:
        actions.insert(0, Node(package="go2_auditor", executable="replay_node", output="screen",
                               parameters=[{"messages": ParameterValue(messages, value_type=str),
                                            "period_s": period_s, "loop": loop}]))
    else:
        raise RuntimeError("set either messages:=<file.jsonl> or bag_path:=<rosbag dir>")
    return actions


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("messages", default_value=""),
        DeclareLaunchArgument("bag_path", default_value=""),
        DeclareLaunchArgument("api_url", default_value="http://localhost:8000"),
        DeclareLaunchArgument("period_s", default_value="1.0"),
        DeclareLaunchArgument("loop", default_value="false"),
        OpaqueFunction(function=_launch_setup),
    ])
