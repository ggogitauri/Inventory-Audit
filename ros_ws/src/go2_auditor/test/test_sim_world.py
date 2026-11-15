"""Tests for the pure-Python 2D world (no ROS needed)."""
import math
import unittest

from go2_auditor.sim_world import (Circle, DEFAULT_ROUTE, Wall, empty_world,
                                   make_world, normalize_angle, ray_circle,
                                   ray_segment, scan)


class RayTest(unittest.TestCase):
    def test_ray_hits_right_wall(self):
        wall = Wall(12.0, 0.0, 12.0, 8.0)
        self.assertAlmostEqual(ray_segment(6.0, 4.0, 1.0, 0.0, wall), 6.0)

    def test_ray_hits_left_wall(self):
        wall = Wall(0.0, 0.0, 0.0, 8.0)
        self.assertAlmostEqual(ray_segment(6.0, 4.0, -1.0, 0.0, wall), 6.0)

    def test_ray_misses_segment(self):
        wall = Wall(12.0, 0.0, 12.0, 8.0)
        self.assertIsNone(ray_segment(6.0, 4.0, -1.0, 0.0, wall))

    def test_ray_hits_circle(self):
        circle = Circle(8.0, 4.0, 1.0)
        self.assertAlmostEqual(ray_circle(6.0, 4.0, 1.0, 0.0, circle), 1.0)

    def test_origin_inside_circle_is_ignored(self):
        circle = Circle(6.0, 4.0, 1.0)
        self.assertIsNone(ray_circle(6.0, 4.0, 1.0, 0.0, circle))


class ScanTest(unittest.TestCase):
    def test_single_beam_right_wall(self):
        ranges = scan(empty_world(), 6.0, 4.0, math.pi, n_beams=1, max_range=8.0)
        self.assertAlmostEqual(ranges[0], 6.0)

    def test_single_beam_sees_object(self):
        world = empty_world()
        world.circles.append(Circle(8.0, 4.0, 1.0))
        ranges = scan(world, 6.0, 4.0, math.pi, n_beams=1, max_range=8.0)
        self.assertAlmostEqual(ranges[0], 1.0)

    def test_scan_shape_and_bounds(self):
        ranges = scan(empty_world(), 6.0, 4.0, 0.0, n_beams=360, max_range=8.0)
        self.assertEqual(len(ranges), 360)
        self.assertGreaterEqual(min(ranges), 4.0)
        self.assertLessEqual(max(ranges), 8.0)


class WorldTest(unittest.TestCase):
    def test_route_is_closed(self):
        self.assertEqual(DEFAULT_ROUTE[0], DEFAULT_ROUTE[-1])
        self.assertGreaterEqual(len(DEFAULT_ROUTE), 3)

    def test_changed_world_differs(self):
        base = make_world(seed=7, scenario="baseline")
        changed = make_world(seed=7, scenario="changed", change_seed=1)
        base_ids = {(o.id, round(o.x, 3), round(o.y, 3)) for o in base.truth}
        changed_ids = {(o.id, round(o.x, 3), round(o.y, 3)) for o in changed.truth}
        self.assertNotEqual(base_ids, changed_ids)

    def test_normalize_angle(self):
        self.assertAlmostEqual(abs(normalize_angle(3.0 * math.pi)), math.pi)
        self.assertAlmostEqual(abs(normalize_angle(-3.0 * math.pi)), math.pi)


if __name__ == "__main__":
    unittest.main()
