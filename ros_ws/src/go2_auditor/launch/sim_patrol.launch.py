"""Run the no-Gazebo simulated patrol.

  ros2 launch go2_auditor sim_patrol.launch.py scenario:=baseline name:=monday
  ros2 launch go2_auditor sim_patrol.launch.py scenario:=changed  name:=tuesday

Optional real SLAM on the simulated /scan:

  ros2 launch go2_auditor sim_patrol.launch.py scenario:=baseline use_slam:=true

The backend must be running (default http://localhost:8000). Set
auto_compare:=false for shadow mode.
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction
from launch.launch_description_sources import PythonLaunchDescriptionSource
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

    use_slam = LC("use_slam").perform(context).lower() in ("1", "true", "yes")
    auto_compare = LC("auto_compare").perform(context).lower() in ("1", "true", "yes")

    sim = Node(
        package="go2_auditor", executable="sim_robot", output="screen",
        parameters=[{
            "area": s("area"), "patrol_name": s("name"), "scenario": s("scenario"),
            "lab_seed": i("lab_seed"), "change_seed": i("change_seed"),
            "n_passes": i("n_passes"), "speed": f("speed"),
            "scan_rate": f("scan_rate"), "pose_sigma": f("pose_sigma"),
            "map_version": s("map_version"),
            "publish_map_odom": not use_slam,
        }],
    )
    detector = Node(
        package="go2_auditor", executable="ground_truth_detector", output="screen",
        parameters=[{
            "area": s("area"), "patrol_name": s("name"), "scenario": s("scenario"),
            "lab_seed": i("lab_seed"), "change_seed": i("change_seed"),
            "n_passes": i("n_passes"), "pose_sigma": f("pose_sigma"),
            "miss_rate": f("miss_rate"), "fp_rate": f("fp_rate"),
            "embedding_noise": f("embedding_noise"), "obs_seed": i("obs_seed"),
            "map_version": s("map_version"),
        }],
    )
    uploader = Node(
        package="go2_auditor", executable="patrol_uploader", output="screen",
        parameters=[{"api_url": s("api_url"), "auto_compare": auto_compare}],
    )

    actions = [sim, detector, uploader]
    if use_slam:
        slam_params = os.path.join(get_package_share_directory("go2_auditor"),
                                   "config", "slam_params.yaml")
        actions.insert(0, IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(
                get_package_share_directory("slam_toolbox"), "launch",
                "online_async_launch.py")),
            launch_arguments={"slam_params_file": slam_params,
                              "use_sim_time": "false"}.items(),
        ))
    return actions


def generate_launch_description():
    args = {
        "scenario": "baseline",
        "name": "sim-patrol",
        "area": "sim-lab",
        "api_url": "http://localhost:8000",
        "lab_seed": "7",
        "change_seed": "1",
        "n_passes": "2",
        "speed": "1.5",
        "scan_rate": "10.0",
        "pose_sigma": "0.05",
        "miss_rate": "0.03",
        "fp_rate": "0.02",
        "embedding_noise": "0.02",
        "obs_seed": "-1",
        "map_version": "",
        "use_slam": "false",
        "auto_compare": "true",
    }
    declared = [DeclareLaunchArgument(k, default_value=v) for k, v in args.items()]
    return LaunchDescription(declared + [OpaqueFunction(function=_launch_setup)])
