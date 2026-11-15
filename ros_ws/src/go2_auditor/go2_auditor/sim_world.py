"""Deterministic 2D lab and ray-cast LiDAR for the no-Gazebo simulation.

Pure Python (no ROS), so it can be unit-tested on any machine. The world uses
the same object vocabulary as mock_world.py, but adds walls and a physically
plausible 2D scan so the *real* SLAM/TF/patrol stack can run on top of it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .mock_world import Obj, make_lab, plant_changes

SIZE = (12.0, 8.0)
DEFAULT_ROUTE = ((2.0, 2.0), (10.0, 2.0), (10.0, 6.0), (2.0, 6.0), (2.0, 2.0))
OBJECT_RADIUS = 0.30


@dataclass(frozen=True)
class Wall:
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True)
class Circle:
    x: float
    y: float
    radius: float = OBJECT_RADIUS


@dataclass
class World:
    width: float
    height: float
    walls: list[Wall]
    circles: list[Circle]
    truth: list[Obj]


def normalize_angle(a: float) -> float:
    while a > math.pi:
        a -= 2.0 * math.pi
    while a < -math.pi:
        a += 2.0 * math.pi
    return a


def perimeter(width: float = SIZE[0], height: float = SIZE[1]) -> list[Wall]:
    return [
        Wall(0.0, 0.0, width, 0.0),
        Wall(width, 0.0, width, height),
        Wall(width, height, 0.0, height),
        Wall(0.0, height, 0.0, 0.0),
    ]


def empty_world(width: float = SIZE[0], height: float = SIZE[1]) -> World:
    return World(width, height, perimeter(width, height), [], [])


def make_world(seed: int = 7, scenario: str = "baseline", n_objects: int = 40,
               change_seed: int = 1) -> World:
    """Build the baseline or changed lab. Deterministic for a given seed."""
    truth = make_lab(seed=seed, n=n_objects)
    if scenario == "changed":
        truth, _ = plant_changes(truth, seed=change_seed)
    elif scenario != "baseline":
        raise ValueError("scenario must be 'baseline' or 'changed'")
    width, height = SIZE
    circles = [Circle(o.x, o.y) for o in truth]
    return World(width, height, perimeter(width, height), circles, truth)


def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def ray_segment(ox: float, oy: float, dx: float, dy: float, wall: Wall) -> float | None:
    """Distance from (ox,oy) along unit (dx,dy) to a wall segment, or None."""
    sx = wall.x2 - wall.x1
    sy = wall.y2 - wall.y1
    denom = _cross(dx, dy, sx, sy)
    if abs(denom) < 1e-9:
        return None
    qx = wall.x1 - ox
    qy = wall.y1 - oy
    t = _cross(qx, qy, sx, sy) / denom
    u = _cross(qx, qy, dx, dy) / denom
    if t > 0.0 and 0.0 <= u <= 1.0:
        return t
    return None


def ray_circle(ox: float, oy: float, dx: float, dy: float, circle: Circle) -> float | None:
    """Distance from (ox,oy) along unit (dx,dy) to a circle, or None.

    An origin inside the circle returns None: the robot cannot see out of an
    object it is standing in, and returning the exit point would produce a
    misleading zero-ish range.
    """
    fx = ox - circle.x
    fy = oy - circle.y
    c = fx * fx + fy * fy - circle.radius * circle.radius
    if c <= 0.0:
        return None
    b = fx * dx + fy * dy
    disc = b * b - c
    if disc < 0.0:
        return None
    sq = math.sqrt(disc)
    t = -b - sq
    if t <= 0.0:
        t = -b + sq
    return t if t > 0.0 else None


def scan(world: World, x: float, y: float, theta: float, n_beams: int = 360,
         max_range: float = 8.0) -> list[float]:
    """One 360-degree LaserScan, angles relative to the robot's heading."""
    ranges: list[float] = []
    for i in range(n_beams):
        angle = theta - math.pi + (2.0 * math.pi * i / n_beams)
        dx = math.cos(angle)
        dy = math.sin(angle)
        best = max_range
        for wall in world.walls:
            t = ray_segment(x, y, dx, dy, wall)
            if t is not None and 0.02 < t < best:
                best = t
        for circle in world.circles:
            t = ray_circle(x, y, dx, dy, circle)
            if t is not None and 0.02 < t < best:
                best = t
        ranges.append(round(best, 3))
    return ranges
