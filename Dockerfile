FROM osrf/ros:humble-desktop

RUN apt-get update && apt-get install -y \
    ros-humble-navigation2 ros-humble-nav2-bringup \
    ros-humble-robot-localization ros-humble-rosbridge-suite \
    ros-humble-rmw-cyclonedds-cpp ros-humble-foxglove-bridge \
    ros-humble-pcl-ros ros-humble-grid-map \
    python3-pip python3-colcon-common-extensions git \
    && rm -rf /var/lib/apt/lists/*

ENV RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
RUN echo "source /opt/ros/humble/setup.bash" >> /root/.bashrc
WORKDIR /ws
