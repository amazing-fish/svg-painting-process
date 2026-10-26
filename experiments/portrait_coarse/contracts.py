"""Explicit source-support and prepared-attention geometry contracts."""
import hashlib
import json
import numpy as np
import construction_episodes as ce
from . import energy

CONTRACT="continuous-source-band-final-geometry-v1"
PREPARED_CONTRACT="attention-measured-prepared-geometry-v1"

def geometry_digest(points,radii):
    return hashlib.sha256(json.dumps([np.asarray(points).tolist(),np.asarray(radii).tolist()],separators=(',',':')).encode()).hexdigest()


def is_ready_geometry(candidate,source_sha):
    record=candidate.get('geometryReady')
    if record is None:return False
    if (record.get('contract')!=CONTRACT or record.get('ready') is not True or
        record.get('sourceSHA256')!=source_sha or record.get('noFurtherSmoothing') is not True or
        record.get('finalGeometrySHA256')!=geometry_digest(candidate['points'],candidate['radii'])):
        raise ValueError('Invalid/stale source-support geometryReady contract')
    return True


def is_prepared_geometry(candidate,source_sha):
    record=candidate.get('preparedGeometry')
    if record is None:return False
    if (record.get('contract')!=PREPARED_CONTRACT or record.get('sourceSHA256')!=source_sha or
        record.get('observationDomain')!='model-attention' or record.get('sourceReady') is not False or
        record.get('noFurtherSmoothing') is not True or
        record.get('finalGeometrySHA256')!=geometry_digest(candidate['points'],candidate['radii'])):
        raise ValueError('Invalid/stale attention preparedGeometry contract')
    return True


def arc_length(points):
    return np.r_[0.,np.cumsum(np.linalg.norm(np.diff(points,axis=0),axis=1))]


def preserving_resample(points,radii,step=7.):
    """Piecewise linear arc sampling retains every supplied vertex/pressure."""
    points=np.asarray(points,float);radii=np.asarray(radii,float);arc=arc_length(points)
    if np.any(np.diff(arc)<=0):raise ValueError('Duplicated or nonpositive travel')
    stations=np.unique(np.r_[arc,np.linspace(0,arc[-1],max(3,int(np.ceil(arc[-1]/step))+1))])
    q=np.column_stack([np.interp(stations,arc,points[:,k]) for k in (0,1)])
    r=np.interp(stations,arc,radii)
    return q,r


def reducing_caps(radii):
    """Taper existing travelled endpoint stations; add no unsupported extension."""
    r=np.asarray(radii,float).copy()
    if len(r)<5:raise ValueError('Need enough travelled stations for caps')
    r[[0,-1]]*=.08;r[[1,-2]]*=.72
    return r


def observe_final_support(source,points,measurement_radii,background,normals=None):
    """Raw source activity in the final normals, absolute half-pixel sampling.

    The final paint halfwidth does not decide the smoothing or hide a white
    gap. This strict validation may reject reflectance gaps; no semantic body
    completion is inferred and no pixels become a paint mask.
    """
    points=np.asarray(points,float)
    normal=ce._frame(points) if normals is None else np.asarray(normals,float)
    span=float(max(3.*np.max(measurement_radii),2.));u=np.arange(-np.ceil(span)*2,np.ceil(span)*2+1)*.5
    query=points[:,None,:]+normal[:,None,:]*u[None,:,None]
    rgb=ce._sample(source,query);occ=1-np.exp(-np.maximum(0,energy._activity(rgb,background)-4)/22)
    mid=int(np.argmin(abs(u)));left=[];right=[];records=[]
    for i,row in enumerate(occ):
        l=h=mid
        if row[mid]>=.35:
            while l and row[l-1]>=.35:l-=1
            while h<len(row)-1 and row[h+1]>=.35:h+=1
            dl=-u[l];dr=u[h]
        else:dl=dr=0.
        left.append(dl);right.append(dr)
        records.append(dict(station=i,center=points[i].tolist(),centerOccupancy=float(row[mid]),leftSupportPx=float(dl),rightSupportPx=float(dr),upperRadiusPx=float(min(dl,dr))))
    return np.minimum(left,right),records


def validate_final(points,radii,upper,records):
    points=np.asarray(points);radii=np.asarray(radii);upper=np.asarray(upper)
    v=np.gradient(points,axis=0);a=np.gradient(v,axis=0)
    k=abs(v[:,0]*a[:,1]-v[:,1]*a[:,0])/(np.linalg.norm(v,axis=1)**3+1.e-9)
    reasons=[]
    if np.any(upper<=0):reasons.append('source_unsupported_center_or_zero_symmetric_width')
    if np.any(radii<=0):reasons.append('nonpositive_pressure')
    if np.any(radii>upper+1.e-8):reasons.append('final_radius_exceeds_connected_source_support')
    if np.max(radii*k)>=1.:reasons.append('physical_offset_fold_risk')
    return dict(contract=CONTRACT,ready=not reasons,rejectionReasons=reasons,allFinalCentersSupported=bool(np.all(upper>0)),
        maxRadiusOverSourceBoundPx=float(np.maximum(0,radii-upper).max()),radiusTimesCurvatureMax=float((radii*k).max()),
        medianDiameterPx=float(2*np.median(radii)),movementLengthPx=float(arc_length(points)[-1]),
        finalGeometryNoFurtherSmoothing=True,rawSourceStationRecords=records,
        note='Geometry source support only; no semantic or painting completion claim. Every accepted mark still paints its full ribbon.')
