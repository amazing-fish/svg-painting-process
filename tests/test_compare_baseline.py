"""Evidence must retain failed/missing samples and reject invalid comparisons."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from compare_baseline import compare, stroke_time, unresolved_transition


def painting(count):
    strokes = [dict(start=index * 100, duration=80, color="#334455", opacity=1,
                    d="M0,0 L10,0 L10,2 L0,2 Z", points=[[0, 1], [10, 1]],
                    left=[[0, 0], [10, 0]], right=[[0, 2], [10, 2]]) for index in range(count)]
    return dict(width=20, height=20, strokes=strokes, totalDuration=count * 100,
                version="7.1", sourceDataURL="data:image/png;base64,AA==", constructionReports=[])


class ComparisonTest(unittest.TestCase):
    def manifest(self, root, data, *, error=False, status="generated"):
        root.mkdir()
        source = dict(relativePath="person.png", sha256="a" * 64)
        result = dict(source, status="error", error="test failure") if error else dict(source, status=status, output="sample", seconds=1)
        value = dict(version="7.1", inputs=[source], parameters=dict(maxEdge=640, through=2), results=[result])
        if not error:
            (root / "sample" / "qa").mkdir(parents=True)
            (root / "sample" / "qa" / "painting-data.json").write_text(json.dumps(data), encoding="utf-8")
        target = root / "manifest-v7.1.json"
        target.write_text(json.dumps(value), encoding="utf-8")
        return target

    def test_vector_locators_and_shared_original_time(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            before, after = painting(211), painting(140)
            before["constructionReports"] = [dict(guideIndex=0, layer=0, needsRevisit=True, endIndex=20, status="budget_unfinished")]
            first = self.manifest(root / "before", before)
            second = self.manifest(root / "after", after)
            target = compare(first, second, root / "comparison")
            records = json.loads((target.parent / "comparison-evidence.json").read_text())["cases"][0]
            frames = records["runs"]["after"]["frames"]
            self.assertEqual(frames[2]["status"], "unavailable: fewer strokes")
            self.assertEqual(records["clips"][0]["anchorMs"], stroke_time(before, 100))
            self.assertEqual(records["clips"][1]["status"], "unavailable")
            frame = ET.parse(target.parent / "sample-001" / "before" / "stroke-100.svg")
            self.assertEqual(len(frame.findall(".//{http://www.w3.org/2000/svg}path")), 100)
            self.assertNotIn("<image", (target.parent / "sample-001" / "before" / "stroke-100.svg").read_text())
            self.assertIsNone(stroke_time(after, 185))

    def test_reject_changed_input_or_parameters(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = self.manifest(root / "before", painting(1))
            second = self.manifest(root / "after", painting(1))
            changed = json.loads(second.read_text())
            changed["parameters"]["maxEdge"] = 768
            second.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "identical ordered"):
                compare(first, second, root / "comparison")
            changed["parameters"]["maxEdge"] = 640
            changed["inputs"][0]["sha256"] = "b" * 64
            changed["results"][0]["sha256"] = "b" * 64
            second.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "identical ordered"):
                compare(first, second, root / "comparison")

    def test_failure_retained_and_continuation_is_not_a_transfer(self):
        data = painting(4)
        data["constructionReports"] = [dict(guideIndex=0, layer=0, needsRevisit=True, endIndex=1, status="budget_unfinished"),
                                       dict(guideIndex=0, layer=0, needsRevisit=True, endIndex=2, status="no_improvement"),
                                       dict(guideIndex=1, layer=0, needsRevisit=False, endIndex=3, status="readable_proxy")]
        self.assertEqual(unresolved_transition(data)["reportIndex"], 1)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = self.manifest(root / "before", data)
            second = self.manifest(root / "after", data, error=True)
            target = compare(first, second, root / "comparison")
            record = json.loads((target.parent / "comparison-evidence.json").read_text())["cases"][0]
            self.assertEqual(record["runs"]["after"]["status"], "error")
            self.assertTrue(all(clip["status"] == "unavailable" for clip in record["clips"]))

    def test_incomplete_keeps_vectors_and_marks_missing_time_and_blocked_end(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            before, after = painting(211), painting(80)
            after["constructionReports"] = [dict(guideIndex=0, layer=0, needsRevisit=True, endIndex=80, status="budget_unfinished")]
            after["constructionBlocked"] = dict(guideIndex=0, reason="repair_budget_unfinished")
            after["layers"] = [dict(complete=False)]
            first = self.manifest(root / "before", before)
            second = self.manifest(root / "after", after, status="incomplete")
            target = compare(first, second, root / "comparison")
            record = json.loads((target.parent / "comparison-evidence.json").read_text())["cases"][0]
            run = record["runs"]["after"]
            self.assertTrue(run["incomplete"])
            self.assertEqual((run["stages"], run["completedStages"]), (1, 0))
            self.assertEqual(run["unresolvedTransition"]["kind"], "blocked_end")
            self.assertFalse(run["unresolvedTransition"]["hasNextTransfer"])
            self.assertTrue(all(clip["status"] == "unavailable" for clip in record["clips"][:4]))
            self.assertEqual(record["clips"][-1]["status"], "available")
            self.assertTrue(record["clips"][-1]["terminal"])
            self.assertTrue(record["clips"][-1]["trailingContextTruncated"])
            svg = ET.parse(target.parent / "sample-001" / "after" / "blocked-final.svg")
            self.assertEqual(len(svg.findall(".//{http://www.w3.org/2000/svg}path")), 80)
            self.assertFalse((target.parent / "sample-001" / "after" / "transition-after.svg").exists())

    def test_earlier_stalled_transfer_and_later_budget_stop_are_both_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            before, after = painting(211), painting(100)
            after["constructionReports"] = [dict(guideIndex=0, layer=0, needsRevisit=True, endIndex=20, status="no_improvement"),
                                              dict(guideIndex=1, layer=0, needsRevisit=True, endIndex=100, status="budget_unfinished")]
            after["constructionBlocked"] = dict(guideIndex=1, layer=0, reportIndex=1, reason="repair_budget_exhausted_before_coarse_ready")
            after["layers"] = [dict(complete=False)]
            first = self.manifest(root / "before", before)
            second = self.manifest(root / "after", after, status="incomplete")
            target = compare(first, second, root / "comparison")
            record = json.loads((target.parent / "comparison-evidence.json").read_text())["cases"][0]
            run = record["runs"]["after"]
            self.assertEqual(run["unresolvedTransition"]["kind"], "transfer")
            self.assertEqual(run["unresolvedTransition"]["strokeCount"], 20)
            self.assertEqual(run["blockedEnd"]["kind"], "blocked_end")
            self.assertFalse(run["blockedEnd"]["hasNextTransfer"])
            self.assertEqual(run["blockedEnd"]["strokeCount"], 100)
            for name, count in (("transition-before.svg", 20), ("transition-after.svg", 21), ("blocked-final.svg", 100)):
                svg = ET.parse(target.parent / "sample-001" / "after" / name)
                self.assertEqual(len(svg.findall(".//{http://www.w3.org/2000/svg}path")), count)
            stop_clips = [clip for clip in record["clips"] if "预算停止前" in clip["label"]]
            self.assertEqual(len(stop_clips), 1)
            self.assertEqual(stop_clips[0]["status"], "available")
            self.assertTrue(stop_clips[0]["terminal"])


if __name__ == "__main__":
    unittest.main()
