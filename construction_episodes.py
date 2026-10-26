"""Develop one open gesture until its large form is supported by the canvas.

An episode is an interval of attention, not an image segment. All measurements
are one-dimensional samples along/normal to an open guide. They never become a
closed boundary, a clipping path, or a map of pixel ownership. Every accepted
physical brush footprint is painted in full by the supplied painter.
"""
import math
from collections import Counter

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d


def _frame(points):
    tangent = np.gradient(points, axis=0)
    tangent /= np.linalg.norm(tangent, axis=1)[:, None] + 1.e-9
    return np.column_stack((-tangent[:, 1], tangent[:, 0]))


def _sample(image, points):
    return cv2.remap(image.astype(np.float32), points[..., 0].astype(np.float32),
                     points[..., 1].astype(np.float32), cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REPLICATE)


def _probes(points, radii):
    distance = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    count = max(12, min(90, int(distance[-1] / 10) + 1))
    locations = np.linspace(distance[-1]*.035, distance[-1]*.965, count)
    center = np.column_stack([np.interp(locations, distance, points[:, axis]) for axis in (0, 1)])
    rr = np.interp(locations, distance, radii)
    # Loaded-brush end caps must not determine the anatomical sampling scale.
    rr = np.maximum(rr, np.percentile(radii, 60)*.32)
    normal = _frame(center)
    offsets = np.linspace(-1.30, 1.30, 35)
    samples = center[:, None, :] + normal[:, None, :]*rr[:, None, None]*offsets[None, :, None]
    return center, rr, normal, offsets, samples, locations


def _profile(image, samples):
    values = _sample(image, samples)
    values = gaussian_filter1d(values, 1.2, axis=1, mode='nearest')
    return gaussian_filter1d(values, .65, axis=0, mode='nearest')


def _longest_fraction(good, positions):
    if len(good) < 2:
        return float(bool(good.all()))
    weights = np.r_[np.diff(positions), np.diff(positions)[-1]]
    longest = current = 0.
    for valid, weight in zip(good, weights):
        current = current + weight if valid else 0.
        longest = max(longest, current)
    return float(longest / max(1.e-9, weights.sum()))


def _assessment(canvas, target_profile, samples, offsets, positions, layer=1):
    sigma = 16. if layer == 0 else 6. if layer == 1 else 3.
    actual = _profile(cv2.GaussianBlur(canvas, (0, 0), sigma), samples)
    weights = np.exp(-.5*(offsets/.85)**2)
    error = np.average(np.abs(actual-target_profile).mean(axis=2), axis=1, weights=weights)
    baseline = np.average(np.abs(255.-target_profile).mean(axis=2), axis=1, weights=weights)
    progress = 1.-error/(baseline+4.)
    good = (error <= (16. if layer == 0 else 11.)) | (progress >= (.75 if layer == 0 else .78))
    source_luma = target_profile @ np.array([.2126, .7152, .0722])
    actual_luma = actual @ np.array([.2126, .7152, .0722])
    derivative = np.diff(source_luma, axis=1)
    painted_derivative = np.diff(actual_luma, axis=1)
    amplitude = np.ptp(source_luma, axis=1)
    meaningful = amplitude >= 16.
    dot = (derivative*painted_derivative).sum(axis=1)
    correlation = dot/(np.linalg.norm(derivative, axis=1)*
                     np.linalg.norm(painted_derivative, axis=1)+1.e-6)
    contrast_error = np.abs(np.ptp(actual_luma, axis=1)-amplitude)
    structural_good = (correlation >= .52) & (contrast_error <= np.maximum(14., amplitude*.48))
    structure_fraction = float(structural_good[meaningful].mean()) if meaningful.any() else 1.
    fraction = float(good.mean())
    longest = _longest_fraction(good, positions)
    # This is explicitly a source/canvas profile proxy, not semantic vision.
    readable = (fraction >= .78 and longest >= .55 and
                float(error.mean()) <= (17. if layer == 0 else 13.5) and
                structure_fraction >= (.40 if layer == 0 else .58))
    return dict(readableProxy=bool(readable), meanProfileMAE=float(error.mean()),
                stationFractionReady=fraction, longestContinuousFraction=longest,
                meanWhiteErrorReduction=float(progress.mean()),
                meaningfulContrastStations=int(meaningful.sum()),
                contrastCorrespondence=structure_fraction,
                meanSignedGradientAgreement=float(correlation[meaningful].mean()) if meaningful.any() else None)


def _fit_open_movement(source, points, radii):
    """Move brush centers and loads using normal-profile activity moments.

    These are per-station brush parameters. No left/right silhouette is traced
    or joined, and the image remains equally paintable on either side.
    """
    normal = _frame(points)
    relative = np.linspace(-2.15, 2.15, 57)
    query = points[:, None, :] + normal[:, None, :]*radii[:, None, None]*relative[None, :, None]
    values = _profile(source, query)
    signal = np.linalg.norm(255.-values, axis=2)/math.sqrt(3.)
    activity = 1.-np.exp(-np.maximum(0., signal-4.)/22.)
    weights = activity*np.exp(-.5*(relative[None, :]/1.25)**2)
    mass = weights.sum(axis=1)
    mean = (weights*relative).sum(axis=1)/np.maximum(1.e-6, mass)
    variance = (weights*(relative[None, :]-mean[:, None])**2).sum(axis=1)/np.maximum(1.e-6, mass)
    step = float(np.median(np.linalg.norm(np.diff(points, axis=0), axis=1)))
    along_sigma = 30./max(1., step)
    shift = gaussian_filter1d(np.clip(mean, -.85, .85), along_sigma, mode='nearest')
    width = gaussian_filter1d(np.clip(np.sqrt(3.*variance)*.88, .38, 1.15), along_sigma, mode='nearest')
    # The tiny pressure cap follows its neighboring loaded movement.
    cap = np.minimum(1., radii/max(1., float(np.percentile(radii, 60))*.35))
    shifted = points+normal*(shift*radii*cap)[:, None]
    return shifted, radii*width, dict(maxCenterShift=float(np.max(np.abs(shift*radii))),
                                      widthFactorRange=[float(width.min()), float(width.max())])


def _fit_color_load(target, path, radii, color):
    """Fit one open brush load to its low-frequency normal color profiles.

    Color-compatible widths are brush parameters only. Two sides are never
    exported as a body outline, nor used to suppress any footprint pixels.
    """
    normal = _frame(path)
    relative = np.linspace(-1.45, 1.45, 37)
    query = path[:, None, :]+normal[:, None, :]*radii[:, None, None]*relative[None, :, None]
    values = _profile(target, query)
    error = np.linalg.norm(values-np.asarray(color), axis=2)/math.sqrt(3.)
    # A continuous dynamic program follows a paintable plane through the open
    # strip. Its position prior prevents chasing an unrelated nearby surface.
    local = error**2+relative[None, :]**2*95.
    transition = (relative[:, None]-relative[None, :])**2*850.
    back = np.empty(local.shape, dtype=np.int16)
    cost = local[0]
    for row in range(1, len(path)):
        combined = cost[:, None]+transition
        previous = combined.argmin(axis=0)
        back[row] = previous
        cost = local[row]+combined[previous, np.arange(len(relative))]
    selected = np.empty(len(path), dtype=int)
    selected[-1] = int(cost.argmin())
    for row in range(len(path)-1, 0, -1):
        selected[row-1] = back[row, selected[row]]
    left, right = np.empty(len(path)), np.empty(len(path))
    quality = error[np.arange(len(path)), selected]
    for row, column in enumerate(selected):
        tolerance = max(13., min(30., float(error[row, column])+7.))
        lo = hi = int(column)
        while lo and error[row, lo-1] <= tolerance:
            lo -= 1
        while hi < len(relative)-1 and error[row, hi+1] <= tolerance:
            hi += 1
        left[row], right[row] = relative[lo], relative[hi]
    step = float(np.median(np.linalg.norm(np.diff(path, axis=0), axis=1)))
    raw_width = np.maximum(0., (right-left)*.50)
    displacement = gaussian_filter1d((left+right)*.5, 18./max(1., step), mode='nearest')
    load_width = gaussian_filter1d(raw_width, 20./max(1., step), mode='nearest')
    fitted = path+normal*(displacement*radii)[:, None]
    fitted_radii = np.maximum(.7, radii*load_width)
    # A color absent for a sustained length cannot be joined by a fabricated
    # hairline. Keep the longest compatible open interval of this brush load.
    can_load = (quality < 39.) & (raw_width*radii >= np.maximum(2.4, radii*.22))
    good = gaussian_filter1d(can_load.astype(float), .65) >= .55
    runs, start = [], None
    for index, valid in enumerate(np.r_[good, False]):
        if valid and start is None:
            start = index
        elif not valid and start is not None:
            runs.append((start, index)); start = None
    if not runs:
        return None
    start, stop = max(runs, key=lambda pair: pair[1]-pair[0])
    if stop-start < 3:
        return None
    fitted, fitted_radii = fitted[start:stop], fitted_radii[start:stop]
    distance = np.r_[0., np.cumsum(np.linalg.norm(np.diff(fitted, axis=0), axis=1))]
    if distance[-1] < 32.:
        return None
    # A new fragment lands with paint already loaded. It does not grow a long
    # hairline into a fat head or connect through a color-incompatible interval.
    cap = .55+.45*np.clip(np.minimum(distance, distance[-1]-distance)/22., 0., 1.)
    fitted_radii = fitted_radii*cap
    for index in range(1, len(fitted_radii)):
        fitted_radii[index] = min(fitted_radii[index], fitted_radii[index-1]+.30*(distance[index]-distance[index-1]))
    for index in range(len(fitted_radii)-2, -1, -1):
        fitted_radii[index] = min(fitted_radii[index], fitted_radii[index+1]+.30*(distance[index+1]-distance[index]))
    nn = _frame(fitted)
    fitted = np.round(fitted, 3)
    return fitted, fitted_radii, np.round(fitted+nn*fitted_radii[:, None], 3), np.round(fitted-nn*fitted_radii[:, None], 3)


def _adaptive_radii(source, points, radii):
    """Use normal low-frequency contrast to soften width, never cut a stroke."""
    normal = _frame(points)
    relative = np.linspace(-1.05, 1.05, 27)
    samples = points[:, None, :] + normal[:, None, :]*radii[:, None, None]*relative[None, :, None]
    profile = _profile(source, samples)
    differences = np.linalg.norm(np.diff(profile, axis=1), axis=2)
    center = len(relative)//2
    factors = np.ones(len(points))
    for index, row in enumerate(differences):
        threshold = max(8., float(np.percentile(row, 75))*1.5)
        peaks = np.flatnonzero(row > threshold)
        if len(peaks):
            distance = np.min(np.abs(peaks-center+.5))/center
            # Interior folds cannot collapse a large plane into little dots.
            factors[index] = np.clip(distance*1.15, .46, 1.)
    factors = gaussian_filter1d(factors, 1.5, mode='nearest')
    return radii*factors


def _segments(points, radii, source, maximum=320., minimum=95.):
    """Overlapping long color-load intervals along a single open movement."""
    distance = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    colors = gaussian_filter1d(_sample(source, points[:, None, :])[:, 0, :], 2., axis=0, mode='nearest')
    result, start = [], 0
    while start < len(points)-2:
        last = start+1
        for stop in range(start+2, len(points)):
            length = distance[stop]-distance[start]
            last = stop
            color_span = np.linalg.norm(colors[stop]-colors[start])
            if length >= maximum or (length >= minimum and color_span > 44.):
                break
        if last-start >= 2:
            result.append((start, last+1))
        if last >= len(points)-1:
            break
        # Neighboring long marks overlap longitudinally instead of leaving
        # disconnected stamps when the brush load needs to change.
        desired = distance[last]-max(22., float(np.median(radii[start:last+1]))*.8)
        start = max(start+1, int(np.searchsorted(distance, desired)))
    return result or [(0, len(points))]


def _geometry(points, radii, lane, width_factor, begin, end):
    normal = _frame(points)
    path = points[begin:end] + normal[begin:end]*radii[begin:end, None]*lane
    rr = radii[begin:end]*width_factor
    if len(path) < 3:
        return None
    # Local segment caps are physical pressure/geometry, not hidden clipping.
    cap = np.interp(np.linspace(0, 1, len(path)), [0, .08, .90, 1], [.38, 1., 1., .30])
    rr = np.maximum(.65, rr*cap)
    path = np.round(path, 3)
    nn = _frame(path)
    left = np.round(path+nn*rr[:, None], 3)
    right = np.round(path-nn*rr[:, None], 3)
    return path, rr, left, right


def _proposal(painter, template, points, radii, target, palette_size):
    geometry = _geometry(points, radii, *template['geometry'])
    if geometry is None:
        return None
    path, rr, left, right = geometry
    footprint = painter.footprint(left, right)
    if footprint is None:
        return None
    (x0, y0, x1, y1), alpha = footprint
    area = float(alpha.sum())
    if area < 2.:
        return None
    truth = target[y0:y1, x0:x1].astype(float)
    wanted = (truth*alpha[:, :, None]).sum(axis=(0, 1))/area
    color, source_sample = painter.choose_color(wanted, None, palette_size)
    if template.get('fitLoad', True):
        geometry = _fit_color_load(target, path, rr, color)
        if geometry is None:
            return None
        path, rr, left, right = geometry
        footprint = painter.footprint(left, right)
        if footprint is None:
            return None
        (x0, y0, x1, y1), alpha = footprint
        area = float(alpha.sum())
        truth = target[y0:y1, x0:x1].astype(float)
    old = painter.canvas[y0:y1, x0:x1].astype(float)
    opacity = template['opacity']
    new = old*(1-alpha[:, :, None]*opacity)+np.asarray(color)*alpha[:, :, None]*opacity
    # Protect correct light surroundings softly. The entire brush is accepted
    # or rejected, and no pixels within the footprint are selectively removed.
    old_error = ((old-truth)**2).mean(axis=2)
    protection = 1.+2.*np.exp(-old_error/70.)*(truth.mean(axis=2)/255.)**10
    gain = float((((old-truth)**2-(new-truth)**2)*protection[:, :, None]).sum())
    if gain <= max(10., area*1.8):
        return None
    length = float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum())
    return dict(points=path, radii=rr, color=color, sourceSample=source_sample,
                footprint=footprint, gain=gain, area=area, length=length,
                priority=gain/(area**.45)*(1.+min(1., length/240.)*.22))


def _coarse_progress(before, after):
    """Pixel gain alone must not purchase a worse large plane or turn."""
    keys = ('stationFractionReady', 'longestContinuousFraction', 'contrastCorrespondence')
    if after['meanProfileMAE'] > before['meanProfileMAE'] + 1.e-7:
        return 0.
    if any(after[key] < before[key] - 1.e-7 for key in keys):
        return 0.
    return (before['meanProfileMAE'] - after['meanProfileMAE'] +
            sum(6. * (after[key] - before[key]) for key in keys))


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
    sigma = 16. if layer == 0 else 6.
    actual = _profile(cv2.GaussianBlur(canvas, (0, 0), sigma), samples)
    residual = np.abs(actual-target_profile).mean(axis=2)
    station_error = residual.mean(axis=1)
    anchor = int(np.argmax(gaussian_filter1d(station_error, 1., mode='nearest')))
    distance = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    span = max(65., min(distance[-1], float(np.median(radii))*4., distance[-1]*.65))
    begin = max(0, int(np.searchsorted(distance, positions[anchor]-span*.5))-1)
    end = min(len(points), max(begin+3, int(np.searchsorted(distance, positions[anchor]+span*.5))+1))
    chunks = list(dict.fromkeys([(begin, end), (0, len(points))]))
    plane_missing = (current['stationFractionReady'] < .78 or current['longestContinuousFraction'] < .55 or
                     current['meanProfileMAE'] > (17. if layer == 0 else 13.5))
    intent = 'plane' if plane_missing else 'turn'
    # The worst normal-profile deficit locates the side to load. Width is
    # broad relative to the existing guide; it never collapses into pixel dabs.
    across = gaussian_filter1d(residual[max(0, anchor-1):anchor+2].mean(axis=0), 2.)
    lane = float(np.clip(offsets[int(across.argmax())], -.95, .95))
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
    if layer == 0:
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
                if progress < .08:
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
                        candidates.append((proposal['priority'], -index, index, proposal))
                if not candidates:
                    break
                _, _, index, proposal = max(candidates, key=lambda value: (value[0], value[1]))
                template = templates.pop(index)
                assessed = _preview_assessment(painter, proposal, template['opacity'], target_profile,
                                               samples, offsets, positions, layer)
                if _coarse_progress(current, assessed) < .02:
                    report['rejectedCoarse'] += 1
                    continue
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
                report['history'].append(dict(afterAccepted=report['accepted'], intent=template['intent'], **current))
                turn_required = current['meaningfulContrastStations'] >= max(3, len(positions)*.25)
                if current['readableProxy'] and (not turn_required or intents['turn'] >= 1):
                    status = 'readable_proxy'; break
                if (phase_number == 0 and current['stationFractionReady'] >= .76 and
                        current['longestContinuousFraction'] >= .55):
                    # Main plane is continuous: proceed to its major turn now,
                    # instead of exhausting all same-axis color refinements.
                    break
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
