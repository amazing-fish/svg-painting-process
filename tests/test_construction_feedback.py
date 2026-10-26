"""Scheduling feedback regressions; these do not assert human readability."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from build_portrait import SharedCanvasPainter, generate
from construction_episodes import paint_construction_episode


def episode(guide=0, *, ready=False, accepted=2, error=30.0):
    assessment = dict(readableProxy=ready, meanProfileMAE=error,
                      stationFractionReady=.5, longestContinuousFraction=.4,
                      contrastCorrespondence=.25, meaningfulContrastStations=8,
                      meanSignedGradientAgreement=.2, meanWhiteErrorReduction=.6)
    return dict(guideIndex=guide, layer=0, startIndex=0, endIndex=accepted,
                accepted=accepted, attempted=4, rounds=1,
                status="readable_proxy" if ready else "budget_unfinished" if accepted else "no_improvement",
                needsRevisit=not ready, before={**assessment, "meanProfileMAE": error+4},
                after=assessment, history=[dict(afterAccepted=accepted, intent="plane", **assessment)],
                intentCounts={"plane": accepted}, acceptedLengths=[100.] * accepted,
                acceptedWidths=[25.] * accepted)


class ConstructionFeedbackTest(unittest.TestCase):
    def painter(self, guide_count=1):
        return SimpleNamespace(guides=[None] * guide_count, construction_reports=[], construction_blocked=None)

    def run_sequence(self, painter, reports):
        with patch("build_portrait.paint_construction_episode", side_effect=reports) as paint:
            with redirect_stdout(StringIO()):
                painter.sequence_result = SharedCanvasPainter.construction_sequence(painter, 0)
        return paint

    def test_revisit_passes_previous_assessment_to_same_movement(self):
        initial = episode(error=35.)
        follow = episode(error=31.)
        painter = self.painter()
        paint = self.run_sequence(painter, [initial, follow])
        self.assertEqual(paint.call_count, 2)
        self.assertEqual(paint.call_args.args[1], 0)
        self.assertIs(paint.call_args.kwargs.get("feedback"), initial,
                      "continuation must consume its actual unfinished report, not repeat an unrelated episode")

    def test_repair_budget_blocks_departure_to_next_movement(self):
        painter = self.painter(2)
        initial, follow = episode(error=35.), episode(error=31.)
        paint = self.run_sequence(painter, [initial, follow, episode(1, ready=True)])
        self.assertEqual(paint.call_count, 2, "exhausting repair budget must not start the next movement")
        self.assertTrue(all(call.args[1] == 0 for call in paint.call_args_list))
        terminal = painter.construction_reports[-1]
        self.assertTrue(terminal["needsRevisit"])
        self.assertEqual(terminal.get("transitionDecision"), "blocked_unresolved")
        self.assertIs(painter.sequence_result, False)
        self.assertEqual(painter.construction_blocked["guideIndex"], 0)
        self.assertEqual(painter.construction_blocked["layer"], 0)
        self.assertEqual(painter.construction_blocked["reportIndex"], 1)
        self.assertEqual(painter.construction_blocked["reason"], "repair_budget_exhausted_before_coarse_ready")

    def test_stalled_feedback_is_bounded_and_stays_unresolved(self):
        initial = episode(accepted=0, error=35.)
        follow = episode(accepted=0, error=35.)
        painter = self.painter(2)
        paint = self.run_sequence(painter, [initial, follow, episode(1, ready=True)])
        self.assertEqual(sum(call.args[1] == 0 for call in paint.call_args_list), 2,
                         "stalled work must not cause an unbounded retry loop")
        terminal = painter.construction_reports[1]
        self.assertTrue(terminal["needsRevisit"])
        self.assertEqual(terminal.get("transitionDecision"), "stalled_unresolved")
        self.assertEqual(painter.construction_reports[-1].get("transitionDecision"), "proxy_ready")
        self.assertIs(painter.sequence_result, True)
        self.assertIsNone(painter.construction_blocked)

    def test_blocked_value_phase_does_not_hide_failure_with_refinement(self):
        canvas = np.full((8, 8, 3), 255, dtype=np.uint8)
        painter = SimpleNamespace(strokes=[], clock=0, layers=[], events=[], canvas=canvas,
                                  src=canvas.copy(), construction_blocked=None,
                                  refine_pass=Mock(), export=Mock(),
                                  low_frequency_evidence=lambda: {})

        def block(_layer):
            painter.construction_blocked = dict(layer=1, guideIndex=0, reportIndex=1,
                                               reason="repair_budget_exhausted_before_coarse_ready")
            return False

        painter.construction_sequence = Mock(side_effect=block)
        with tempfile.TemporaryDirectory() as folder, patch("build_portrait.OUT", Path(folder)):
            with redirect_stdout(StringIO()):
                result = SharedCanvasPainter.paint_phase(painter, 1)
        painter.refine_pass.assert_not_called()
        self.assertIs(result, False)
        painter.export.assert_called_once()

    def test_generate_stops_later_stages_when_phase_is_blocked(self):
        painter = SimpleNamespace(guides=[], guide_metadata={}, paint_phase=Mock(return_value=False),
                                  export=Mock(), construction_blocked=dict(layer=0))
        with tempfile.TemporaryDirectory() as folder, patch("build_portrait.SharedCanvasPainter", return_value=painter):
            with redirect_stdout(StringIO()):
                result = generate(Path(folder) / "unused.png", Path(folder) / "run", 128, 4)
        self.assertIs(result, painter)
        painter.paint_phase.assert_called_once_with(0)
        painter.export.assert_called_once()

    def test_real_repair_paints_without_degrading_coarse_structure(self):
        # A synthetic two-plane figure keeps this regression independent of
        # private gallery images. Use actual extraction, footprints and paint.
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "figure.png"
            image = Image.new("RGB", (160, 200), "white")
            brush = ImageDraw.Draw(image)
            brush.ellipse((53, 15, 105, 65), fill=(190, 145, 120))
            brush.polygon([(51, 66), (106, 66), (131, 144), (120, 187),
                           (37, 187), (28, 144)], fill=(45, 65, 100))
            brush.polygon([(79, 68), (105, 68), (128, 143), (117, 185),
                           (83, 185)], fill=(80, 104, 146))
            image.save(source)
            painter = SharedCanvasPainter(source, max_edge=200)
            initial = paint_construction_episode(painter, 0, layer=0, palette_size=12,
                                                 max_strokes=1, max_rounds=1)
            self.assertTrue(initial["needsRevisit"])
            start = len(painter.strokes)
            repair = paint_construction_episode(painter, 0, layer=0, palette_size=28,
                                                max_strokes=5, max_rounds=1, feedback=initial)
            self.assertGreater(repair["accepted"], 0, "fixture must exercise painting, not a vacuous stall")
            self.assertLessEqual(repair["accepted"], 5)
            self.assertEqual(len(painter.strokes) - start, repair["accepted"])
            self.assertEqual(len(repair["history"]), repair["accepted"])
            self.assertGreater(repair["rejectedCoarse"], 0, "fixture must exercise the coarse rejection gate")
            self.assertEqual(repair["feedbackConsumed"]["after"], initial["after"])
            previous = repair["before"]
            for assessment in repair["history"]:
                self.assertLessEqual(assessment["meanProfileMAE"], previous["meanProfileMAE"] + 1.e-7)
                for key in ("stationFractionReady", "longestContinuousFraction", "contrastCorrespondence"):
                    self.assertGreaterEqual(assessment[key] + 1.e-7, previous[key], key)
                previous = assessment
            # The preview used for acceptance must match what was physically
            # painted; no selective masking or uncommitted candidate counts.
            measured = paint_construction_episode(painter, 0, layer=0, profile_only=True)
            for key in ("meanProfileMAE", "stationFractionReady", "longestContinuousFraction",
                        "contrastCorrespondence"):
                self.assertAlmostEqual(repair["after"][key], measured["after"][key], places=7)
            self.assertFalse(repair["after"]["readableProxy"])
            self.assertTrue(repair["needsRevisit"], "bounded incomplete work must remain unresolved")
            self.assertNotEqual(repair["status"], "readable_proxy")
            self.assertTrue(repair["stopReason"])


if __name__ == "__main__":
    unittest.main()
