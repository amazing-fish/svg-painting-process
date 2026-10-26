"""Experimental continuous stage0 surface energy; dominant-border assumption.

Normal/longitudinal smoothing is in a source-derived moving brush frame, using
the actual local diameter. This measures surface support, not semantic anatomy.
No station-ready, continuity-ready or signed-contrast ratio is a progress gate.
"""
import numpy as np
from scipy.ndimage import gaussian_filter1d

MIN_INITIAL_GAIN=2.e-4
MIN_REPAIR_GAIN=8.e-4
MEAN_READY=.08
P90_READY=.16
WHITE_LEAK_P95_READY=.12

def _strip_smooth(values,offsets,positions,radii,diameter_fraction):
    # Gaussian in normal coordinates followed by a variable-scale along-axis
    # Gaussian. Neither source support nor this attention kernel clips paint.
    normal_step=float(np.median(np.diff(offsets)))
    across=gaussian_filter1d(values,(2.*diameter_fraction)/normal_step,axis=1,mode='nearest')
    sigma=np.maximum(.5,2.*np.asarray(radii)*diameter_fraction)
    distance=np.asarray(positions)[:,None]-np.asarray(positions)[None,:]
    kernel=np.exp(-.5*(distance/sigma[:,None])**2)
    kernel/=np.maximum(1.e-9,kernel.sum(axis=1,keepdims=True))
    return np.einsum('ij,jkc->ikc',kernel,across)

def estimate_background(source):
    """Dominant outer-border appearance; no foreground mask or paint clipping."""
    source=np.asarray(source,float);h,w=source.shape[:2];dy=max(1,round(h*.02));dx=max(1,round(w*.02))
    border=np.concatenate([source[:dy].reshape(-1,3),source[-dy:].reshape(-1,3),source[:,:dx].reshape(-1,3),source[:,-dx:].reshape(-1,3)])
    color=np.median(border,axis=0);distance=np.linalg.norm(border-color,axis=1)/np.sqrt(3.)
    noise=max(4.,float(np.quantile(distance,.50))*2.)
    support=float((distance<=max(8.,noise*2.)).mean())
    return dict(rgb=color.tolist(),noise=noise,borderSupport=support,supported=bool(support>=.55),uniformityVerified=False,
        assumption='One dominant approximately uniform border background; blank white canvas and that background both mean no structural paint demand.')

def _activity(rgb,background):
    # White canvas and the source border appearance are equivalent empty space.
    # This avoids requiring an exterior gray wash merely to match the source.
    white=np.linalg.norm(255.-rgb,axis=2)/np.sqrt(3.)
    backdrop=np.linalg.norm(rgb-np.asarray(background['rgb']),axis=2)/np.sqrt(3.)
    return np.maximum(0.,np.minimum(white,backdrop)-background['noise'])

def _source_valley_attention(source_occ,offsets):
    """Soft current-movement attention contracts at source white/background gaps.

    A remote parallel surface across a source gap belongs to its own episode.
    These positive weights measure support only; physical paint is never cut.
    """
    weights=[];widths=[];near=np.flatnonzero(np.abs(offsets)<=.4)
    for row in source_occ:
        anchor=int(near[np.argmax(row[near])]);threshold=max(.03,float(row[anchor])*.20)
        lo=hi=anchor
        while lo and row[lo-1]>=threshold:lo-=1
        while hi<len(row)-1 and row[hi+1]>=threshold:hi+=1
        left=max(.30,min(1.25,.65*abs(offsets[anchor]-offsets[max(0,lo-1)])))
        right=max(.30,min(1.25,.65*abs(offsets[min(len(row)-1,hi+1)]-offsets[anchor])))
        delta=offsets-offsets[anchor];sigma=np.where(delta<0,left,right)
        weights.append(np.exp(-.5*(delta/sigma)**2));widths.append([left,right])
    return np.array(weights),widths

def _attention(source_occ,offsets):
    # The supplied physical halfwidth is the positive-demand scale. A touching
    # neighboring surface at1.6r must not become this movement's completion debt.
    # The negative spill guard below deliberately retains its wider field.
    outside=np.maximum(0.,np.abs(offsets)-1.)
    weights=np.exp(-.5*(outside/.20)**2)
    return np.broadcast_to(weights,source_occ.shape).copy(),[[1.,1.]]*len(source_occ)

def _moments(activity,offsets,attention):
    weighted=activity*attention;mass=weighted.sum(axis=1)
    center=(weighted*offsets).sum(axis=1)/np.maximum(1.e-6,mass)
    width=np.sqrt((weighted*(offsets[None,:]-center[:,None])**2).sum(axis=1)/np.maximum(1.e-6,mass))
    return mass/attention.sum(axis=1),center,width

def evaluate(source_rgb,canvas_rgb,offsets,positions,radii,background=None):
    source_rgb=np.asarray(source_rgb,float);canvas_rgb=np.asarray(canvas_rgb,float)
    offsets=np.asarray(offsets,float);positions=np.asarray(positions,float);radii=np.asarray(radii,float)
    if source_rgb.shape!=canvas_rgb.shape or source_rgb.shape[:2]!=(len(positions),len(offsets)):
        raise ValueError('Source/canvas moving-frame samples must match')
    background=background or dict(rgb=[255.,255.,255.],noise=4.,supported=True,borderSupport=1.,assumption='Explicit uniform white synthetic background')
    coarse_source=_activity(_strip_smooth(source_rgb,offsets,positions,radii,.18),background)
    coarse_actual=_activity(_strip_smooth(canvas_rgb,offsets,positions,radii,.18),background)
    fine_source=_activity(_strip_smooth(source_rgb,offsets,positions,radii,.04),background)
    fine_actual=_activity(_strip_smooth(canvas_rgb,offsets,positions,radii,.04),background)
    source_occ=1.-np.exp(-np.maximum(0.,fine_source-4.)/22.)
    actual_occ=1.-np.exp(-np.maximum(0.,fine_actual-4.)/22.)
    attention,attention_widths=_attention(source_occ,offsets)
    avg=lambda v:(v*attention).sum(axis=1)/attention.sum(axis=1)
    denominator=avg(source_occ**2)+.05
    occupancy=avg((source_occ-actual_occ)**2)/denominator
    main_plane=avg((coarse_source-coarse_actual)**2)/(avg(coarse_source**2)+100.)
    sm,sc,sw=_moments(coarse_source,offsets,attention)
    am,ac,aw=_moments(coarse_actual,offsets,attention)
    mass=np.log((am+2.)/(sm+2.))**2
    center=(ac-sc)**2
    width=np.log((aw+.10)/(sw+.10))**2
    # Excess occupancy in a source white valley gets more weight than a small
    # internal value change; identity source/canvas gives exactly zero leakage.
    excess=np.maximum(0.,actual_occ-source_occ)
    # Spill checks keep a wider field than positive coverage demand: ignoring a
    # distant unpainted limb must never make its separating white gap paintable.
    spill_attention=np.exp(-.5*(offsets[None,:]/1.25)**2)
    spill_avg=lambda v:(v*spill_attention).sum(axis=1)/spill_attention.sum()
    white_leak=spill_avg(excess**2*(1.-source_occ)**2)/(spill_avg(source_occ**2)+.05)
    deficit=avg(np.maximum(0.,source_occ-actual_occ)**2)/denominator
    deficit_field=np.maximum(0.,source_occ-actual_occ)*attention
    excess_field=excess**2*(1.-source_occ)**2*spill_attention
    components=dict(occupancy=occupancy,mainPlane=main_plane,mass=mass,center=center,width=width,whiteGap=white_leak)
    energy=.30*occupancy+.20*main_plane+.15*mass+.10*center+.10*width+.15*white_leak
    meaningful=bool(background['supported'] and float(sm.mean())>=max(4.,2.*background['noise']))
    mean=float(energy.mean());p90=float(np.quantile(energy,.90));leak95=float(np.quantile(white_leak,.95))
    ready=meaningful and mean<=MEAN_READY and p90<=P90_READY and leak95<=WHITE_LEAK_P95_READY
    return dict(energy=mean,p90Energy=p90,whiteLeakP95=leak95,mainSurfaceReady=bool(ready),
        sourceSupportPresent=meaningful,stationEnergy=energy.tolist(),
        sourceSupportInterpretation='Signal exceeds this estimator noise; false means unmeasurable/unsupported signal, never proof of no person. supported is not proof of uniform background.',
        components={k:float(v.mean()) for k,v in components.items()},
        occupancyDeficit=float(deficit.mean()),occupancyExcess=float(excess.mean()),
        repairField=dict(occupancyDeficitStations=deficit.tolist(),whiteLeakStations=white_leak.tolist(),
            positiveDeficitLane=offsets[deficit_field.argmax(axis=1)].tolist(),
            negativeExcessLane=offsets[excess_field.argmax(axis=1)].tolist()),
        meanMassRatio=float(np.mean((am+2.)/(sm+2.))),
        background=background,positiveAttention=dict(coreHalfwidthRadii=1.,outsideFalloffSigmaRadii=.20,negativeSpillSigmaRadii=1.25),
        scale=dict(normalCoarseDiameterFraction=.18,normalWhiteGapDiameterFraction=.04,
                   actualDiameterRange=[float(2*radii.min()),float(2*radii.max())]),
        meaning='Source-relative low-frequency main-surface support only; not semantic pose or value completion.')

def progress(before,after):return float(before['coarseEnergy']['energy']-after['coarseEnergy']['energy'])
