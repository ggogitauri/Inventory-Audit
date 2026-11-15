"""Tests for route parsing and passive loop counting (no ROS needed)."""
import math
import unittest

from go2_auditor.patrol_loop import LoopTracker, Route, route_from_dict


def square() -> list[tuple[float, float]]:
    return [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0), (0.0, 0.0)]


class RouteTest(unittest.TestCase):
    def test_closed_route_length(self):
        route = route_from_dict({"waypoints": [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]})
        self.assertAlmostEqual(route.length, 16.0)
        self.assertEqual(route.start, (0.0, 0.0))

    def test_open_route_is_closed_automatically(self):
        route = route_from_dict({"waypoints": [[0, 0], [4, 0], [4, 4]]})
        self.assertEqual(route.waypoints[-1], route.waypoints[0])
        self.assertAlmostEqual(route.length, 4.0 + 4.0 + math.hypot(4.0, 4.0))

    def test_rejects_bad_waypoints(self):
        for bad in ({}, {"waypoints": []}, {"waypoints": [[1, 2, 3]]},
                    {"waypoints": [["a", 2]]}, {"waypoints": [[1, float("nan")]]}):
            with self.assertRaises(ValueError):
                route_from_dict(bad)

    def test_route_dataclass_validates(self):
        with self.assertRaises(ValueError):
            Route(((0.0, 0.0),))


class TrackerTest(unittest.TestCase):
    def feed(self, tracker, points):
        events = []
        for x, y in points:
            event = tracker.update(x, y)
            if event is not None:
                events.append(event)
        return events

    def test_no_event_before_minimum_distance(self):
        tracker = LoopTracker(min_loop_distance=10.0, loop_radius=1.0, n_passes=2)
        points = [(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)]   # 16 m, one loop
        events = self.feed(tracker, points)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].pass_idx, 0)
        self.assertFalse(events[0].last)
        self.assertAlmostEqual(events[0].distance, 16.0)

    def test_short_wiggle_near_start_does_not_count(self):
        tracker = LoopTracker(min_loop_distance=5.0, loop_radius=1.0, n_passes=1)
        events = self.feed(tracker, [(0, 0), (0.5, 0), (0, 0.2), (0.1, 0.1)])
        self.assertEqual(events, [])

    def test_second_loop_is_last_and_third_is_ignored(self):
        tracker = LoopTracker(min_loop_distance=10.0, loop_radius=1.0, n_passes=2)
        points = square() + square()[1:] + square()[1:]
        events = self.feed(tracker, points)
        self.assertEqual([e.pass_idx for e in events], [0, 1])
        self.assertEqual(events[-1].last, True)
        self.assertTrue(tracker.finished)

    def test_lingering_after_pass_does_not_double_count(self):
        tracker = LoopTracker(min_loop_distance=10.0, loop_radius=1.0, n_passes=3)
        self.feed(tracker, square())
        events = self.feed(tracker, [(0.1, 0.0), (0.0, 0.1), (0.0, 0.0)])
        self.assertEqual(events, [])

    def test_closure_radius_is_respected(self):
        near = LoopTracker(min_loop_distance=10.0, loop_radius=0.8, n_passes=1)
        events = self.feed(near, [(0, 0), (4, 0), (4, 4), (0, 4), (0.5, 0.0)])
        self.assertEqual(len(events), 1)
        far = LoopTracker(min_loop_distance=10.0, loop_radius=0.3, n_passes=1)
        events = self.feed(far, [(0, 0), (4, 0), (4, 4), (0, 4), (0.5, 0.0)])
        self.assertEqual(events, [])

    def test_explicit_start_is_used(self):
        tracker = LoopTracker(min_loop_distance=5.0, loop_radius=1.0, n_passes=1,
                              start=(2.0, 2.0))
        events = self.feed(tracker, [(2.0, 2.0), (6.0, 2.0), (6.0, 6.0),
                                     (2.0, 6.0), (2.0, 2.0)])
        self.assertEqual(len(events), 1)

    def test_rejects_bad_configuration(self):
        for kwargs in ({"min_loop_distance": 0.0, "loop_radius": 1.0},
                       {"min_loop_distance": 5.0, "loop_radius": 0.0},
                       {"min_loop_distance": 5.0, "loop_radius": 1.0, "n_passes": 0}):
            with self.assertRaises(ValueError):
                LoopTracker(**kwargs)


if __name__ == "__main__":
    unittest.main()
