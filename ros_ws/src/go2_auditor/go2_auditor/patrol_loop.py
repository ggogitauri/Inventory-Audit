"""Route parsing and passive loop detection for the real-robot patrol.

Pure Python (no ROS) so the loop logic can be tested without a robot.

The coordinator is *passive*: it never commands motion. It watches odometry and
counts a pass each time the robot returns near the start of the route having
travelled at least `min_loop_distance`. That way teleop, Nav2, or the Unitree
app can drive, and pass counting still works.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    """A closed waypoint loop in metres, in the map frame."""

    waypoints: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if len(self.waypoints) < 2:
            raise ValueError("route needs at least two waypoints")
        for x, y in self.waypoints:
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError(f"route waypoint is not finite: ({x}, {y})")

    @property
    def start(self) -> tuple[float, float]:
        return self.waypoints[0]

    @property
    def length(self) -> float:
        total = 0.0
        for (x1, y1), (x2, y2) in zip(self.waypoints, self.waypoints[1:]):
            total += math.hypot(x2 - x1, y2 - y1)
        return total


def route_from_dict(data: dict) -> Route:
    """Build a Route from parsed YAML, with a readable error for bad files."""
    if not isinstance(data, dict):
        raise ValueError("route file must contain a YAML mapping")
    raw = data.get("waypoints")
    if not isinstance(raw, list):
        raise ValueError("route file needs a 'waypoints' list of [x, y] pairs")
    points: list[tuple[float, float]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError(f"waypoint {i} must be [x, y], got {item!r}")
        try:
            points.append((float(item[0]), float(item[1])))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"waypoint {i} is not numeric: {item!r}") from exc
    if points and points[0] != points[-1]:
        points.append(points[0])
    return Route(tuple(points))


@dataclass(frozen=True)
class LoopEvent:
    """One completed loop, ready to be turned into a patrol pass."""

    pass_idx: int
    n_passes: int
    last: bool
    distance: float = 0.0          # metres travelled since the previous pass


class LoopTracker:
    """Count loops from a stream of odometry positions.

    Start is either given (route start) or learned from the first position. A
    loop closes when the robot is within `loop_radius` of the start after at
    least `min_loop_distance` metres of travel. After an event the distance
    counter resets, so lingering by the start cannot double-count.
    """

    def __init__(self, min_loop_distance: float, loop_radius: float,
                 n_passes: int = 1, start: tuple[float, float] | None = None):
        if not math.isfinite(min_loop_distance) or min_loop_distance <= 0:
            raise ValueError("min_loop_distance must be a positive number of metres")
        if not math.isfinite(loop_radius) or loop_radius <= 0:
            raise ValueError("loop_radius must be a positive number of metres")
        if n_passes < 1:
            raise ValueError("n_passes must be at least 1")
        self.min_loop_distance = float(min_loop_distance)
        self.loop_radius = float(loop_radius)
        self.n_passes = int(n_passes)
        self.start = start
        self.last: tuple[float, float] | None = None
        self.traveled = 0.0
        self.loops_done = 0

    @property
    def finished(self) -> bool:
        return self.loops_done >= self.n_passes

    @property
    def distance_to_start(self) -> float | None:
        if self.start is None or self.last is None:
            return None
        return math.hypot(self.last[0] - self.start[0], self.last[1] - self.start[1])

    def update(self, x: float, y: float) -> LoopEvent | None:
        """Feed one position; return an event when a loop completes."""
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        if self.start is None:
            self.start = (x, y)
        if self.last is not None:
            self.traveled += math.hypot(x - self.last[0], y - self.last[1])
        self.last = (x, y)

        if self.finished:
            return None
        if self.traveled < self.min_loop_distance:
            return None
        if self.distance_to_start is None or self.distance_to_start > self.loop_radius:
            return None

        idx = self.loops_done
        distance = self.traveled
        self.loops_done += 1
        self.traveled = 0.0
        return LoopEvent(pass_idx=idx, n_passes=self.n_passes,
                         last=idx == self.n_passes - 1, distance=distance)
