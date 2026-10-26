"""A guide audit must expose reversal, lost movements, scale and order."""
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from compare_guides import compare, load_guides, pair_metrics, sample_curve


class GuideComparisonTest(unittest.TestCase):
    def test_pair_reports_reversal_width_and_direction_separately(self):
        manual = ([[0, 0], [0, 50], [0, 100]], [10, 10, 10])
        reverse = ([[0, 100], [0, 50], [0, 0]], [5, 5, 5])
        result = pair_metrics(manual, reverse, 200.)
        self.assertEqual(result["traversal"], "reversed")
        self.assertEqual(result["manualCoverageWithinManualRadius"], 1.)
        self.assertEqual(result["meanAxisDistancePx"], 0.)
        self.assertLess(result["medianUndirectedAngleDegrees"], .01)
        self.assertLess(result["meanDirectedTangentAgreement"], -.99)
        self.assertEqual(result["widthRatioAutomaticToManual"], .5)

    def test_same_pixels_different_working_scale_normalize_without_hiding_order(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.png"
            Image.new("RGB", (100, 200), "white").save(source)
            sha = hashlib.sha256(source.read_bytes()).hexdigest()
            original = [([[10, 10], [10, 50], [10, 100]], [8, 8, 8]),
                        ([[20, 10], [20, 50], [20, 100]], [8, 8, 8])]
            manual = dict(gestures=original, metadata=dict(sourceSize=[100, 200], sourceSHA256=sha, guideOrder=[1, 0]))
            automatic = dict(gestures=[((np.asarray(p, dtype=float)*.5).tolist(), (np.asarray(r)*.5).tolist())
                                       for p, r in original], metadata=dict(sourceSize=[50, 100]))
            m, a = root / "m.json", root / "a.json"
            m.write_text(json.dumps(manual), encoding="utf-8")
            a.write_text(json.dumps(automatic), encoding="utf-8")
            target = compare(m, a, source, sha, root / "out")
            report = json.loads((target.parent / "guide-comparison.json").read_text(encoding="utf-8"))
            self.assertFalse(report["sameWorkingResolution"])
            self.assertEqual([p["manualIndex"] for p in report["pairs"]], [1, 0])
            self.assertEqual([p["automaticRank"] for p in report["pairs"]], [2, 1])
            self.assertTrue(all(p["meanAxisDistancePx"] == 0 for p in report["pairs"]))
            self.assertFalse(report["manualGuidesUsedByAutomaticGenerator"])
            with self.assertRaisesRegex(ValueError, "Automatic source SHA256"):
                compare(m, a, source, "wrong", root / "bad")

    def test_empty_zero_or_invalid_geometry_is_not_accepted(self):
        for points, radii in [([[0, 0]]*3, [1, 1, 1]),
                              ([[0, 0], [1, 0], [2, float('nan')]], [1, 1, 1]),
                              ([[0, 0], [1, 0], [2, 0]], [1, 0, 1])]:
            with self.assertRaises(ValueError):
                sample_curve(points, radii)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "guides.json"
            target.write_text(json.dumps(dict(gestures=[([[0, 0], [1, 0], [2, 0]], [1, 1, 1])],
                                              metadata=dict(sourceSize=[20, 20], guideOrder=[1]))), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "permutation"):
                load_guides(target, [20, 20])


if __name__ == "__main__":
    unittest.main()
