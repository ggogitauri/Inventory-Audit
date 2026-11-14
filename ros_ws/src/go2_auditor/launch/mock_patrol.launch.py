"""Run the mock detector and the uploader together.

  ros2 launch go2_auditor mock_patrol.launch.py scenario:=baseline name:=monday
  ros2 launch go2_auditor mock_patrol.launch.py scenario:=changed  name:=tuesday

Add record_dir:=bags/monday to also record the detection topic to rosbag2.
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction
from launch.substitutions import LaunchConfiguration as LC
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

from go2_auditor.topics import DETECTIONS_TOPIC


def _launch_setup(context, *args, **kwargs):
    # Typed explicitly: otherwise a patrol named "2026" would be parsed as an integer.
    def s(k): return ParameterValue(LC(k), value_type=str)
    def i(k): return ParameterValue(LC(k), value_type=int)
    def f(k): return ParameterValue(LC(k), value_type=float)

    perception = Node(package="go2_auditor", executable="mock_perception", output="screen",
                      parameters=[{"scenario": s("scenario"), "patrol_name": s("name"),
                                   "area": s("area"), "lab_seed": i("lab_seed"),
                                   "change_seed": i("change_seed"), "n_passes": i("n_passes"),
                                   "period_s": f("period_s")}])
    uploader = Node(package="go2_auditor", executable="patrol_uploader", output="screen",
                    parameters=[{"api_url": s("api_url"),
                                 "auto_compare": ParameterValue(LC("auto_compare"), value_type=bool)}])

    actions = [uploader, perception]
    record_dir = LC("record_dir").perform(context)
    if record_dir:
        actions.insert(0, ExecuteProcess(
            cmd=["ros2", "bag", "record", "-o", record_dir, DETECTIONS_TOPIC],
            output="screen"))
    return actions


def generate_launch_description():
    args = {"scenario": "baseline", "name": "patrol", "area": "mock-lab",
            "api_url": "http://localhost:8000", "lab_seed": "7", "change_seed": "1",
            "n_passes": "3", "period_s": "1.0", "record_dir": "", "auto_compare": "true"}
    declared = [DeclareLaunchArgument(k, default_value=v) for k, v in args.items()]
    return LaunchDescription(declared + [OpaqueFunction(function=_launch_setup)])
