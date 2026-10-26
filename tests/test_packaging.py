"""An executable player must contain filled, unambiguous payload containers."""
from pathlib import Path
import base64
import gzip
import json
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from pack_v7 import GENERATOR_VERSION
from render_player import render_player


def minimal_data():
    return dict(version=GENERATOR_VERSION, width=20, height=20,
                strokes=[dict(start=0, duration=100, color="#334455", opacity=1,
                              d="M0,0 L10,0 L10,2 L0,2 Z", points=[[0, 1], [10, 1]],
                              left=[[0, 0], [10, 0]], right=[[0, 2], [10, 2]])],
                totalDuration=100, layers=[dict(name="test")])


class PackagingTest(unittest.TestCase):
    def render(self, template):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "player_v7.html").write_text(template, encoding="utf-8")
            data_path = root / "data.json"
            data_path.write_text(json.dumps(minimal_data()), encoding="utf-8")
            target = root / "output.html"
            with patch("render_player.ROOT", root), patch("render_player.complete_source", return_value="print('generator')"):
                render_player(data_path, target)
            return target.read_text(encoding="utf-8")

    def test_missing_or_duplicate_data_container_is_rejected(self):
        template = (ROOT / "player_v7.html").read_text(encoding="utf-8")
        for malformed in (template.replace('id="painting-data"', 'id="missing-data"'),
                          template + '<script id="painting-data">__PAINTING_DATA__</script>'):
            with self.subTest(template=malformed[-100:]):
                with self.assertRaisesRegex(ValueError, "painting-data"):
                    self.render(malformed)

    def test_unfilled_payload_is_rejected_before_writing(self):
        template = (ROOT / "player_v7.html").read_text(encoding="utf-8")
        malformed = template.replace('id="painting-data">__PAINTING_DATA__',
                                     'id="painting-data">__WRONG_PAYLOAD__')
        with self.assertRaisesRegex(ValueError, "painting-data"):
            self.render(malformed)

    def test_legal_placeholder_text_outside_payload_is_not_replaced(self):
        template = (ROOT / "player_v7.html").read_text(encoding="utf-8")
        literal = "const sourceExample='__PAINTING_DATA__';"
        result = self.render(template + "<script>" + literal + "</script>")
        self.assertIn(literal, result)
        payload = re.search(r'<script[^>]*id="painting-data"[^>]*>(.*?)</script>', result, re.S).group(1)
        decoded = json.loads(gzip.decompress(base64.b64decode(json.loads(payload)["payload"])))
        self.assertEqual(decoded, minimal_data())

    def test_real_source_download_is_complete_and_payload_is_filled(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data_path = root / "data.json"
            data_path.write_text(json.dumps(minimal_data()), encoding="utf-8")
            result = render_player(data_path, root / "player.html").read_text(encoding="utf-8")
            for element in ("painting-data", "generator-source"):
                payload = re.search(r'<script[^>]*id="' + element + r'"[^>]*>(.*?)</script>', result, re.S).group(1)
                envelope = json.loads(payload)
                raw = gzip.decompress(base64.b64decode(envelope["payload"]))
                if element == "painting-data":
                    self.assertEqual(json.loads(raw), minimal_data())
                else:
                    self.assertEqual(envelope["filename"], f"portrait-painting-v{GENERATOR_VERSION}.py")
                    compile(raw.decode("utf-8"), envelope["filename"], "exec")
                    self.assertIn(b"PLAYER_TEMPLATE_V7", raw)


if __name__ == "__main__":
    unittest.main()
