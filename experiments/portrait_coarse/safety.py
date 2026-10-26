"""State-dependent, whole-stroke safety for the first attention plane only."""
from . import energy


def select_initial_load(raw, assessed, *, current, template, contract, layer,
                        phase, propose_fitted, assess, report):
    """Keep the raw candidate unless its current-canvas spill requires fitting.

    The caller supplies the already previewed full physical footprint. This
    decision is never cached; source-only geometry may be cached independently.
    Return (proposal, assessment, actual_fit_load), or None for whole rejection.
    """
    if raw is None:
        return None
    fit_load = bool(template.get('fitLoad', True))
    if layer != 0 or phase != 0 or contract.get('observationDomain') != 'model-attention':
        return raw, assessed, fit_load
    limit = max(energy.WHITE_LEAK_P95_READY, current['coarseEnergy']['whiteLeakP95'])
    record = dict(geometry=list(template['geometry']),
                  rawWhiteLeak=assessed['coarseEnergy']['whiteLeakP95'],
                  allowedWhiteLeak=limit, selectedFitLoad=fit_load,
                  decision='raw_safe', rejected=False)
    report.setdefault('initialLoadSafetyPreviews', []).append(record)
    if record['rawWhiteLeak'] <= limit + 1.e-9:
        return raw, assessed, fit_load
    fitted = propose_fitted(dict(template, fitLoad=True))
    report['attempted'] += 1
    record['fittedAvailable'] = fitted is not None
    fitted_assessment = assess(fitted) if fitted is not None else None
    if fitted_assessment is not None:
        record['fittedWhiteLeak'] = fitted_assessment['coarseEnergy']['whiteLeakP95']
        record['fittedEnergy'] = fitted_assessment['coarseEnergy']['energy']
    safe = (fitted_assessment is not None and
            fitted_assessment['coarseEnergy']['whiteLeakP95'] <= limit + 1.e-9)
    record['safeFittedCandidate'] = safe
    record['selectedFitLoad'] = True if safe else None
    record['rejected'] = not safe
    record['decision'] = ('fitted_safe' if safe else 'rejected_fitted_spill' if
                          fitted is not None else 'rejected_fitted_unavailable')
    if not safe:
        report['rejectedCoarse'] += 1
        return None
    return fitted, fitted_assessment, True
