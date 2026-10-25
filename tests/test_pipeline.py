"""Small integration checks; synthetic figures do not establish portrait quality."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from PIL import Image, ImageDraw
from build_portrait import generate
from render_player import render_player
from export_svg import export_svg


class PipelineTest(unittest.TestCase):
    def test_repackaging_cannot_mislabel_historical_generator(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / 'old-data.json'
            data.write_text(json.dumps({'version': '7.0'}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'does not match generator'):
                render_player(data, root / 'old-painting.html')
            self.assertFalse((root / 'old-painting.html').exists())

    def test_shared_canvas_to_offline_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            image = Image.new("RGB", (96, 128), "white")
            brush = ImageDraw.Draw(image)
            brush.ellipse((32, 10, 64, 43), fill=(190, 145, 120))
            brush.polygon([(34, 44), (63, 44), (79, 90), (72, 118), (23, 118), (17, 90)], fill=(45, 65, 100))
            source = work / "figure.png"
            image.save(source)
            generate(source, work / "run", 128, 1)
            data_path = work / "run" / "qa" / "painting-data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            self.assertEqual(data["automaticGuides"]["manualCoordinateCount"], 0)
            self.assertEqual(len(data["layers"]), 1)
            self.assertGreater(len(data["strokes"]), 0)
            expected = 0
            for stroke in data["strokes"]:
                self.assertEqual(stroke["start"], expected)
                expected = stroke["start"] + stroke["duration"] + stroke["pauseAfter"]
            self.assertEqual(expected, data["totalDuration"])
            html_path = render_player(data_path, work / "painting-v7.0.html")
            html = html_path.read_text(encoding="utf-8")
            self.assertNotIn("__PAINTING_DATA__", html)
            self.assertNotIn("__GENERATOR_SOURCE__", html)
            self.assertIn("对数播放", html)
            target = work / "painting-v7.0.svg"
            export_svg(data, target)
            self.assertTrue(target.is_file())
            self.assertNotIn("<image", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
