import os
from glob import glob

from setuptools import setup

package_name = "go2_auditor"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.py")),
        (os.path.join("share", package_name, "config"), glob("config/*.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="maintainer",
    maintainer_email="you@example.com",
    description="Mock perception and patrol uploader for the Go2 site auditor",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "mock_perception = go2_auditor.mock_perception:main",
            "patrol_uploader = go2_auditor.patrol_uploader:main",
            "replay_node = go2_auditor.replay_node:main",
            "sim_robot = go2_auditor.sim_robot:main",
            "ground_truth_detector = go2_auditor.ground_truth_detector:main",
        ],
    },
)
