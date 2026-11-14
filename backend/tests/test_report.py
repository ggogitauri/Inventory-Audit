import random
import re
import xml.etree.ElementTree as ET

from auditor.report import build_map_svg, render_report_html
from auditor.service import run_comparison
from auditor.sim import make_lab, observe, plant_changes
from auditor.store import Store


def make_report(area="lab"):
    s = Store()
    rng = random.Random(3)
    truth = make_lab(seed=3)
    after, _ = plant_changes(truth, rng)
    for name, t in (("monday", truth), ("tuesday", after)):
        pid = s.create_patrol(area, name)
        for _ in range(3):
            s.add_pass(pid, observe(t, rng))
    r = run_comparison(s, 2)
    return s, r


def test_report_html_contains_summary_and_valid_svg():
    s, r = make_report()
    html = render_report_html(r, s.get_patrol(r["baseline_id"]), s.get_patrol(r["current_id"]))
    assert f"Change report #{r['id']}" in html
    assert html.count("<tr>") == len(r["changes"]) + 1          # header + one row per change
    svg = re.search(r"<svg.*?</svg>", html, re.S).group(0)
    ET.fromstring(svg)                                            # well-formed XML
    assert svg.count("<text") == len(r["changes"])                # one label per change


def test_empty_report_renders():
    svg = build_map_svg([])
    ET.fromstring(svg)
    html = render_report_html({"id": 1, "summary": {"moved": 0, "missing": 0, "new": 0, "unchanged": 5}, "changes": []},
                              {"name": "a", "created_at": 0}, {"name": "b", "created_at": 1, "area": "lab"})
    assert "No changes between these patrols" in html


def test_user_text_is_escaped():
    s, r = make_report(area="<script>alert(1)</script>")
    html = render_report_html(r, s.get_patrol(r["baseline_id"]), s.get_patrol(r["current_id"]))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_evidence_images_are_linked():
    changes = [{"type": "missing", "cls": "chair",
                "before": {"x": 1, "y": 1, "conf": 0.9, "image": "crops/chair 1.jpg"},
                "after": None, "distance": None}]
    html = render_report_html(
        {"id": 2, "summary": {"moved": 0, "missing": 1, "new": 0, "unchanged": 0}, "changes": changes},
        {"name": "a", "created_at": 0}, {"name": "b", "created_at": 1, "area": "lab"})
    assert 'src="/media/crops/chair%201.jpg"' in html
    assert 'href="/media/crops/chair%201.jpg"' in html


def test_map_labels_do_not_overlap():
    changes = [
        {"type": "missing", "cls": "cabinet", "before": {"x": 7.9, "y": 5.8, "conf": 1, "image": None}, "after": None, "distance": None},
        {"type": "new", "cls": "cabinet", "before": None, "after": {"x": 7.9, "y": 5.9, "conf": 1, "image": None}, "distance": None},
        {"type": "new", "cls": "desk", "before": None, "after": {"x": 7.95, "y": 5.85, "conf": 1, "image": None}, "distance": None},
    ]
    svg = ET.fromstring(build_map_svg(changes))
    ys = sorted(float(t.get("y")) for t in svg.iter("{http://www.w3.org/2000/svg}text"))
    assert len(ys) == 3 and all(b - a >= 12.9 for a, b in zip(ys, ys[1:]))
