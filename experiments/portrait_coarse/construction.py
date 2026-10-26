"""Experimental stage0; unchanged profile and proposal primitives live in core."""
from collections import Counter
import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d
import construction_episodes as core
from construction_episodes import (_sample, _profile, _probes, _fit_open_movement,
    _adaptive_radii, _segments, _geometry, _proposal, _assessment)
from . import energy
from .safety import select_initial_load


def _coarse_progress(before, after):
    return energy.progress(before, after)


def _preview_assessment(painter, proposal, opacity, target_profile, samples, offsets, positions, layer):
    trial = painter.canvas.copy()
    (x0, y0, x1, y1), alpha = proposal['footprint']
    alpha = alpha[:, :, None] * opacity
    trial[y0:y1, x0:x1] = np.rint(trial[y0:y1, x0:x1] * (1.-alpha) +
                                 np.asarray(proposal['color']) * alpha).astype(np.uint8)
    return _assessment(trial, target_profile, samples, offsets, positions, layer)


def _repair_templates(canvas, target_profile, samples, offsets, positions, points, radii, current, layer):
    """Read the unresolved open profile, then choose a broad load or its turn.

    The interval and lanes are brush parameters, never a closed paint region.
    Re-read after each accepted movement; do not replay the whole episode.
    """
    # Same continuous main-surface objective supplies both acceptance and repair
    # location; the old sigma16 RGB residual is not a transverse-lane target.
    station_error = np.asarray(current['coarseEnergy']['stationEnergy'])
    anchor = int(np.argmax(gaussian_filter1d(station_error, 1., mode='nearest')))
    distance = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    span = max(65., min(distance[-1], float(np.median(radii))*4., distance[-1]*.65))
    begin = max(0, int(np.searchsorted(distance, positions[anchor]-span*.5))-1)
    end = min(len(points), max(begin+3, int(np.searchsorted(distance, positions[anchor]+span*.5))+1))
    chunks = list(dict.fromkeys([(begin, end), (0, len(points))]))
    field=current['coarseEnergy']['repairField']
    plane_missing=field['occupancyDeficitStations'][anchor]>=field['whiteLeakStations'][anchor]
    intent = 'plane' if plane_missing else 'turn'
    # The worst normal-profile deficit locates the side to load. Width is
    # broad relative to the existing guide; it never collapses into pixel dabs.
    lane=float(np.clip(field['positiveDeficitLane' if plane_missing else 'negativeExcessLane'][anchor],-.95,.95))
    lanes = [(lane, .68), (0., .90), (-.48, .62), (.48, .62)] if plane_missing else [(lane, .40), (-.65, .38), (.65, .38)]
    templates = [dict(geometry=(offset, width, start, stop), intent=intent, opacity=.97,
                      fitLoad=False, repairRole='main_plane_width' if plane_missing else 'turn_lane')
                 for start, stop in chunks for offset, width in lanes]
    # Also consider a colour-compatible turn, but keep the broad untrimmed
    # alternatives available when fitting would shrink a main plane away.
    templates += [dict(geometry=(lane, .55, start, stop), intent=intent, opacity=.97,
                       fitLoad=True, repairRole='colour_load_width') for start, stop in chunks]
    return templates


def paint_construction_episode(painter, guide_index, layer=1, palette_size=28,
                               max_strokes=None, max_rounds=None, profile_only=False, feedback=None):
    """Paint one variable-length construction episode; return honest evidence.

    Required painter API: src, canvas, guides[(Nx2 points,N radii)],
    footprint(left,right), choose_color(target,xy,n), and
    put(points,radii,color,sample,layer,intent,opacity,group,proposal).

    Readable proxy => leave; budget => unfinished/revisit; no improvement =>
    unfinished/revisit. No fixed ten-stroke completion rule is used. The proxy
    concerns large planes and cross-sectional structure, not finished detail.
    ``profile_only`` measures the current canvas without painting.
    """
    if not 0 <= guide_index < len(painter.guides):
        raise ValueError('Unknown open guide index')
    original_points, original_radii = painter.guides[guide_index]
    points = np.asarray(original_points, dtype=float).copy()
    provided_radii = np.asarray(original_radii, dtype=float).copy()
    if len(points) < 3 or provided_radii.shape != (len(points),):
        raise ValueError('An open guide needs at least three points and matching radii')
    if not np.isfinite(points).all() or not np.isfinite(provided_radii).all():
        raise ValueError('Guide geometry must be finite')
    max_strokes = (14 if layer == 0 else 48) if max_strokes is None else max_strokes
    max_rounds = (1 if layer == 0 else 2) if max_rounds is None else max_rounds
    sigma = 16. if layer == 0 else 6. if layer == 1 else 3.
    target = cv2.GaussianBlur(painter.src, (0, 0), sigma)
    if getattr(painter, 'auto_guides', False):
        fitted_radii=provided_radii.copy()
        movement_fit=dict(mode='automatic image-derived geometry; no white-background refit')
    else:
        points, fitted_radii, movement_fit = _fit_open_movement(target, points, provided_radii)
    if layer == 0 and guide_index in getattr(painter,'geometry_ready',{}):
        # Ready means THIS exact final center/pressure geometry was measured.
        # Never invalidate its support certificate with another Gaussian.
        radii=fitted_radii.copy()
        movement_fit['finalSourceSupportGeometryContract']=painter.geometry_ready[guide_index]
    elif layer == 0:
        # Build the main plane as broad, smooth, uninterrupted paint first.
        # Fine color compatibility is deliberately irrelevant at this stage.
        step = float(np.median(np.linalg.norm(np.diff(points, axis=0), axis=1)))
        points = gaussian_filter1d(points, 22./max(1., step), axis=0, mode='nearest')
        radii = gaussian_filter1d(fitted_radii, 25./max(1., step), mode='nearest')
    else:
        radii = _adaptive_radii(target, points, fitted_radii)
    center, probe_radii, normal, offsets, samples, positions = _probes(points, radii)
    target_profile = _profile(target, samples)
    before = _assessment(painter.canvas, target_profile, samples, offsets, positions, layer)
    start_index = len(painter.strokes)
    report = dict(guideIndex=int(guide_index), layer=int(layer), startIndex=start_index,
                  before=before, history=[], accepted=0, attempted=0, rounds=0,
                  intentCounts={}, acceptedLengths=[], acceptedWidths=[], rejectedCoarse=0,
                  movementFit=movement_fit,
                  actualEvalSigma=sigma,
                  geometry='open longitudinal sweeps; whole physical brush footprints',
                  completionMeaning='Measured large-plane/profile correspondence only; not semantic recognition or finished detail.')
    if not before['coarseEnergy']['sourceSupportPresent']:
        report.update(status='unsupported_source', stopReason='source_support_unknown',
                      needsRevisit=True, after=before, endIndex=start_index)
        return report
    if profile_only or before['readableProxy']:
        report.update(status='readable_proxy' if before['readableProxy'] else 'measure_only',
                      needsRevisit=not before['readableProxy'], after=before, endIndex=start_index)
        return report

    segments = _segments(points, radii, target)
    intents = Counter()
    status = 'no_improvement'
    episode_number = int(getattr(painter, '_construction_episode_count', 0))
    painter._construction_episode_count = episode_number+1
    group = f'construction-{layer}-{episode_number}'
    current = before
    if feedback is not None:
        if feedback['guideIndex'] != guide_index or feedback['layer'] != layer or not feedback['needsRevisit']:
            raise ValueError('Repair feedback must describe this unfinished open movement')
        report['feedbackConsumed'] = dict(status=feedback['status'], after=feedback['after'])
        report['repairActions'] = []
        # Same five-mark continuation ceiling as v7.0, with a finite candidate
        # search and an explicit stall. No additional rounds or detail pass.
        for _ in range(min(max_strokes, 5)):
            candidates = []
            for template in _repair_templates(painter.canvas, target_profile, samples, offsets, positions,
                                               points, radii, current, layer):
                report['attempted'] += 1
                proposal = _proposal(painter, template, points, radii, target, palette_size)
                if proposal is None:
                    continue
                assessment = _preview_assessment(painter, proposal, template['opacity'], target_profile,
                                                 samples, offsets, positions, layer)
                progress = _coarse_progress(current, assessment)
                if progress < energy.MIN_REPAIR_GAIN:
                    report['rejectedCoarse'] += 1
                    continue
                candidates.append((progress, proposal, template, assessment))
            if not candidates:
                status = 'no_improvement'
                report['stopReason'] = 'no_safe_coarse_progress_on_current_open_axis'
                break
            _, proposal, template, assessment = max(candidates, key=lambda item: item[0])
            if not painter.put(proposal['points'], proposal['radii'], proposal['color'], proposal['sourceSample'],
                               layer, template['intent'], template['opacity'], group, proposal['footprint']):
                status = 'no_improvement'
                report['stopReason'] = 'brush_not_paintable'
                break
            current = assessment
            report['accepted'] += 1
            intents[template['intent']] += 1
            report['acceptedLengths'].append(proposal['length'])
            report['acceptedWidths'].append(float(2*proposal['radii'].max()))
            report['repairActions'].append(dict(role=template['repairRole'], geometry=list(template['geometry'])))
            report['history'].append(dict(afterAccepted=report['accepted'], intent=template['intent'], **current))
            if current['readableProxy']:
                status = 'readable_proxy'
                report['stopReason'] = 'coarse_profile_ready'
                break
        else:
            status = 'budget_unfinished'
            report['stopReason'] = 'bounded_repair_budget'
        report.update(status=status, needsRevisit=status != 'readable_proxy', after=current,
                      endIndex=len(painter.strokes), intentCounts=dict(intents))
        return report
    for round_index in range(max_rounds):
        report['rounds'] += 1
        scale = 1. if round_index == 0 else .78 if round_index == 1 else .58
        chunks = [(0, len(points))] if round_index == 0 else []
        chunks += segments
        # Main planes are developed first, then adjacent turns, then open
        # environmental corrections. These are brush lanes, not image borders.
        phases = [
            ('plane', [(0., .67), (-.46, .54), (.46, .54)], .97),
            ('turn', [(-.76, .33), (.76, .33), (-.25, .28), (.25, .28)], .92),
            ('plane', [(-1.08, .29), (1.08, .29)], .97),
        ]
        accepted_this_round = 0
        for phase_number, (intent, lanes, opacity) in enumerate(phases):
            phase_chunks = [(0, len(points))] if layer == 0 and phase_number == 0 else chunks
            if layer == 0 and phase_number == 1:
                lanes = [(-.60, .38), (.60, .38)]
            templates = [dict(geometry=(lane, width*scale, begin, end), intent=intent,
                              opacity=opacity, fitLoad=not(layer == 0 and phase_number == 0))
                         for begin, end in phase_chunks for lane, width in lanes]
            accepted_in_phase = 0
            while templates:
                if report['accepted'] >= max_strokes:
                    status = 'budget_unfinished'; break
                # Read current canvas again: residual rejection cannot silently
                # turn a planned long movement into three isolated accepted dabs.
                candidates = []
                for index, template in enumerate(templates):
                    proposal = _proposal(painter, template, points, radii, target, palette_size)
                    report['attempted'] += 1
                    if proposal is not None:
                        assessed=_preview_assessment(painter,proposal,template['opacity'],target_profile,
                                                     samples,offsets,positions,layer)
                        selected = select_initial_load(
                            proposal, assessed, current=current, template=template,
                            contract=getattr(painter, 'geometry_ready', {}).get(guide_index, {}),
                            layer=layer, phase=phase_number,
                            propose_fitted=lambda fitted: _proposal(painter, fitted, points, radii, target, palette_size),
                            assess=lambda fitted: _preview_assessment(painter, fitted, template['opacity'],
                                target_profile, samples, offsets, positions, layer), report=report)
                        if selected is None:
                            continue
                        proposal, assessed, selected_fit_load = selected
                        progress=_coarse_progress(current,assessed)
                        candidates.append((progress,proposal['priority'],-index,index,proposal,assessed,selected_fit_load))
                if not candidates:
                    break
                progress,_,_,index,proposal,assessed,selected_fit_load=max(candidates,key=lambda item:(item[0],item[1],item[2]))
                if progress<energy.MIN_INITIAL_GAIN:
                    report['rejectedCoarse']+=len(candidates)
                    break
                template=templates.pop(index)
                accepted = painter.put(proposal['points'], proposal['radii'], proposal['color'],
                                       proposal['sourceSample'], layer, template['intent'],
                                       template['opacity'], group, proposal['footprint'])
                if not accepted:
                    continue
                report['accepted'] += 1; accepted_this_round += 1
                accepted_in_phase += 1
                intents[template['intent']] += 1
                report['acceptedLengths'].append(proposal['length'])
                report['acceptedWidths'].append(float(2*proposal['radii'].max()))
                current = assessed
                report['history'].append(dict(afterAccepted=report['accepted'], intent=template['intent'],
                                             selectedFitLoad=selected_fit_load, **current))
                if current['readableProxy']:
                    status = 'readable_proxy'; break
                if layer == 0 and accepted_in_phase >= (3 if phase_number != 1 else 4):
                    # A coarse episode has a small work budget per paint role.
                    # Running out is unfinished, never a completion criterion.
                    break
            if status in ('readable_proxy', 'budget_unfinished'):
                break
        if status in ('readable_proxy', 'budget_unfinished'):
            break
        if accepted_this_round == 0:
            status = 'no_improvement'; break
        status = 'budget_unfinished' if round_index == max_rounds-1 else 'no_improvement'
    report.update(status=status, needsRevisit=status != 'readable_proxy', after=current,
                  endIndex=len(painter.strokes), intentCounts=dict(intents),
                  guideLength=float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum()),
                  radiusRatioRange=[float(np.min(radii/np.maximum(provided_radii, .001))),
                                    float(np.max(radii/np.maximum(provided_radii, .001)))])
    return report


def strict_sequence(painter,layer,episode):
    """Unready + no safe repair is blocked, never a successful transfer."""
    for i in range(len(painter.guides)):
        report=episode(painter,i,layer=layer,palette_size=12)
        painter.construction_reports.append(report)
        if report['needsRevisit']:
            follow=episode(painter,i,layer=layer,palette_size=28,max_strokes=5,max_rounds=1,feedback=report)
            follow['continuationOf']=len(painter.construction_reports)-1
            painter.construction_reports.append(follow);report=follow
        report['transitionDecision']='main_surface_ready' if not report['needsRevisit'] else 'blocked_unresolved'
        if report['needsRevisit']:
            reason = ('source_support_unknown' if report['status']=='unsupported_source' else
                      'no_safe_coarse_energy_progress_before_main_surface_ready' if report['status']=='no_improvement' else
                      'repair_budget_exhausted_before_main_surface_ready')
            painter.construction_blocked=dict(layer=layer,guideIndex=i,reportIndex=len(painter.construction_reports)-1,
                reason=reason)
            return False
    return True
