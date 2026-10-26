"""Explicit frozen-input experiment. No guide extraction or model inference."""
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from unittest.mock import patch
import hashlib
import json
import time

import numpy as np
from PIL import Image, ImageOps
import build_portrait as base
import construction_episodes as core
from tools.export_svg import export_svg
from . import construction, energy, physical
from .canonical import CanonicalFootprint, Engine, source_geometry
from .contracts import is_prepared_geometry, is_ready_geometry, observe_final_support, validate_final

EXPERIMENT_VERSION = 'portrait-coarse-v8.1'
_RUN_LOCK = RLock()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_inputs(source, guides, provenance):
    """Fail closed before creating an output directory or constructing paint."""
    if source is None or guides is None or provenance is None:
        raise ValueError('Source, guide bundle, and provenance are required')
    source, guides, provenance = (Path(p).resolve() for p in (source, guides, provenance))
    payload = json.loads(guides.read_text(encoding='utf-8'))
    binding = json.loads(provenance.read_text(encoding='utf-8'))
    metadata = payload['metadata']
    source_sha = sha256(source)
    if metadata.get('sourceSHA256') != source_sha:
        raise ValueError('Guide bundle source SHA mismatch')
    if metadata.get('manualGuidesUsed') is not False:
        raise ValueError('An explicitly image-derived guide bundle is required')
    if binding.get('inputSHA256') != sha256(guides):
        raise ValueError('Guide bundle provenance SHA mismatch')
    if binding.get('sourceSHA256', source_sha) != source_sha:
        raise ValueError('Provenance source SHA mismatch')
    if not isinstance(binding.get('bindings'), dict):
        raise ValueError('Explicit geometry provenance bindings are required')
    binding['_baseDirectory'] = str(provenance.parent)
    with Image.open(source) as image:
        size = list(ImageOps.exif_transpose(image).size)
    if metadata.get('sourceSize') != size:
        raise ValueError('Guide coordinates must match the EXIF-oriented native source size')
    candidates, gestures = payload['candidates'], payload['gestures']
    if not candidates or len(candidates) != len(gestures):
        raise ValueError('Nonempty matching candidate and gesture lists are required')
    order = metadata.get('guideOrder')
    if (not isinstance(order, list) or any(type(i) is not int for i in order) or
            sorted(order) != list(range(len(candidates)))):
        raise ValueError('Explicit guide order must be a permutation of every candidate')
    contracts = {}
    for visited, index in enumerate(order):
        candidate = candidates[index]
        points, radii = np.asarray(candidate['points'], float), np.asarray(candidate['radii'], float)
        if (points.ndim != 2 or points.shape[1] != 2 or len(points) < 3 or
                radii.shape != (len(points),) or not np.isfinite(points).all() or
                not np.isfinite(radii).all() or np.any(radii <= 0) or
                np.any(np.linalg.norm(np.diff(points, axis=0), axis=1) <= 0)):
            raise ValueError('Guide geometry must be finite, positive, and have continuous travel')
        if gestures[index] != [candidate['points'], candidate['radii']]:
            raise ValueError('Gesture and candidate geometry differ')
        if candidate.get('geometryReady') and candidate.get('preparedGeometry'):
            raise ValueError('A candidate cannot claim two geometry contracts')
        ready = is_ready_geometry(candidate, source_sha)
        prepared = is_prepared_geometry(candidate, source_sha)
        if ready or prepared:
            contracts[visited] = candidate['geometryReady' if ready else 'preparedGeometry']
            source_geometry(candidate, binding)
    evidence = dict(sourceSHA256=source_sha, inputSHA256=sha256(guides),
                    provenanceSHA256=sha256(provenance), source=str(source),
                    guides=str(guides), provenance=str(provenance))
    return payload, binding, order, contracts, evidence


class EnergyAssessor:
    """Cache only source observations; sample every new candidate canvas."""
    def __init__(self, source):
        self.source = source
        self.background = energy.estimate_background(source)
        self.source_cache = {}

    def __call__(self, canvas, target_profile, samples, offsets, positions, layer=1):
        report = core._assessment(canvas, target_profile, samples, offsets, positions, layer)
        report['profileReadableProxy'] = report['readableProxy']
        report['valuePending'] = not report['readableProxy']
        if layer == 0:
            center = samples[:, len(offsets)//2, :]
            normal_radius = (samples[:, -1, :]-samples[:, 0, :])/(offsets[-1]-offsets[0])
            wide_offsets = np.linspace(-3., 3., 81)
            wide_samples = center[:, None, :]+normal_radius[:, None, :]*wide_offsets[None, :, None]
            key = wide_samples.tobytes()
            if key not in self.source_cache:
                self.source_cache[key] = core._sample(self.source, wide_samples)
            metric = energy.evaluate(self.source_cache[key], core._sample(canvas, wide_samples),
                                     wide_offsets, positions, np.linalg.norm(normal_radius, axis=1),
                                     self.background)
            report['coarseEnergy'] = metric
            report['readableProxy'] = metric['mainSurfaceReady']
            report['completionMeaning'] = 'Experimental main-surface energy only; no semantic acceptance.'
        return report


class ExperimentalPainter(base.SharedCanvasPainter):
    def __init__(self, source, payload, order, contracts, provenance, output, evidence):
        self.output = Path(output)
        self.input_evidence = evidence
        self.payload, self.order = payload, order
        self.geometry_ready = contracts
        self.proposal_trace = []
        metadata = payload['metadata']
        gestures = []
        for visited, index in enumerate(order):
            points, radii = payload['gestures'][index]
            if visited not in contracts and not metadata.get('geometryResampled', False):
                points, radii = base.resample(points, radii)
            gestures.append((points, radii))
        # The unmodified core initializer supplies palette/flow state. Injection
        # is bounded by this context and restored even when initialization fails.
        identity = lambda p, r, step=7: (np.asarray(p, float).copy(), np.asarray(r, float).copy())
        guide_metadata = dict(metadata, method='explicit image-derived experimental guide bundle',
                              originalIndices=order)
        with patch.object(base, 'extract_guides', return_value=(gestures, guide_metadata)), \
                patch.object(base, 'resample', side_effect=identity):
            super().__init__(source, max_edge=max(metadata['sourceSize']))
        background = energy.estimate_background(self.src)
        for visited, record in contracts.items():
            if record.get('ready') is True:
                points, radii = self.guides[visited]
                upper, records = observe_final_support(self.src, points, radii, background)
                check = validate_final(points, radii, upper, records)
                if not check['ready']:
                    raise ValueError('Final source-support contract failed: '+str(check['rejectionReasons']))
        self.canonical_engine = Engine(self, payload, order, provenance)
        self.assessor = EnergyAssessor(self.src)

    @contextmanager
    def output_context(self):
        with patch.object(base, 'OUT', self.output/'output'), patch.object(base, 'QA', self.output/'qa'):
            yield

    def proposal(self, painter, template, points, radii, target, palette_size):
        engine = self.canonical_engine
        result = (engine.proposal if engine.active in engine.guides else core_proposal)(
            painter, template, points, radii, target, palette_size)
        self.proposal_trace.append(dict(strokeIndex=len(self.strokes), geometry=list(template['geometry']),
            fitLoad=bool(template.get('fitLoad', True)), available=result is not None,
            length=result['length'] if result else None,
            canonical=bool(result and result.get('canonicalGeometry') is not None)))
        return result

    def episode(self, painter, guide_index, layer=1, **kwargs):
        engine = self.canonical_engine
        previous = engine.active, engine.layer
        engine.active = guide_index if guide_index in engine.guides else None
        engine.layer = layer
        candidate = self.payload['candidates'][self.order[guide_index]]
        self.auto_guides = bool(candidate.get('pixelCenterRefit', self.payload['metadata'].get('pixelCenterRefit', False)))
        try:
            if layer == 0:
                with patch.object(construction, '_proposal', self.proposal), \
                        patch.object(construction, '_assessment', self.assessor):
                    return construction.paint_construction_episode(self, guide_index, layer=layer, **kwargs)
            with patch.object(core, '_proposal', self.proposal):
                return core.paint_construction_episode(self, guide_index, layer=layer, **kwargs)
        finally:
            engine.active, engine.layer = previous

    def construction_sequence(self, layer):
        if layer == 0:
            return construction.strict_sequence(self, layer, self.episode)
        for index in range(len(self.guides)):
            report = self.episode(self, index, layer=layer, palette_size=28)
            self.construction_reports.append(report)
            if report['needsRevisit']:
                follow = self.episode(self, index, layer=layer, palette_size=96,
                                      max_strokes=5, max_rounds=1, feedback=report)
                follow['continuationOf'] = len(self.construction_reports)-1
                self.construction_reports.append(follow)
                report = follow
            report['transitionDecision'] = 'proxy_ready' if not report['needsRevisit'] else 'blocked_unresolved'
            if report['needsRevisit']:
                self.construction_blocked = dict(layer=layer, guideIndex=index,
                    reportIndex=len(self.construction_reports)-1, reason='no_safe_value_progress_before_proxy_ready')
                return False
        return True

    def put(self, points, radii, color, sample, layer, intent, opacity=1., group=None, proposal=None):
        if not isinstance(proposal, CanonicalFootprint):
            return super().put(points, radii, color, sample, layer, intent, opacity, group, proposal)
        geometry = proposal.geometry
        (x0, y0, x1, y1), mask = proposal
        alpha = mask[:, :, None]*opacity
        crop = self.canvas[y0:y1, x0:x1]
        crop[:] = np.rint(crop*(1-alpha)+np.array(color)*alpha).astype(np.uint8)
        length = float(physical.arc(geometry.points)[-1])
        duration = round(np.clip(130+length*2.5, 150, 3300))
        pause = 85 if intent not in ('accent', 'edge') else 100
        stroke = dict(d=geometry.svg_path(), left=geometry.left.tolist(), right=geometry.right.tolist(),
            points=geometry.points.tolist(), spine='M'+' L'.join(f'{x:.6f},{y:.6f}' for x,y in geometry.points),
            length=length, width=round(float(2*geometry.radii.max()), 3),
            color='#'+''.join(f'{int(c):02x}' for c in color), opacity=opacity, start=self.clock,
            duration=duration, pauseAfter=pause, layer=layer, stage=base.STAGES[layer], phase=base.PHASES[layer][0],
            intent=intent, intentLabel=base.INTENT_LABELS[intent], group=group or f'{layer}-{len(self.strokes)//10}',
            colorSample=sample, sourceSample=sample, sample=sample, type='sweep' if length>12 else 'dab', canonicalGeometry=True)
        self.strokes.append(stroke)
        self.clock += duration+pause
        self.canonical_engine.accepted.append(dict(strokeNumber=len(self.strokes),
            guideIndex=self.canonical_engine.active, layer=layer, diagnostics=geometry.diagnostics))
        return True

    def paint_phase(self, layer):
        with self.output_context():
            return super().paint_phase(layer)

    def export(self):
        with self.output_context():
            super().export()
        path = self.output/'qa'/'painting-data.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data['experiment'] = dict(version=EXPERIMENT_VERSION, explicitInputs=self.input_evidence,
            originalGuideOrder=self.order, requestedThrough=self.requested_through,
            automaticSemanticAcceptance=False, sourceReady=False,
            limitation='Frozen image-derived guide experiment; no model inference, automatic semantic approval, or finished portrait claim.',
            canonicalGeometry=self.canonical_engine.evidence())
        data['metrics']['method'] = 'Explicit image-derived guide experiment; whole physical ribbons on a shared canvas'
        if 'structureHints' in self.payload:
            data['structureHints'] = self.payload['structureHints']
        path.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')


core_proposal = core._proposal


def generate(source, guides, provenance, output, through=2):
    if through not in (1, 2):
        raise ValueError('The coarse experiment supports through 1 or 2 only')
    with _RUN_LOCK:
        payload, bindings, order, contracts, evidence = load_inputs(source, guides, provenance)
        output = Path(output)
        if output.exists():
            raise ValueError('Experiment output must be a new directory')
        painter = ExperimentalPainter(source, payload, order, contracts, bindings, output, evidence)
        painter.requested_through = through
        (output/'output').mkdir(parents=True)
        (output/'qa').mkdir()
        started = time.perf_counter()
        times = []
        for layer in range(through):
            before = time.perf_counter()
            complete = painter.paint_phase(layer)
            times.append(time.perf_counter()-before)
            data = json.loads((output/'qa'/'painting-data.json').read_text(encoding='utf-8'))
            (output/f'stage-{layer}-data.json').write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
            export_svg(data, output/f'frame-stage-{layer}.svg')
            if not complete:
                break
        Image.fromarray(painter.canvas).save(output/'canvas-stop.png')
        summary = dict(experimentVersion=EXPERIMENT_VERSION, sourceSHA256=evidence['sourceSHA256'],
            seconds=time.perf_counter()-started, stageSeconds=times, strokes=len(painter.strokes),
            requestedThrough=through, completedLayers=len([l for l in painter.layers if l['complete']]),
            blocked=getattr(painter, 'construction_blocked', None), semanticAcceptance=False,
            inputEvidence=evidence, layers=[{k:layer[k] for k in ('stage','complete','added','endIndex','mae')} for layer in painter.layers])
        (output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        (output/'proposal-trace.json').write_text(json.dumps(painter.proposal_trace, ensure_ascii=False, indent=2), encoding='utf-8')
        return painter
