"""Synthetic full-ribbon and explicit-input regressions; no private fixtures."""
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import io
import json
import unittest

import cv2
import numpy as np
from PIL import Image
import build_portrait as base
import construction_episodes as core
from experiments.portrait_coarse import construction, energy, physical
from experiments.portrait_coarse.canonical import CanonicalFootprint, Engine, source_geometry
from experiments.portrait_coarse.contracts import (geometry_digest, preserving_resample, reducing_caps,
                                                  observe_final_support, validate_final)
from experiments.portrait_coarse.runner import EnergyAssessor, ExperimentalPainter, generate, load_inputs, sha256
from experiments.portrait_coarse.safety import select_initial_load


class InitialLoadSafetyTests(unittest.TestCase):
    def setUp(self):
        source = np.full((160, 100, 3), 255, np.uint8)
        source[10:151, 43:58] = 90
        self.painter = SimpleNamespace(src=source, canvas=np.full_like(source, 255), h=160, w=100)
        self.points = np.array([[50., 20.], [50., 80.], [50., 140.]])
        self.radii = np.full(3, 10.)
        _, _, _, self.offsets, self.samples, self.positions = core._probes(self.points, self.radii)
        self.target = core._profile(cv2.GaussianBlur(source, (0, 0), 16.), self.samples)
        self.assessor = EnergyAssessor(source)
        self.template = dict(geometry=(0., .67, 0, 3), opacity=.97, fitLoad=False)
        self.contract = dict(observationDomain='model-attention', sourceReady=False)

    def proposal(self, width):
        fp = base.SharedCanvasPainter.footprint(self.painter, self.points+[-width, 0], self.points+[width, 0])
        return dict(footprint=fp, color=[90, 90, 90], points=self.points, radii=np.full(3, width))

    def assess(self, proposal):
        with patch.object(construction, '_assessment', self.assessor):
            return construction._preview_assessment(self.painter, proposal, .97, self.target,
                self.samples, self.offsets, self.positions, 0)

    def select(self, raw, fitted, **overrides):
        current = self.assessor(self.painter.canvas, self.target, self.samples, self.offsets, self.positions, 0)
        factory = Mock(return_value=fitted)
        report = dict(attempted=1, rejectedCoarse=0)
        options = dict(current=current, template=self.template, contract=self.contract, layer=0,
                       phase=0, propose_fitted=factory, assess=self.assess, report=report)
        options.update(overrides)
        result = select_initial_load(raw, self.assess(raw) if raw is not None else None, **options)
        return result, factory, report

    def test_safe_raw_full_footprint_is_preserved_without_fitted_attempt(self):
        raw = self.proposal(7)
        canvas = self.painter.canvas.copy()
        result, factory, report = self.select(raw, self.proposal(5))
        self.assertIs(result[0], raw)
        self.assertFalse(result[2])
        factory.assert_not_called()
        self.assertEqual(report['initialLoadSafetyPreviews'][0]['decision'], 'raw_safe')
        np.testing.assert_array_equal(self.painter.canvas, canvas)

    def test_spilling_raw_uses_same_template_fitted_whole_ribbon(self):
        raw, fitted = self.proposal(15), self.proposal(7)
        result, factory, report = self.select(raw, fitted)
        self.assertGreater(self.assess(raw)['coarseEnergy']['whiteLeakP95'], energy.WHITE_LEAK_P95_READY)
        self.assertIs(result[0], fitted)
        self.assertTrue(result[2])
        factory.assert_called_once_with(dict(self.template, fitLoad=True))
        self.assertEqual(report['attempted'], 2)
        self.assertEqual(report['initialLoadSafetyPreviews'][0]['decision'], 'fitted_safe')

    def test_two_spilling_ribbons_are_rejected_as_whole_candidates(self):
        result, _, report = self.select(self.proposal(15), self.proposal(10))
        self.assertIsNone(result)
        self.assertEqual(report['rejectedCoarse'], 1)
        self.assertEqual(report['initialLoadSafetyPreviews'][0]['decision'], 'rejected_fitted_spill')
        self.assertTrue(np.all(self.painter.canvas == 255))

    def test_unavailable_raw_never_opens_a_new_search(self):
        result, factory, report = self.select(None, self.proposal(7))
        self.assertIsNone(result)
        factory.assert_not_called()
        self.assertEqual(report['attempted'], 1)

    def test_unavailable_fitted_rejects_raw(self):
        result, factory, report = self.select(self.proposal(15), None)
        self.assertIsNone(result)
        factory.assert_called_once()
        self.assertEqual(report['initialLoadSafetyPreviews'][0]['decision'], 'rejected_fitted_unavailable')

    def test_canvas_change_recomputes_safety_for_the_identical_raw_geometry(self):
        raw = self.proposal(10)
        first, factory, _ = self.select(raw, self.proposal(7))
        self.assertIsNot(first[0], raw)
        factory.assert_called_once()
        self.painter.canvas[10:151, 25:76] = 90
        second, factory, report = self.select(raw, self.proposal(7))
        self.assertIs(second[0], raw)
        factory.assert_not_called()
        self.assertGreater(report['initialLoadSafetyPreviews'][0]['allowedWhiteLeak'], energy.WHITE_LEAK_P95_READY)

    def test_scope_does_not_change_other_layers_phases_or_contracts(self):
        raw = self.proposal(15)
        for options in (dict(layer=1), dict(phase=1), dict(contract={})):
            with self.subTest(options=options):
                result, factory, _ = self.select(raw, self.proposal(7), **options)
                self.assertIs(result[0], raw)
                factory.assert_not_called()

    def test_canonical_preview_put_and_svg_reuse_the_same_immutable_edges(self):
        curve = physical.C1Curve(self.points, self.radii)
        ribbon = physical.finalize(self.points, self.radii, np.array([0., .5, 1.]), curve,
                                   source_pressure_upper=lambda tags: np.full_like(tags, 12.))
        self.assertTrue(ribbon.diagnostics['ready'])
        self.assertFalse(ribbon.left.flags.writeable)
        fp = CanonicalFootprint(base.SharedCanvasPainter.footprint(self.painter, ribbon.left, ribbon.right), ribbon)
        candidate = dict(footprint=fp, color=[90]*3)
        preview = self.assess(candidate)
        self.painter.strokes, self.painter.clock = [], 0
        self.painter.canonical_engine = SimpleNamespace(active=0, accepted=[])
        ExperimentalPainter.put(self.painter, ribbon.points, ribbon.radii, [90]*3, [50, 80], 0, 'plane', .97, proposal=fp)
        stroke = self.painter.strokes[0]
        self.assertEqual(stroke['d'], ribbon.svg_path())
        self.assertEqual(stroke['left'], ribbon.left.tolist())
        self.assertEqual(stroke['right'], ribbon.right.tolist())
        actual = self.assessor(self.painter.canvas, self.target, self.samples, self.offsets, self.positions, 0)
        self.assertEqual(actual['coarseEnergy'], preview['coarseEnergy'])


class SourceReadyFinalGeometryTests(unittest.TestCase):
    def test_sparse_ready_stations_cannot_certify_the_interpolated_ribbon(self):
        source = np.full((101,151,3), 255, np.uint8)
        points = np.array([[25.,50.],[75.,50.],[125.,50.]])
        radii = np.full(3,12.)
        for point in points:
            cv2.circle(source, tuple(point.astype(int)), 18, (40,40,40), -1)
            cv2.circle(source, tuple(point.astype(int)), 12, (160,160,160), -1)
        background = energy.estimate_background(source)
        upper, records = observe_final_support(source, points, radii, background)
        self.assertTrue(validate_final(points, radii, upper, records)['ready'])
        painter = base.SharedCanvasPainter.__new__(base.SharedCanvasPainter)
        painter.src, painter.canvas = source, np.full_like(source,255)
        painter.w, painter.h = 151,101
        painter.guides = [(points,radii)]
        painter.palettes, painter.rng = {}, np.random.default_rng(83177)
        payload = dict(candidates=[dict(name='three-separated-support-islands', points=points.tolist(),
            radii=radii.tolist(), geometryReady=dict(ready=True))])
        engine = Engine(painter,payload,[0],{})
        engine.active, engine.layer = 0,0
        final_points, final_radii = painter.guides[0]
        proposal = engine.proposal(painter,
            dict(geometry=(0.,.67,0,len(final_points)),fitLoad=False,opacity=.97),
            final_points,final_radii,cv2.GaussianBlur(source,(0,0),16.),12)
        self.assertTrue(proposal is None, 'Sparse source-ready stations cannot justify paint across unsupported intervals')
        self.assertTrue(any('final_source_support:' in key[2] for key in engine.calls))
        self.assertTrue(np.all(painter.canvas==255))


class ExplicitInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source.png'
        Image.new('RGB', (100, 160), 'white').save(self.source)
        self.guides, self.provenance = self.root/'guides.json', self.root/'provenance.json'
        raw_candidate = dict(name='synthetic-open-axis', person=0, attention='test-field',
                             points=[[50.,20.],[50.,80.],[50.,140.]], radii=[10.,10.,10.], pixelCenterRefit=True)
        raw = dict(metadata=dict(sourceSHA256=sha256(self.source)), candidates=[raw_candidate])
        self.raw_path = self.root/'raw.json'
        self.raw_path.write_text(json.dumps(raw), encoding='utf-8')
        points, radii = preserving_resample(raw_candidate['points'], raw_candidate['radii'])
        radii = reducing_caps(radii)
        self.candidate = dict(raw_candidate, points=points.tolist(), radii=radii.tolist())
        geometry_sha = geometry_digest(points, radii)
        self.candidate['preparedGeometry'] = dict(contract='attention-measured-prepared-geometry-v1',
            sourceSHA256=sha256(self.source), observationDomain='model-attention', sourceReady=False,
            noFurtherSmoothing=True, finalGeometrySHA256=geometry_sha, inputSourceFileSHA256=sha256(self.raw_path))
        self.payload = dict(metadata=dict(sourceSHA256=sha256(self.source), sourceSize=[100,160],
            manualGuidesUsed=False, guideOrder=[0], pixelCenterRefit=True), candidates=[self.candidate],
            gestures=[[self.candidate['points'],self.candidate['radii']]])
        self.bindings = dict(bindings={geometry_sha: dict(path='raw.json', sha256=sha256(self.raw_path),
                                                        observationDomain='model-attention', sourceReady=False)})
        self.write_bundle()

    def write_bundle(self):
        self.guides.write_text(json.dumps(self.payload), encoding='utf-8')
        self.bindings['inputSHA256'] = sha256(self.guides)
        self.provenance.write_text(json.dumps(self.bindings), encoding='utf-8')

    def test_relative_raw_provenance_reconstructs_geometry_without_claiming_source_ready(self):
        payload, binding, _, contracts, _ = load_inputs(self.source, self.guides, self.provenance)
        points, radii, origin = source_geometry(payload['candidates'][0], binding)
        self.assertFalse(origin['sourceReady'])
        self.assertFalse(contracts[0]['sourceReady'])
        self.assertTrue(origin['reconstructsPreparedHashExactly'])
        self.assertEqual(len(points), 3)
        np.testing.assert_array_equal(radii, [10.,10.,10.])

    def test_missing_explicit_source_never_falls_back(self):
        with self.assertRaisesRegex(ValueError, 'required'):
            load_inputs(None, self.guides, self.provenance)

    def test_changed_source_fails_before_output_creation(self):
        Image.new('RGB', (100,160), 'black').save(self.source)
        output = self.root/'run'
        with self.assertRaisesRegex(ValueError, 'source SHA mismatch'):
            generate(self.source, self.guides, self.provenance, output)
        self.assertFalse(output.exists())

    def test_geometry_or_raw_provenance_tampering_fails_closed(self):
        self.payload['candidates'][0]['radii'][2] += 1
        self.write_bundle()
        with self.assertRaisesRegex(ValueError, 'preparedGeometry'):
            load_inputs(self.source, self.guides, self.provenance)
        self.payload['candidates'][0]['radii'][2] -= 1
        self.write_bundle()
        self.raw_path.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'provenance SHA'):
            load_inputs(self.source, self.guides, self.provenance)

    def test_missing_binding_rejects_prepared_attention(self):
        self.bindings['bindings'] = {}
        self.write_bundle()
        with self.assertRaisesRegex(ValueError, 'lacks explicit bound raw provenance'):
            load_inputs(self.source, self.guides, self.provenance)

    def test_blank_source_is_unknown_and_blocks_without_empty_success(self):
        original = base.extract_guides, base.resample, base.OUT, base.QA, core._proposal
        with redirect_stdout(io.StringIO()):
            painter = generate(self.source, self.guides, self.provenance, self.root/'run', through=2)
        self.assertEqual(len(painter.strokes), 0)
        self.assertEqual(len(painter.layers), 1)
        self.assertFalse(painter.layers[0]['complete'])
        self.assertEqual(painter.construction_reports[0]['status'], 'unsupported_source')
        self.assertEqual(painter.construction_reports[0]['stopReason'], 'source_support_unknown')
        self.assertFalse(painter.construction_reports[0]['after']['coarseEnergy']['mainSurfaceReady'])
        self.assertEqual(painter.construction_blocked['guideIndex'], 0)
        self.assertEqual(painter.construction_blocked['reason'], 'source_support_unknown')
        self.assertEqual(original, (base.extract_guides, base.resample, base.OUT, base.QA, core._proposal))
        data = json.loads((self.root/'run/qa/painting-data.json').read_text(encoding='utf-8'))
        self.assertFalse(data['experiment']['automaticSemanticAcceptance'])
        self.assertFalse(data['experiment']['sourceReady'])

    def test_zero_signal_is_never_ready_even_at_identity(self):
        source = np.full((12,81,3), 255.)
        metric = energy.evaluate(source, source, np.linspace(-3,3,81), np.linspace(0,100,12), np.full(12,10.))
        self.assertEqual(metric['energy'], 0.)
        self.assertFalse(metric['sourceSupportPresent'])
        self.assertFalse(metric['mainSurfaceReady'])

    def test_strict_stop_blocks_next_guide_after_no_improvement(self):
        painter = SimpleNamespace(guides=[object(), object()], construction_reports=[])
        episode = Mock(side_effect=lambda *args, **kwargs: dict(needsRevisit=True, status='no_improvement'))
        self.assertFalse(construction.strict_sequence(painter, 0, episode))
        self.assertEqual([call.args[1] for call in episode.call_args_list], [0,0])
        self.assertEqual(painter.construction_blocked['guideIndex'], 0)
        self.assertEqual(painter.construction_reports[-1]['transitionDecision'], 'blocked_unresolved')

    def test_value_stage_also_stops_on_unresolved_continuation(self):
        painter = ExperimentalPainter.__new__(ExperimentalPainter)
        painter.guides, painter.construction_reports = [object(),object()], []
        painter.episode = Mock(side_effect=lambda *args, **kwargs: dict(needsRevisit=True, status='no_improvement'))
        self.assertFalse(painter.construction_sequence(1))
        self.assertEqual([call.args[1] for call in painter.episode.call_args_list], [0,0])
        self.assertEqual(painter.construction_blocked['reason'], 'no_safe_value_progress_before_proxy_ready')


if __name__ == '__main__':
    unittest.main()
