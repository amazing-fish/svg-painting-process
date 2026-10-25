"""Global painting stages and overlapping, unpartitioned attention windows.

``attention_route`` orders candidate *locations*, not painted stroke endpoints.
It never changes stroke geometry and does not create pixel ownership or closed
regions. A caller may reject any candidate after testing the current canvas.
"""
import math

import numpy as np
from scipy.spatial import cKDTree


STAGES = ('composition', 'value', 'volume', 'detail')
INTENTS = ('silhouette', 'plane', 'turn', 'edge', 'accent')


class TimelineValidationError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__('Global painting timeline rejected: ' + '; '.join(
            item['code'] for item in report['errors'][:6]))


def _summary(values):
    values = np.asarray(values, dtype=float)
    if not len(values):
        return dict(count=0, mean=0., p95=0., maximum=0.)
    return dict(count=len(values), mean=float(values.mean()),
                p95=float(np.percentile(values, 95)), maximum=float(values.max()))


def attention_route(points, initial_scores, width, height,
                    window_radius=130, bundle_size=10):
    """Return candidate indices and observable soft-attention diagnostics.

    A bundle uses an overlapping circular window. Within that window, source
    importance and proximity to candidate centers jointly rank locations. After
    a bundle the window moves toward nearby high-priority remaining candidates,
    by at most 0.8 radii. Only an empty neighborhood triggers relocation to the
    nearest remaining candidate; it never searches for a distant score maximum.

    Every input index is returned exactly once, including duplicate coordinates.
    Scores are fixed hints: the painting generator remains responsible for
    evaluating current residuals and deciding whether to make an actual mark.
    Neither actual stroke endpoints nor directions enter this function.
    """
    xy = np.asarray(points, dtype=np.float64)
    if xy.size == 0:
        xy = xy.reshape(0, 2)
    scores = np.asarray(initial_scores, dtype=np.float64)
    if xy.ndim != 2 or xy.shape[1] != 2 or scores.shape != (len(xy),):
        raise ValueError('Expected Nx2 points and N initial_scores')
    if not np.isfinite(xy).all() or not np.isfinite(scores).all():
        raise ValueError('Points and scores must be finite')
    if not math.isfinite(float(width)) or not math.isfinite(float(height)) or width <= 0 or height <= 0:
        raise ValueError('Image dimensions must be positive and finite')
    radius = float(window_radius)
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError('window_radius must be positive and finite')
    if isinstance(bundle_size, bool) or int(bundle_size) != bundle_size or bundle_size < 1:
        raise ValueError('bundle_size must be a positive integer')
    bundle_size = int(bundle_size)
    if len(xy) and ((xy[:, 0] < 0).any() or (xy[:, 0] > width).any() or
                    (xy[:, 1] < 0).any() or (xy[:, 1] > height).any()):
        raise ValueError('Candidate locations must lie in the image')
    count = len(xy)
    if not count:
        return [], dict(count=0, windowRadius=radius, bundleSize=bundle_size,
                        bundleCount=0, bundles=[], bundleIds=[], relocations=[],
                        seedSteps=_summary([]), windowSteps=_summary([]))

    nonnegative = np.maximum(scores, 0.)
    positive = nonnegative[nonnegative > 0]
    scale = float(np.percentile(positive, 90)) if len(positive) else 1.
    priority = np.minimum(nonnegative / max(scale, 1.e-12), 3.)
    available = np.ones(count, dtype=bool)
    live_ids = np.arange(count, dtype=np.int64)
    tree = cKDTree(xy)
    removed_since_build = 0

    def rebuild_if_needed():
        nonlocal tree, live_ids, removed_since_build
        if removed_since_build >= max(256, int(.25 * len(live_ids))):
            live_ids = np.flatnonzero(available)
            tree = cKDTree(xy[live_ids])
            removed_since_build = 0

    def neighbors(center, search_radius):
        local = np.asarray(tree.query_ball_point(center, search_radius), dtype=np.int64)
        if not len(local):
            return local
        candidates = live_ids[local]
        return candidates[available[candidates]]

    def nearest(center):
        # Static-tree tombstones are cheap until the next bounded rebuild.
        k = min(16, len(live_ids))
        while k:
            distances, positions = tree.query(center, k=k)
            positions = np.atleast_1d(positions)
            distances = np.atleast_1d(distances)
            candidates = live_ids[positions]
            active = available[candidates]
            if active.any():
                best_distance = distances[active].min()
                tied = candidates[active & (distances <= best_distance + 1.e-10)]
                return int(tied.min())
            if k == len(live_ids):
                return None
            k = min(k * 2, len(live_ids))
        return None

    center = xy[int(np.argmax(priority))].copy()
    cursor = center.copy()
    ordered, bundle_ids, bundles, relocations, window_steps = [], [], [], [], []
    while len(ordered) < count:
        rebuild_if_needed()
        local = neighbors(center, radius)
        if not len(local):
            target = nearest(center)
            if target is None:
                raise RuntimeError('Attention route lost an available candidate')
            old = center.copy()
            center = xy[target].copy()
            distance = float(np.linalg.norm(center - old))
            relocations.append(dict(beforeIndex=len(ordered), distance=distance,
                                    fromPoint=old.tolist(), toPoint=center.tolist(),
                                    reason='nearest remaining candidate after local window emptied'))
            window_steps.append(distance)
            local = neighbors(center, radius)

        # Rank the whole window once, then refine a bounded pool for a coherent
        # bundle. This avoids a quadratic nearest-neighbor scan over all points.
        local_dist = np.linalg.norm(xy[local] - center, axis=1) / radius
        quality = .7 * priority[local] - .3 * local_dist
        pool_size = min(len(local), max(64, bundle_size * 8))
        if pool_size < len(local):
            cutoff = float(np.partition(quality, -pool_size)[-pool_size])
            pool = local[quality >= cutoff]
            pool_quality = quality[quality >= cutoff]
            # Explicit input-index tie break is stable even on a uniform grid.
            ranking = np.lexsort((pool, -pool_quality))[:pool_size]
            pool = pool[ranking]
        else:
            pool = local[np.lexsort((local, -quality))]
        pool_live = np.ones(len(pool), dtype=bool)
        start = len(ordered)
        for _ in range(min(bundle_size, len(pool))):
            choices = np.flatnonzero(pool_live)
            ids = pool[choices]
            distance = np.linalg.norm(xy[ids] - cursor, axis=1) / radius
            central = np.linalg.norm(xy[ids] - center, axis=1) / radius
            merit = .6 * priority[ids] - .3 * distance - .1 * central
            rank = np.lexsort((ids, -merit))[0]
            selected, slot = int(ids[rank]), int(choices[rank])
            ordered.append(selected); bundle_ids.append(len(bundles))
            available[selected] = False; pool_live[slot] = False
            removed_since_build += 1
            cursor = xy[selected]
        stop = len(ordered)
        bundles.append(dict(id=len(bundles), start=start, end=stop,
                            center=center.tolist(), radius=radius, count=stop-start,
                            meanScore=float(scores[np.asarray(ordered[start:stop])].mean())))
        if stop == count:
            break

        # A nearby target can move attention across and back over earlier
        # windows. No point is permanently assigned to any one window.
        nearby = neighbors(center, radius * 2.)
        if len(nearby):
            distance = np.linalg.norm(xy[nearby] - center, axis=1) / radius
            merit = .55 * priority[nearby] - .45 * distance
            target = int(nearby[np.lexsort((nearby, -merit))[0]])
            delta = xy[target] - center
            length = float(np.linalg.norm(delta))
            step = delta * min(.65, (.8 * radius / length) if length else .65)
            center = center + step
            window_steps.append(float(np.linalg.norm(step)))

    route_xy = xy[np.asarray(ordered)]
    steps = np.linalg.norm(np.diff(route_xy, axis=0), axis=1)
    return ordered, dict(count=count, windowRadius=radius, bundleSize=bundle_size,
                         bundleCount=len(bundles), bundles=bundles,
                         bundleIds=bundle_ids, relocations=relocations,
                         seedSteps=_summary(steps), windowSteps=_summary(window_steps),
                         policy='overlapping soft attention; candidate centers only; geometry unchanged')


def build_stage_plan():
    """Only four global jobs; intent suggestions are soft, never partitions."""
    hints = dict(composition=['silhouette', 'plane'], value=['plane'],
                 volume=['turn', 'plane'], detail=['edge', 'accent'])
    reasons = dict(composition='先建立全画面的构图、轮廓与主要色面。',
                   value='构图建立后，统一全画面的明暗关系。',
                   volume='明暗关系建立后，画体积转折与冷暖。',
                   detail='主要低频色面与体积建立后，最后处理边缘与强调。')
    jobs = [dict(id=stage, stage=stage,
                 dependsOn=[] if not index else [STAGES[index-1]],
                 intentHints=hints[stage], reason=reasons[stage])
            for index, stage in enumerate(STAGES)]
    return dict(jobs=jobs, order=list(STAGES), intents=list(INTENTS),
                policy='global stage barriers; open attention windows may cross objects')


def _finite(value):
    return isinstance(value, (int, float, np.number)) and math.isfinite(float(value))


def _stage_events(events):
    records, errors = {}, []
    for event in events:
        stage = event.get('stage', event.get('phase', event.get('jobId', event.get('id'))))
        kind = str(event.get('type', event.get('event', 'interval'))).replace('-', '_')
        if kind in ('stroke', 'stroke_start', 'stroke_end', 'pen_up'):
            continue
        if stage not in STAGES:
            errors.append(dict(code='unknown_stage', stage=stage)); continue
        if kind in ('stage_start', 'phase_start', 'job_start', 'start'):
            if stage in records:
                errors.append(dict(code='duplicate_stage_start', stage=stage))
            records[stage] = dict(event, stage=stage, start=event.get('time', event.get('start')))
        elif kind in ('stage_end', 'phase_end', 'job_end', 'end'):
            record = records.setdefault(stage, dict(stage=stage))
            record.update(event)
            record['end'] = event.get('time', event.get('end'))
        else:
            if stage in records:
                errors.append(dict(code='duplicate_stage_interval', stage=stage))
            records[stage] = dict(event, stage=stage)
    return records, errors


def validate_timeline(events, strokes=None, require_complete=True, raise_on_error=True):
    """Validate global stage intervals and the intent of every supplied stroke.

    Events are {stage,start,end}, or paired {type:'stage_start'|'stage_end',
    stage,time}. Times use any consistent unit. This verifies scheduling only;
    visual evidence that broad color planes are established is a separate image
    quality measurement, never a fabricated per-object completion percentage.
    """
    records, errors = _stage_events(events)
    plan = build_stage_plan()
    evidence = []
    for job in plan['jobs']:
        stage, record = job['stage'], records.get(job['stage'])
        row = dict(job, start=None, end=None)
        if record is None:
            if require_complete:
                errors.append(dict(code='missing_stage', stage=stage))
            evidence.append(row); continue
        start, end = record.get('start'), record.get('end')
        row.update(start=start, end=end, strokeCount=record.get('strokeCount'))
        valid = _finite(start) and _finite(end) and end >= start
        if not valid:
            errors.append(dict(code='invalid_stage_interval', stage=stage, start=start, end=end))
        for dependency in job['dependsOn']:
            before = records.get(dependency)
            before_end = before.get('end') if before else None
            if before is None or not _finite(before_end):
                errors.append(dict(code='stage_dependency_incomplete', stage=stage, dependency=dependency))
            elif _finite(start) and before_end > start + 1.e-9:
                errors.append(dict(code='stage_started_before_dependency_finished', stage=stage,
                                   dependency=dependency, dependencyEnd=before_end, start=start))
        evidence.append(row)
    intent_counts = {intent: 0 for intent in INTENTS}
    stroke_count = 0
    for index, stroke in enumerate(strokes if strokes is not None else []):
        stroke_count += 1
        intent = stroke.get('intent')
        if intent not in INTENTS:
            errors.append(dict(code='invalid_stroke_intent', index=index, intent=intent))
        else:
            intent_counts[intent] += 1
        stage = stroke.get('stage', stroke.get('phase'))
        if stage is not None and stage not in STAGES:
            errors.append(dict(code='invalid_stroke_stage', index=index, stage=stage))
    report = dict(passed=not errors, errors=errors, jobs=evidence,
                  expectedOrder=list(STAGES),
                  observedOrder=sorted(records, key=lambda key: (
                      records[key].get('start') if _finite(records[key].get('start')) else math.inf,
                      STAGES.index(key))),
                  strokeCount=stroke_count, intentCounts=intent_counts,
                  scope='global stage order and stroke intent; image quality is evaluated separately')
    if errors and raise_on_error:
        raise TimelineValidationError(report)
    return report


validate_stage_timeline = validate_timeline


def _self_test():
    import json
    rng = np.random.default_rng(20261022)
    points = rng.uniform([0, 0], [1086, 1448], (2400, 2))
    scores = rng.uniform(.1, 1., len(points))
    points = np.vstack((points, points[:3]))
    scores = np.concatenate((scores, scores[:3]))
    before_points, before_scores = points.copy(), scores.copy()
    order, diagnostics = attention_route(points, scores, 1086, 1448)
    repeated, _ = attention_route(points, scores, 1086, 1448)
    assert order == repeated, 'Route must be deterministic'
    assert sorted(order) == list(range(len(points))), 'Missing or repeated indices'
    assert np.array_equal(points, before_points) and np.array_equal(scores, before_scores)
    assert len(diagnostics['bundleIds']) == len(points)
    forbidden = {'mask', 'owner', 'part', 'region', 'regionId', 'patch'}
    def check_keys(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for child in value.values():
                check_keys(child)
        elif isinstance(value, list):
            for child in value:
                check_keys(child)
    check_keys(diagnostics)
    events = [dict(stage=stage, start=i*100, end=(i+1)*100)
              for i, stage in enumerate(STAGES)]
    strokes = [dict(intent=intent) for intent in INTENTS]
    assert validate_timeline(events, strokes)['passed']
    bad = [dict(event) for event in events]
    bad[2]['start'] = 150
    assert not validate_timeline(bad, strokes, raise_on_error=False)['passed']
    assert not validate_timeline(events, [dict(intent='unknown')], raise_on_error=False)['passed']
    print(json.dumps(dict(status='passed', count=len(points), deterministic=True,
                          allIndicesExactlyOnce=True, inputsUnchanged=True,
                          noOwnershipFields=True, stages=list(STAGES),
                          overlappingStagesRejected=True, invalidIntentRejected=True,
                          bundles=diagnostics['bundleCount'],
                          seedSteps=diagnostics['seedSteps']), ensure_ascii=False))


if __name__ == '__main__':
    _self_test()


