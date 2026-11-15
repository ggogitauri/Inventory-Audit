"""Tests for preflight report/run-sheet rendering and the synthetic pass."""
import math
import unittest

from go2_auditor.contract import CONTRACT_VERSION
from go2_auditor.preflight_core import (Check, build_test_pass, failed,
                                        render_report, render_run_sheet,
                                        scan_stats)


class ScanStatsTest(unittest.TestCase):
    def test_ignores_inf_and_out_of_range(self):
        stats = scan_stats("laser", [0.5, 1.0, float("inf"), 9.0, float("nan"), 2.0],
                           range_min=0.05, range_max=8.0)
        self.assertEqual(stats["beams"], 6)
        self.assertEqual(stats["finite"], 4)
        self.assertEqual(stats["in_range"], 3)
        self.assertAlmostEqual(stats["min"], 0.5)
        self.assertAlmostEqual(stats["max"], 2.0)

    def test_empty_scan(self):
        stats = scan_stats("laser", [], 0.05, 8.0)
        self.assertEqual(stats["in_range"], 0)
        self.assertIsNone(stats["min"])

    def test_all_infinite(self):
        stats = scan_stats("laser", [math.inf, math.inf], 0.05, 8.0)
        self.assertEqual(stats["finite"], 0)
        self.assertIsNone(stats["max"])


class TestPassTest(unittest.TestCase):
    def test_schema_v2_and_no_automatic_compare(self):
        payload = build_test_pass(uid="u1", now=123.0)
        self.assertEqual(payload["schema_version"], CONTRACT_VERSION)
        self.assertEqual(payload["patrol_uid"], "u1")
        self.assertEqual(payload["pass_uid"], "u1:0")
        self.assertFalse(payload["last"])
        self.assertEqual(payload["n_passes"], 2)
        self.assertEqual(len(payload["detections"]), 2)
        for det in payload["detections"]:
            self.assertIn("cls", det)
            self.assertIn("x", det)
            self.assertIn("conf", det)

    def test_uid_is_stable_when_given(self):
        a = build_test_pass(uid="same")
        b = build_test_pass(uid="same")
        self.assertEqual(a["patrol_uid"], b["patrol_uid"])


class RenderTest(unittest.TestCase):
    def facts(self, checks):
        return {
            "generated_at": "2026-11-15 12:00:00",
            "host": "laptop",
            "domain": "0",
            "rmw": "rmw_cyclonedds_cpp",
            "api_url": "http://localhost:8000",
            "map_frame": "map",
            "base_frame": "base_link",
            "checks": checks,
            "topics": {"scan": "/scan", "odom": "/odom"},
            "facts": {"scan topic": "/scan", "odom topic": "/odom"},
            "scan": {"frame_id": "laser", "beams": 360, "finite": 360,
                     "in_range": 340, "min": 0.3, "max": 7.8},
            "odom": {"frame_id": "odom", "child_frame_id": "base_link", "rate_hz": 20.0},
        }

    def test_report_contains_checks_and_verdict(self):
        text = render_report(self.facts([Check("TF chain", "ok", "map -> base_link")]))
        self.assertIn("**PASS**", text)
        self.assertIn("| TF chain | **OK** |", text)
        self.assertIn("map -> base_link", text)

    def test_report_flags_failures_and_hints(self):
        checks = [Check("Backend", "fail", "unreachable", "docker compose up -d api")]
        text = render_report(self.facts(checks))
        self.assertIn("FAIL — fix these before driving", text)
        self.assertIn("**Backend:** docker compose up -d api", text)
        self.assertTrue(failed(checks))
        self.assertFalse(failed([Check("Backend", "ok")]))

    def test_run_sheet_uses_discovered_values(self):
        text = render_run_sheet(self.facts([Check("TF chain", "ok")]))
        self.assertIn("ROS_DOMAIN_ID `0`", text)
        self.assertIn("`/scan`", text)
        self.assertIn("http://localhost:8000", text)
        self.assertIn("/patrols/<patrol-id>/promote", text)
        self.assertNotIn("PREFLIGHT FAILED", text)

    def test_run_sheet_warns_on_failed_preflight(self):
        text = render_run_sheet(self.facts([Check("TF chain", "fail", "no tf")]))
        self.assertIn("PREFLIGHT FAILED", text)
        self.assertIn("TF chain", text)


if __name__ == "__main__":
    unittest.main()
