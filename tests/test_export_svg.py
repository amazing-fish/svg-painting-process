"""Boundary and vector-only checks for the static SVG frame exporter."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from export_svg import export_svg, partial_ribbon_d, render_svg

NS = {"s": "http://www.w3.org/2000/svg"}


def fixture() -> dict:
    first = {
        "start": 0, "duration": 100, "pauseAfter": 50, "color": "#123456",
        "points": [[0, 10], [50, 10], [100, 10]],
        "left": [[0, 5], [50, 5], [100, 0]],
        "right": [[0, 15], [50, 15], [100, 20]],
        "d": "M0,5 L50,5 L100,0 L100,20 L50,15 L0,15 Z",
    }
    second = copy.deepcopy(first)
    second.update(start=150, color="#654321")
    return {"width": 120, "height": 30, "strokes": [first, second], "totalDuration": 300}


def paths(time_ms=None):
    return ET.fromstring(render_svg(fixture(), time_ms)).findall(".//s:path", NS)


class ExportSvgTests(unittest.TestCase):
    def test_public_export_function_writes_requested_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "half.svg"
            self.assertEqual(export_svg(fixture(), output, 50), output)
            self.assertEqual(output.read_text(encoding="utf-8"), render_svg(fixture(), 50))

    def test_default_final_frame_is_valid_vector_xml(self):
        svg = render_svg(fixture())
        root = ET.fromstring(svg)
        self.assertEqual(root.tag, "{" + NS["s"] + "}svg")
        self.assertEqual(root.attrib["viewBox"], "0 0 120 30")
        self.assertEqual(root.find("s:rect", NS).attrib["fill"], "#ffffff")
        self.assertEqual(len(root.findall(".//s:path", NS)), 2)
        self.assertEqual(paths()[0].attrib["d"], fixture()["strokes"][0]["d"])
        for forbidden in ("image", "clipPath", "mask", "animate", "script"):
            self.assertEqual(root.findall(".//s:" + forbidden, NS), [])
        self.assertNotIn("data:image", svg)

    def test_zero_and_negative_time_are_blank_paper(self):
        self.assertEqual(paths(0), [])
        self.assertEqual(paths(-100), [])

    def test_half_stroke_contains_no_far_endpoint(self):
        half = paths(50)
        self.assertEqual(len(half), 1)
        self.assertEqual(half[0].attrib["data-state"], "drawing")
        self.assertEqual(half[0].attrib["d"], "M0,5 L50,5 L50,15 L0,15 Z")
        self.assertNotIn("100,", half[0].attrib["d"])

    def test_pause_and_exact_start_keep_completed_prefix(self):
        for time in (100, 125, 150):
            with self.subTest(time=time):
                selected = paths(time)
                self.assertEqual(len(selected), 1)
                self.assertEqual(selected[0].attrib["data-state"], "completed")

    def test_only_one_active_stroke_follows_completed_prefix(self):
        selected = paths(175)
        self.assertEqual([p.attrib["data-state"] for p in selected], ["completed", "drawing"])
        self.assertEqual([p.attrib["data-index"] for p in selected], ["0", "1"])
        self.assertEqual(len(paths(250)), 2)
        self.assertEqual(len(paths(10000)), 2)

    def test_overlap_is_rejected_instead_of_showing_parallel_strokes(self):
        data = fixture()
        data["strokes"][1]["start"] = 99
        with self.assertRaisesRegex(ValueError, "non-overlapping"):
            render_svg(data, 99.5)

    def test_nonfinite_time_and_dimensions_are_rejected(self):
        with self.assertRaises(ValueError):
            render_svg(fixture(), float("nan"))
        data = fixture()
        data["width"] = 0
        with self.assertRaises(ValueError):
            render_svg(data)

    def test_stationary_dab_uses_ordered_boundary_samples(self):
        stroke = fixture()["strokes"][0]
        stroke["points"] = [[0, 0], [0, 0], [0, 0]]
        self.assertEqual(partial_ribbon_d(stroke, 0.5), "M0,5 L50,5 L50,15 L0,15 Z")

    def test_supplied_lengths_follow_player_and_bad_lengths_fall_back(self):
        stroke = fixture()["strokes"][0]
        stroke["lengths"] = [10, 20, 50]
        self.assertIn("66.666666666666", partial_ribbon_d(stroke, 0.5))
        stroke["lengths"] = [0, -1, 0]
        self.assertEqual(partial_ribbon_d(stroke, 0.5), "M0,5 L50,5 L50,15 L0,15 Z")


if __name__ == "__main__":
    unittest.main()
