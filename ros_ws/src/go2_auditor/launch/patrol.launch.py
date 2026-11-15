"""Real-robot patrol: passive coordinator + uploader.

    ros2 launch go2_auditor patrol.launch.py \
        route_file:=/ws/src/go2_auditor/config/patrol_route.yaml

The coordinator only *counts* loops from odometry; it never commands motion.
Drive with teleop, Nav2, or the Unitree app. Each completed loop publishes one
pass event on /auditor/pass_complete; a detector turns that into a schema-v2
pass. The uploader is started here so the chain is ready once the detector is.

Set auto_compare:=false for shadow mode (store passes, do not compare).
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration as LC
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def _launch_setup(context, *args, **kwargs):
    def s(k):
        return ParameterValue(LC(k), value_type=str)

    def i(k):
        return ParameterValue(LC(k), value_type=int)

    def f(k):
        return ParameterValue(LC(k), value_type=float)

    route_file = LC("route_file").perform(context)
    if not route_file:
        route_file = os.path.join(get_package_share_directory("go2_auditor"),
                                  "config", "patrol_route.yaml")

    coordinator = Node(
        package="go2_auditor", executable="patrol_coordinator", output="screen",
        parameters=[{
            "route_file": route_file,
            "odom_topic": s("odom_topic"),
            "map_frame": s("map_frame"),
            "area": s("area"),
            "patrol_name": s("name"),
            "n_passes": i("n_passes"),
            "loop_radius": f("loop_radius"),
            "min_loop_distance": f("min_loop_distance"),
            "pose_sigma": f("pose_sigma"),
        }],
    )
    uploader = Node(
        package="go2_auditor", executable="patrol_uploader", output="screen",
        parameters=[{"api_url": s("api_url"),
                     "auto_compare": ParameterValue(LC("auto_compare"),
                                                    value_type=bool)}],
    )
    return [coordinator, uploader]


def generate_launch_description():
    args = {
        "route_file": "",
        "api_url": "http://localhost:8000",
        "odom_topic": "/odom",
        "map_frame": "map",
        "area": "",
        "name": "",
        "n_passes": "0",
        "loop_radius": "0.0",
        "min_loop_distance": "0.0",
        "pose_sigma": "0.0",
        "auto_compare": "true",
    }
    declared = [DeclareLaunchArgument(k, default_value=v) for k, v in args.items()]
    return LaunchDescription(declared + [OpaqueFunction(function=_launch_setup)])
