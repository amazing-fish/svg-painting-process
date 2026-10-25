"""Published review players preserve engine origin and only link present files."""
from pathlib import Path
import base64
import gzip
import hashlib
import json
import re
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from package_review_artifacts import package_review_player, validate_html_artifact, write_index, PLAYER_VERSION, _check_painting


def painting(version="7.1"):
    return dict(version=version, width=20, height=20, totalDuration=100,
                layers=[dict(name="legacy")], strokes=[dict(start=0, duration=100, opacity=1, color="#334455",
                    d="M0,0 L10,0 L10,2 L0,2 Z", points=[[0, 1], [10, 1]],
                    left=[[0, 0], [10, 0]], right=[[0, 2], [10, 2]])])


class ReviewArtifactTests(unittest.TestCase):
    def archive(self, path, manifest=None, *, include_manifest=True):
        contents = b"# Historical generating engine v7.1\n"
        if manifest is None:
            manifest = dict(engineVersion="7.1", files={"build.py": hashlib.sha256(contents).hexdigest()})
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("build.py", contents)
            if include_manifest:
                archive.writestr("source-manifest.json", json.dumps(manifest))

    def prepare(self, folder):
        root = Path(folder)
        source = root / "matching-source.zip"
        self.archive(source)
        data = root / "painting-data.json"
        data.write_text(json.dumps(painting()), encoding="utf-8")
        return root, data, source

    def test_historical_player_preserves_data_and_uses_matching_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root, data, source = self.prepare(folder)
            target = package_review_player(data, root / "historical.html", "v7.1 原生失败对照", source.name)
            document = target.read_text(encoding="utf-8")
            self.assertIn("生成引擎：v7.1 原生失败对照", document)
            self.assertIn("画谱版本：7.1", document)
            self.assertIn("播放器：v" + PLAYER_VERSION, document)
            self.assertRegex(document, r'id="download-generator"[^>]*hidden[^>]*disabled')
            self.assertIn('href="matching-source.zip"', document)
            payload = json.loads(re.search(r'id="painting-data">(.*?)</script>', document, re.S).group(1))
            self.assertEqual(json.loads(gzip.decompress(base64.b64decode(payload["payload"]))), painting())
            source_payload = json.loads(re.search(r'id="generator-source">(.*?)</script>', document, re.S).group(1))
            self.assertEqual(source_payload["filename"], "source-archive-info.txt")
            self.assertNotIn(".py", source_payload["filename"])
            self.assertEqual(validate_html_artifact(target), target)

    def test_source_and_index_links_must_exist_and_be_relative(self):
        with tempfile.TemporaryDirectory() as folder:
            root, data, source = self.prepare(folder)
            for href in ("missing.zip", "https://example.com/source.zip", "/source.zip", "%2Fsource.zip", "C%3A/source.zip"):
                with self.subTest(href=href), self.assertRaises(ValueError):
                    package_review_player(data, root / "invalid.html", "v7.1历史", href)
            target = package_review_player(data, root / "player.html", "v7.1历史", source.name)
            index = write_index(root / "index.html", [dict(title="v7.1历史", href=target.name, status="失败对照")])
            self.assertIn('href="player.html"', index.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(ValueError, "does not exist"):
                write_index(root / "bad-index.html", [dict(title="不存在", href="missing.html", status="实验")])
            with self.assertRaisesRegex(ValueError, "explicit status"):
                write_index(root / "bad-index.html", [dict(title="未知状态", href=target.name)])

    def test_portal_refuses_raw_template_and_unfilled_top_level_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            root, data, source = self.prepare(folder)
            template = root / "raw-template.html"
            template.write_text((ROOT / "player_v7.html").read_text(encoding="utf-8"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source template"):
                validate_html_artifact(template)
            with self.assertRaisesRegex(ValueError, "source template"):
                write_index(root / "index.html", [dict(title="误开模板", href=template.name, status="不可发布")])
            comparison = root / "comparison.html"
            comparison.write_text('<script type="application/json" id="cases">__CASES__</script>', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unfilled cases"):
                validate_html_artifact(comparison)

    def test_legal_source_examples_are_not_top_level_payload_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            root, data, source = self.prepare(folder)
            target = package_review_player(data, root / "player.html", "v7.1", source.name)
            with target.open("a", encoding="utf-8") as output:
                output.write("<script>const example='__PAINTING_DATA__';</script>")
            validate_html_artifact(target)
            write_index(root / "index.html", [dict(title="保留源码示例", href=target.name, status="失败对照")])

    def test_painting_requires_full_svg_and_prefix_validation(self):
        with self.subTest(case="missing dimensions"), self.assertRaises(ValueError):
            _check_painting({"version": "7.2", "strokes": [{}]})
        bad_cases = []
        for key, value in (("width", 0), ("height", float("nan")), ("totalDuration", 50)):
            data = painting()
            data[key] = value
            bad_cases.append(data)
        for key, value in (("duration", -1), ("color", "red"), ("left", [[0, 0]])):
            data = painting()
            data["strokes"][0][key] = value
            bad_cases.append(data)
        overlap = painting()
        overlap["strokes"].append(dict(overlap["strokes"][0]))
        bad_cases.append(overlap)
        with tempfile.TemporaryDirectory() as folder:
            root, data_path, source = self.prepare(folder)
            for index, data in enumerate(bad_cases):
                data_path.write_text(json.dumps(data), encoding="utf-8")
                with self.subTest(case=index), self.assertRaises(ValueError):
                    package_review_player(data_path, root / "invalid.html", "v7.1历史", source.name)

    def test_source_manifest_version_files_and_hashes_are_required(self):
        with tempfile.TemporaryDirectory() as folder:
            root, data, source = self.prepare(folder)
            valid_hash = hashlib.sha256(b"# Historical generating engine v7.1\n").hexdigest()
            variants = [None, dict(engineVersion="7.2", files={"build.py": valid_hash}),
                        dict(engineVersion="7.1", files={}),
                        dict(engineVersion="7.1", files={"build.py": "0" * 64}),
                        dict(engineVersion="7.1", files={"missing.py": valid_hash})]
            for index, manifest in enumerate(variants):
                self.archive(source, manifest, include_manifest=manifest is not None)
                with self.subTest(case=index), self.assertRaises(ValueError):
                    package_review_player(data, root / "invalid.html", "v7.1历史", source.name)


if __name__ == "__main__":
    unittest.main()
