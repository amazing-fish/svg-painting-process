"""Pixel-derived open painting movements; no reference-composition templates.

The temporary colour/support arrays in this module are *analysis* fields only.
They are discarded after extracting open centre lines and brush radii. Nothing
here clips paint, assigns a pixel to a body part, or produces a paint mask.

This is a classical-vision approximation to large visual masses, not a trained
human-pose or anatomy recognizer. The caller must preserve that distinction.
Dependencies: numpy, scipy, opencv-python.
"""
from __future__ import annotations

import math
import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree


def _thin(mask):
    """Zhang--Suen thinning, implemented locally to avoid extra dependencies."""
    image = np.pad(mask.astype(np.uint8), 1)
    for _ in range(max(mask.shape)):
        changed = False
        for phase in (0, 1):
            p = image[1:-1, 1:-1]
            n = [image[:-2, 1:-1], image[:-2, 2:], image[1:-1, 2:],
                 image[2:, 2:], image[2:, 1:-1], image[2:, :-2],
                 image[1:-1, :-2], image[:-2, :-2]]
            neighbors = sum(n)
            transitions = sum((n[i] == 0) & (n[(i+1) % 8] == 1) for i in range(8))
            remove = ((p == 1) & (neighbors >= 2) & (neighbors <= 6) & (transitions == 1))
            if phase == 0:
                remove &= (n[0]*n[2]*n[4] == 0) & (n[2]*n[4]*n[6] == 0)
            else:
                remove &= (n[0]*n[2]*n[6] == 0) & (n[0]*n[4]*n[6] == 0)
            if remove.any():
                p[remove] = 0
                changed = True
        if not changed:
            break
    return image[1:-1, 1:-1].astype(bool)


def _pca_path(mask, distance):
    yx = np.argwhere(mask)
    if len(yx) < 8:
        return None
    xy = yx[:, ::-1].astype(float)
    weights = np.maximum(.25, distance[mask].astype(float))
    center = np.average(xy, axis=0, weights=weights)
    covariance = ((xy-center)*weights[:, None]).T @ (xy-center)/weights.sum()
    values, vectors = np.linalg.eigh(covariance)
    axis = vectors[:, -1]
    normal = np.array([-axis[1], axis[0]])
    along = (xy-center) @ axis
    lo, hi = np.percentile(along, [4., 96.])
    if hi-lo < 5.:
        return None
    t = np.linspace(lo, hi, max(5, int((hi-lo)/3.)))
    points = center+axis*t[:, None]
    across = np.abs((xy-center) @ normal)
    radius = max(1.5, float(np.percentile(across, 65)))
    # Use broad cross sections rather than the distance at the centre pixel:
    # eyes/highlights inside a face must not make its base axis snake around
    # them or collapse its load to a hairline.
    radii = np.full(len(points), radius)
    half_bin = max(3., (hi-lo)/max(5., len(points)*.4))
    for index, position in enumerate(t):
        local = np.abs(along-position) <= half_bin
        if local.sum() >= 5:
            radii[index] = np.clip(np.percentile(across[local], 70), radius*.45, radius*1.15)
    radii = gaussian_filter1d(radii, 2., mode='nearest')
    return points, radii


def _graph_path(skeleton, distance, allow):
    yx = np.argwhere(skeleton & allow)
    if len(yx) < 5:
        return None
    ids = np.full(skeleton.shape, -1, np.int32)
    ids[yx[:, 0], yx[:, 1]] = np.arange(len(yx))
    rows, cols, lengths = [], [], []
    h, w = skeleton.shape
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        q = yx+np.array([dy, dx])
        inside = (q[:, 0] >= 0) & (q[:, 0] < h) & (q[:, 1] >= 0) & (q[:, 1] < w)
        start = np.flatnonzero(inside)
        end = ids[q[inside, 0], q[inside, 1]]
        valid = end >= 0
        start, end = start[valid], end[valid]
        rows.extend(start); cols.extend(end)
        lengths.extend(np.full(len(start), math.hypot(dx, dy)))
        rows.extend(end); cols.extend(start)
        lengths.extend(np.full(len(start), math.hypot(dx, dy)))
    if not rows:
        return None
    graph = csr_matrix((lengths, (rows, cols)), shape=(len(yx), len(yx)))
    seed = int(np.argmax(distance[yx[:, 0], yx[:, 1]]))
    first = dijkstra(graph, indices=seed)
    first[~np.isfinite(first)] = -1
    beginning = int(first.argmax())
    distances, previous = dijkstra(graph, indices=beginning, return_predecessors=True)
    distances[~np.isfinite(distances)] = -1
    end = int(distances.argmax())
    if distances[end] < 7.:
        return None
    path = [end]
    while path[-1] != beginning:
        parent = int(previous[path[-1]])
        if parent < 0 or len(path) > len(yx):
            return None
        path.append(parent)
    points = yx[path[::-1], ::-1].astype(float)
    radii = distance[yx[path[::-1], 0], yx[path[::-1], 1]].astype(float)
    return points, radii


def _clean_path(points, radii, shape):
    if len(points) < 3:
        return None
    # A broad loaded brush follows volume, not every turn of a pixel skeleton.
    # Thin ribbon axes retain their bends; thick planes get stronger smoothing.
    step = max(.5, float(np.median(np.linalg.norm(np.diff(points, axis=0), axis=1))))
    sigma = max(2., min(14., float(np.median(radii))*.70)/step)
    points = gaussian_filter1d(points, sigma, axis=0, mode='nearest')
    radii = gaussian_filter1d(radii, 2.8, mode='nearest')
    lengths = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    length = float(lengths[-1])
    if length < max(11., min(shape)*.055):
        return None
    # Resampling keeps the output sparse but does not turn it into contours.
    positions = np.linspace(0., length, max(5, int(length/5.)+1))
    points = np.column_stack([np.interp(positions, lengths, points[:, k]) for k in (0, 1)])
    radii = np.interp(positions, lengths, radii)
    radii = np.clip(radii*1.08, 1.6, min(shape)*.24)
    if points[-1, 1] < points[0, 1] or (abs(points[-1, 1]-points[0, 1]) < 3 and points[-1, 0] < points[0, 0]):
        points, radii = points[::-1], radii[::-1]
    return points, radii, length


def _candidate_paths(mask, origin, background, max_paths=3):
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    candidates = []
    minimum_area = max(55, int(mask.size*.0013))
    for index in np.argsort(stats[1:, cv2.CC_STAT_AREA])[::-1][:12]+1:
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < minimum_area:
            continue
        component = labels == index
        distance = cv2.distanceTransform(np.pad(component.astype(np.uint8), 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
        coordinates = np.argwhere(component)[:, ::-1].astype(float)
        covariance = np.cov(coordinates, rowvar=False)
        eigenvalues = np.maximum(np.linalg.eigvalsh(covariance), .01)
        # Compact colour masses (head, cheek, chest, broad cloth or backdrop)
        # are laid in along their principal volume axis. A medial skeleton of
        # such a mass otherwise circles its internal dark/light holes.
        compact = bool(eigenvalues[-1]/eigenvalues[0] < 4.5)
        skeleton = _thin(component)
        allow = np.ones(component.shape, bool)
        for iteration in range(max_paths):
            result = _pca_path(component, distance) if compact else _graph_path(skeleton, distance, allow)
            if result is None:
                if iteration == 0:
                    result = _pca_path(component, distance)
                if result is None:
                    break
            raw_points, raw_radii = result
            raw_length = float(np.linalg.norm(np.diff(raw_points, axis=0), axis=1).sum())
            chord = float(np.linalg.norm(raw_points[-1]-raw_points[0]))
            slenderness = raw_length/max(1., 2.*float(np.median(raw_radii)))
            # A wide mass must not make a U-turn simply because two nearby
            # pigment islands touch. True long, narrow ribbons keep their arc.
            if not compact and raw_length/max(1., chord) > 1.65 and slenderness < 15.:
                principal = _pca_path(component, distance)
                if principal is not None:
                    result = principal
                    compact = True
            cleaned = _clean_path(*result, mask.shape)
            if cleaned is None:
                break
            points, radii, length = cleaned
            mean_radius = float(np.mean(radii))
            # Isolated hairs, jewelry and droplets are detail, not main masses.
            if mean_radius < max(2., min(mask.shape)*.008) or length < mean_radius*1.6:
                break
            candidates.append(dict(points=points, radii=radii, length=length,
                                   area=area, background=bool(background), origin=origin,
                                   score=float(length*mean_radius**.85)))
            if compact:
                break
            yy, xx = np.indices(mask.shape)
            lookup = cKDTree(points)
            dist, nearest = lookup.query(np.column_stack((xx.ravel(), yy.ravel())))
            covered = dist.reshape(mask.shape) < np.maximum(4., radii[nearest].reshape(mask.shape)*.70)
            allow &= ~covered
    return candidates


def _deduplicate(candidates, limit):
    selected = []
    for candidate in sorted(candidates, key=lambda item: -item['score']):
        points, radii = candidate['points'], candidate['radii']
        duplicate = False
        for existing in selected:
            if candidate['background'] != existing['background']:
                continue
            distances, _ = cKDTree(existing['points']).query(points)
            threshold = np.minimum(np.mean(radii), np.mean(existing['radii']))*.55+1.
            if np.mean(distances < threshold) > .73:
                duplicate = True
                break
        if not duplicate:
            selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def _ordered(candidates, focus, diagonal):
    """Big masses first, then nearby connected movements, entirely data-derived."""
    remaining = list(candidates)
    result = []
    cursor = np.asarray(focus, float)
    for background in (True, False):
        pool = [item for item in remaining if item['background'] == background]
        while pool:
            score_scale = max(item['score'] for item in pool)
            def priority(item):
                near = min(np.linalg.norm(item['points'][0]-cursor),
                           np.linalg.norm(item['points'][-1]-cursor))/diagonal
                return item['score']/score_scale-.85*near
            chosen = max(pool, key=priority)
            pool = [item for item in pool if item is not chosen]
            points = chosen['points']
            if np.linalg.norm(points[-1]-cursor) < np.linalg.norm(points[0]-cursor):
                chosen = dict(chosen, points=points[::-1], radii=chosen['radii'][::-1])
            result.append(chosen)
            cursor = chosen['points'][-1]
    return result


def extract_guides(rgb, max_guides=48):
    """Return ``[(Nx2 points, N radii), ...]`` in source-pixel coordinates.

    Every movement, width and ordering decision is computed from input pixels.
    No remote model service is used. A bundled pretrained Haar face detector
    provides optional attention hints only. Portraits/full figures with
    complex backgrounds remain an experimental classical-vision use case.
    """
    source = np.asarray(rgb)
    if source.ndim != 3 or source.shape[2] < 3 or min(source.shape[:2]) < 8:
        raise ValueError('Expected an RGB image of at least 8 by 8 pixels')
    source = np.clip(source[..., :3], 0, 255).astype(np.uint8)
    h, w = source.shape[:2]
    scale = min(1., 384./max(h, w))
    working = cv2.resize(source, (max(8, round(w*scale)), max(8, round(h*scale))), interpolation=cv2.INTER_AREA)
    ah, aw = working.shape[:2]
    smooth = cv2.GaussianBlur(working, (0, 0), max(.8, min(ah, aw)*.007))
    lab = cv2.cvtColor(smooth.astype(np.float32)/255., cv2.COLOR_RGB2LAB)
    flat = lab.reshape(-1, 3)
    stride = max(1, len(flat)//18000)
    samples = flat[::stride].copy()
    cv2.setRNGSeed(7129)
    color_count = min(7, max(2, int(np.std(flat, axis=0).sum()/9.)+2))
    _, _, centers = cv2.kmeans(samples, color_count, None,
                            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 32, .12),
                            3, cv2.KMEANS_PP_CENTERS)
    differences = ((lab[:, :, None, :]-centers[None, None, :, :])**2).sum(axis=3)
    labels = differences.argmin(axis=2)
    border = np.zeros((ah, aw), bool)
    border_width = max(2, round(min(ah, aw)*.035))
    border[:border_width] = border[-border_width:] = True
    border[:, :border_width] = border[:, -border_width:] = True
    border_fractions = np.bincount(labels[border], minlength=color_count)/border.sum()
    bg_index = int(border_fractions.argmax())
    bg_rgb = np.median(smooth[labels == bg_index], axis=0)
    pale_background = bool(np.min(bg_rgb) > 238. and np.ptp(bg_rgb) < 17.)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    candidates = []
    for color in range(color_count):
        background = bool(border_fractions[color] > .24 and np.mean(labels == color) > .16)
        if background and pale_background and np.min(np.median(smooth[labels == color], axis=0)) > 238.:
            continue
        mask = (labels == color).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        candidates.extend(_candidate_paths(mask, f'low-frequency-colour-{color}', background, 3))
    # A second, overlapping scale joins neighboring pigments into broad masses.
    # It is an analysis hint only and is never exported as a clipping boundary.
    background_distance = np.sqrt(((lab-centers[bg_index])**2).sum(axis=2))
    if border_fractions[bg_index] > .40:
        threshold = max(8., float(np.percentile(background_distance[border], 60))+3.)
        activity = (background_distance > threshold).astype(np.uint8)
        activity = cv2.morphologyEx(activity, cv2.MORPH_CLOSE, kernel, iterations=2)
        candidates.extend(_candidate_paths(activity, 'overlapping-large-mass', False, 4))
    candidates = _deduplicate(candidates, max_guides)
    low_confidence = len(candidates) < 4
    if len(candidates) < 4:
        # An input with few visible axes still gets an image-driven full-canvas
        # lay-in, using the image covariance rather than a body-coordinate grid.
        gray = cv2.cvtColor(smooth, cv2.COLOR_RGB2GRAY)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
        horizontal = float(np.mean(gx*gx)) > float(np.mean(gy*gy))
        bands = 4
        for i in range(bands):
            if horizontal:
                x = aw*(i+.5)/bands
                points = np.column_stack((np.full(7, x), np.linspace(0, ah-1, 7)))
                radii = np.full(7, aw/(bands*1.7))
            else:
                y = ah*(i+.5)/bands
                points = np.column_stack((np.linspace(0, aw-1, 7), np.full(7, y)))
                radii = np.full(7, ah/(bands*1.7))
            candidates.append(dict(points=points, radii=radii,
                                   length=float(max(aw, ah)), area=aw*ah//bands,
                                   background=True, origin='low-confidence-whole-canvas',
                                   score=float(aw*ah/bands)))
    # Face detection is a pixel-derived attention hint, never a body template.
    face_boxes = []
    cascade_path = getattr(cv2, 'data', None)
    if cascade_path is not None and hasattr(cv2, 'CascadeClassifier'):
        detector = cv2.CascadeClassifier(cv2.data.haarcascades+'haarcascade_frontalface_default.xml')
        if not detector.empty():
            face_boxes = detector.detectMultiScale(cv2.cvtColor(working, cv2.COLOR_RGB2GRAY),
                                                   scaleFactor=1.15, minNeighbors=5,
                                                   minSize=(max(18, aw//15), max(18, ah//15)))
    face_boxes = np.asarray(face_boxes).reshape(-1, 4)
    if len(face_boxes):
        box = face_boxes[np.argmax(face_boxes[:, 2]*face_boxes[:, 3])]
        focus = box[:2]+box[2:]*.5
    elif candidates:
        biggest = max(candidates, key=lambda item: item['score'] if not item['background'] else 0.)
        focus = np.mean(biggest['points'], axis=0)
    else:
        focus = np.array([aw/2., ah/2.])
    ordered = _ordered(candidates, focus, math.hypot(aw, ah))
    sx, sy = w/aw, h/ah
    gestures, descriptions = [], []
    for index, item in enumerate(ordered):
        points = item['points']*np.array([sx, sy])
        radii = item['radii']*math.sqrt(sx*sy)
        gestures.append((np.round(points, 3).tolist(), np.round(radii, 3).tolist()))
        descriptions.append(dict(index=index, origin=item['origin'], background=item['background'],
                                 length=round(item['length']*math.sqrt(sx*sy), 2),
                                 meanRadius=round(float(radii.mean()), 2),
                                 score=round(item['score'], 2)))
    metadata = dict(method='automatic low-frequency colour masses and medial open axes',
                    sourceSize=[w, h], analysisSize=[aw, ah], guideCount=len(gestures),
                    manualCoordinateCount=0, sourceSpecificRules=False,
                    ordering='broad background first; visual mass and endpoint proximity',
                    paintClipping=False, semanticPoseRecognition=False,
                    lowConfidence=bool(low_confidence), paleBackground=pale_background,
                    faceAttentionHints=(face_boxes*np.array([sx, sy, sx, sy])).round(2).tolist(),
                    guides=descriptions,
                    limitation='Classical image structure only; not anatomical pose or occlusion understanding.')
    return gestures, metadata
