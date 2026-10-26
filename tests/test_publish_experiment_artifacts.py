"""Publication preserves frozen painting data and binds complete real source."""
from pathlib import Path
import base64
import copy
import io
import json
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from publish_experiment_artifacts import (COMPARISON_NAME, EXPERIMENT, ZIP_NAME,
    digest, dump, git_bytes, public_copy, publish, source_archive,
    source_zip_validation, validate_publication, write)


class PublishedExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.revision = git_bytes(ROOT, "rev-parse", "HEAD").decode().strip()
        cls.archive, cls.source = source_archive(ROOT, cls.revision)

    def inputs(self, folder):
        root = Path(folder)
        strokes = [dict(start=100 * i if i < 41 else 42855 + (i - 41) * 500,
            duration=100, opacity=.5, color="#334455", layer=0,
            d="M0,0 L10,0 L10,2 L0,2 Z", points=[[0, 1], [10, 1]],
            left=[[0, 0], [10, 0]], right=[[0, 2], [10, 2]]) for i in range(64)]
        data = dict(version="7.2", width=20, height=20, totalDuration=59971,
            layers=[dict(name="partial", complete=False)], strokes=strokes,
            constructionBlocked=dict(guideIndex=16), sourceDataURL="data:image/png;base64,",
            experiment=dict(version=EXPERIMENT, explicitInputs=dict(sourceSHA256="a" * 64,
                source="C:\\Users\\private\\original.png", guides="C:\\Users\\private\\guides.json")))
        replay = root / "raw.json"
        write(replay, dump(data))
        old = copy.deepcopy(data); old["strokes"] = old["strokes"][:41]; old["totalDuration"] = 43645
        v6 = copy.deepcopy(data); v6["strokes"] = [dict(strokes[0], start=i * 100) for i in range(676)]
        v6["version"] = "6.0"; v6["totalDuration"] = 395429
        for tag, previous in (("old41-vs-new64", old), ("true-v6-vs-new64", v6)):
            write(root / tag / "before/qa/painting-data.json", dump(previous))
            case = dict(runs=dict(before=dict(data=previous), after=dict(data=data)))
            write(root / tag / "comparison" / COMPARISON_NAME,
                  '<!doctype html><html><div class="controls"></div><script id="cases" type="application/json">' +
                  json.dumps([case]) + '</script></html>')
        return root, replay, data

    def test_source_archive_is_deterministic_real_module_tree_and_complete_dependency_set(self):
        repeated, _ = source_archive(ROOT, self.revision)
        self.assertEqual(self.archive, repeated)
        self.assertEqual(source_zip_validation(self.archive)["experimentVersion"], EXPERIMENT)
        required = {"build_portrait.py", "construction_episodes.py", "auto_guides.py", "global_plan.py",
                    "tools/export_svg.py", "experiments/portrait_coarse/__main__.py",
                    "experiments/portrait_coarse/runner.py", "experiments/portrait_coarse/contracts.py"}
        with zipfile.ZipFile(io.BytesIO(self.archive)) as archive:
            self.assertTrue(required.issubset(archive.namelist()))
            self.assertFalse(any("pose_backend" in name for name in archive.namelist()))
            for name in self.source["files"]:
                if name != "SOURCE-README.md":
                    self.assertEqual(archive.read(name), git_bytes(ROOT, "show", self.revision + ":" + name))
            self.assertIn("原图文件、显式 guide、provenance", archive.read("SOURCE-README.md").decode())

    def test_privacy_redaction_preserves_all_stroke_values_and_does_not_mutate_input(self):
        original = dict(experiment=dict(source="C:\\Users\\private\\image.png"),
                        strokes=[dict(points=[[1.2345678901234, 2]], start=17, diagnostics=dict(path="verbatim"))])
        frozen = copy.deepcopy(original); changes = []
        published = public_copy(original, changes)
        self.assertEqual(original, frozen)
        self.assertEqual(published["strokes"], frozen["strokes"])
        self.assertEqual(published["experiment"]["source"], "inputs/image.png")
        self.assertEqual(changes[0]["field"], "/experiment/source")
        self.assertNotIn("private", dump(changes))

    def test_publish_preserves_input_embeds_zip_and_closes_every_link(self):
        with tempfile.TemporaryDirectory(prefix=".publish-test-", dir=ROOT / "tests") as folder:
            root, replay, raw = self.inputs(folder)
            before = replay.read_bytes(); output = root / "public"
            result = publish(ROOT, replay, root, output, self.revision, digest(before))
            self.assertEqual(result["htmlVerified"], 4)
            self.assertEqual(replay.read_bytes(), before)
            data = json.loads((output / "painting-data-v8.1-64.json").read_bytes())
            self.assertEqual(data["strokes"], raw["strokes"])
            self.assertEqual(data["version"], "7.2")
            document = (output / "replay-v8.1-64.html").read_text(encoding="utf-8")
            self.assertIn("Uint8Array.from(atob(value.payload)", document)
            self.assertIn("mime:'application/zip'", document)
            self.assertIn('id="download-generator" class="quiet"', document)
            self.assertNotIn("source-status", document)
            self.assertEqual((output / ZIP_NAME).read_bytes(), self.archive)
            with self.assertRaisesRegex(ValueError, "never overwritten"):
                publish(ROOT, replay, root, output, self.revision, digest(before))
            (output / "final-v8.1-64.svg").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "SHA256 changed"):
                validate_publication(output)

    def test_wrong_frozen_hash_and_65_strokes_fail_before_output(self):
        with tempfile.TemporaryDirectory(prefix=".publish-test-", dir=ROOT / "tests") as folder:
            root, replay, raw = self.inputs(folder)
            for bad_hash, extra_stroke in (("0" * 64, False), (None, True)):
                if extra_stroke:
                    raw["strokes"].append(copy.deepcopy(raw["strokes"][-1])); write(replay, dump(raw))
                    bad_hash = digest(replay.read_bytes())
                with self.assertRaises(ValueError):
                    publish(ROOT, replay, root, root / "public", self.revision, bad_hash)
                self.assertFalse((root / "public").exists())


if __name__ == "__main__":
    unittest.main()
