"""The lay-in follows an open movement, rather than preceding it with tiles."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw
from build_portrait import SharedCanvasPainter, resample


class CoarseLayinTest(unittest.TestCase):
    def test_first_marks_follow_the_supplied_curved_movement(self):
        # A calibrated open movement isolates the painter from extraction.
        # These coordinates belong only to this synthetic regression fixture.
        points = [[95, 30], [120, 110], [155, 205], [145, 285]]
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'curve.png'
            image = Image.new('RGB', (240, 320), 'white')
            ImageDraw.Draw(image).line([tuple(p) for p in points], fill=(90, 105, 140), width=48)
            image.save(source)
            painter = SharedCanvasPainter(source, max_edge=320)
            painter.guides = [resample(points, [24] * len(points))]
            with redirect_stdout(StringIO()):
                painter.compose()
            self.assertGreater(len(painter.strokes), 0)
            stroke = painter.strokes[0]
            self.assertGreater(stroke['length'] / stroke['width'], 3.,
                               'The initial movement must not be a near-square grid tile')
            path = np.asarray(stroke['points'])
            chord = path[-1] - path[0]
            normal = np.array([-chord[1], chord[0]]) / np.linalg.norm(chord)
            bend = np.max(np.abs((path-path[0]) @ normal))
            self.assertGreater(bend, 3., 'The first mark must retain the supplied open bend')
            self.assertTrue(all(s['group'].startswith('construction-') for s in painter.strokes))


if __name__ == '__main__':
    unittest.main()
