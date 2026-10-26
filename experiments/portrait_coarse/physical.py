"""One final physical ribbon for all consumers. Isolated, not production."""
from dataclasses import dataclass
import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.ndimage import gaussian_filter1d
import cv2

ARC_STEP=.5
CURVATURE_PRESSURE_LIMIT=.8
PRESSURE_SLOPE=.30  # existing color-load pressure slope, physical px / px
MIN_LOAD_RATIO=.22 # existing continuous loadability floor, not an energy gate
MIN_LOAD_RADIUS=2.4

def arc(q):return np.r_[0.,np.cumsum(np.linalg.norm(np.diff(q,axis=0),axis=1))]

def frame_from_velocity(v):
    v=np.asarray(v,float);v=v/(np.linalg.norm(v,axis=1)[:,None]+1.e-12)
    return np.c_[-v[:,1],v[:,0]]

def sample(image,q):
    q=np.asarray(q);return cv2.remap(image.astype(np.float32),q[...,0].astype(np.float32),q[...,1].astype(np.float32),cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)

class C1Curve:
    def __init__(self,points,values=None):
        self.knots=np.asarray(points,float);self.knots_arc=arc(self.knots)
        if len(points)<3 or np.any(np.diff(self.knots_arc)<=1.e-7):raise ValueError('Nonpositive source travel')
        self.curve=PchipInterpolator(self.knots_arc,self.knots,axis=0)
        self.value=None if values is None else PchipInterpolator(self.knots_arc,np.asarray(values,float),axis=0)
        dense=np.linspace(0,self.knots_arc[-1],max(3,int(np.ceil(self.knots_arc[-1]/(.5*ARC_STEP)))+1))
        self.dense_parameter=dense;self.dense_physical_arc=arc(self.curve(dense));self.length=float(self.dense_physical_arc[-1])
    def stations(self,start=0.,stop=1.):
        lo=self.length*start;hi=self.length*stop
        if hi<=lo:raise ValueError('Empty physical source interval')
        s=np.linspace(lo,hi,max(3,int(np.ceil((hi-lo)/ARC_STEP))+1))
        t=np.interp(s,self.dense_physical_arc,self.dense_parameter)
        q=self.curve(t);v=self.curve.derivative()(t);a=self.curve.derivative(2)(t)
        n=frame_from_velocity(v);k=abs(v[:,0]*a[:,1]-v[:,1]*a[:,0])/(np.linalg.norm(v,axis=1)**3+1.e-12)
        value=None if self.value is None else self.value(t)
        return dict(points=q,normal=n,curvature=k,arc=s,parameter=t,values=value)

def lower_envelope(upper,s):
    """Linear-time continuous-pressure lower envelope; never lifts a bound."""
    r=np.asarray(upper,float).copy()
    for i in range(1,len(r)):r[i]=min(r[i],r[i-1]+PRESSURE_SLOPE*(s[i]-s[i-1]))
    for i in range(len(r)-2,-1,-1):r[i]=min(r[i],r[i+1]+PRESSURE_SLOPE*(s[i+1]-s[i]))
    return r

def intersecting_segments(poly,closed=False):
    q=np.asarray(poly,float);q=np.r_[q,q[:1]] if closed else q
    a=q[:-1];b=q[1:];count=0;examples=[]
    cross=lambda u,v:u[...,0]*v[...,1]-u[...,1]*v[...,0]
    for i in range(len(a)-2):
        js=np.arange(i+2,len(a));js=js[js!=len(a)-1] if closed and i==0 else js
        v1=cross(b[i]-a[i],a[js]-a[i]);v2=cross(b[i]-a[i],b[js]-a[i]);v3=cross(b[js]-a[js],a[i]-a[js]);v4=cross(b[js]-a[js],b[i]-a[js])
        hits=js[(v1*v2 < -1.e-8)&(v3*v4 < -1.e-8)];count+=len(hits)
        examples += [[int(i),int(j)] for j in hits[:max(0,5-len(examples))]]
    return dict(count=int(count),examples=examples)

def fit_fixed_color_load(target,path,nominal_pressure,color,source_tags):
    """Existing finite color DP with fixed pigment; geometry stays unfinalized.

    Only the full finalizer produces normals/edges and pressure caps. This is
    not an alternate raster/outline consumer; no color is selected here.
    """
    curve=C1Curve(path);st=curve.stations();q=st['points'];r=np.interp(st['parameter'],curve.knots_arc,nominal_pressure)
    tags=np.interp(st['parameter'],curve.knots_arc,source_tags)
    relative=np.linspace(-1.45,1.45,37);query=q[:,None,:]+st['normal'][:,None,:]*r[:,None,None]*relative[None,:,None]
    values=sample(target,query);values=gaussian_filter1d(values,1.2,axis=1,mode='nearest');values=gaussian_filter1d(values,.65,axis=0,mode='nearest')
    error=np.linalg.norm(values-np.asarray(color),axis=2)/np.sqrt(3.)
    local=error**2+relative[None,:]**2*95.;transition=(relative[:,None]-relative[None,:])**2*850.
    back=np.empty(local.shape,dtype=np.int16);cost=local[0]
    for i in range(1,len(q)):
        combined=cost[:,None]+transition;previous=combined.argmin(axis=0);back[i]=previous;cost=local[i]+combined[previous,np.arange(len(relative))]
    selected=np.empty(len(q),int);selected[-1]=cost.argmin()
    for i in range(len(q)-1,0,-1):selected[i-1]=back[i,selected[i]]
    lo=np.empty(len(q));hi=np.empty(len(q));quality=error[np.arange(len(q)),selected]
    for i,j in enumerate(selected):
        tolerance=max(13.,min(30.,float(error[i,j])+7.));l=h=int(j)
        while l and error[i,l-1]<=tolerance:l-=1
        while h<len(relative)-1 and error[i,h+1]<=tolerance:h+=1
        lo[i]=relative[l];hi[i]=relative[h]
    ds=max(1.e-6,float(np.median(np.diff(st['arc']))));raw_width=(hi-lo)*.5
    # The legacy constants denote physical px. Equal-arc samples may now be
    # subpixel, so an index-space floor of1 would silently halve this scale.
    shift=gaussian_filter1d((lo+hi)*.5,18./ds,mode='nearest')
    widths=gaussian_filter1d(raw_width,20./ds,mode='nearest')
    fitted=q+st['normal']*(shift*r)[:,None];pressure=np.maximum(.7,r*widths)
    can_load=(quality<39.)&(raw_width*r>=np.maximum(MIN_LOAD_RADIUS,r*MIN_LOAD_RATIO))
    good=gaussian_filter1d(can_load.astype(float),.65)>=.55
    starts=np.flatnonzero(np.diff(np.r_[False,good,False].astype(int))==1);stops=np.flatnonzero(np.diff(np.r_[False,good,False].astype(int))==-1)
    if not len(starts):return None,dict(rejection='no_continuous_fixed_pigment_load')
    # Preserve the existing longest continuous interval policy; use physical
    # length because the caller no longer promises arbitrary index density.
    lengths=[st['arc'][z-1]-st['arc'][a] for a,z in zip(starts,stops)];k=int(np.argmax(lengths));a=starts[k];z=stops[k]
    if z-a<3 or arc(fitted[a:z])[-1]<32.:return None,dict(rejection='fixed_pigment_interval_too_short')
    return dict(points=fitted[a:z],pressure=pressure[a:z],sourceTags=tags[a:z]),dict(interval=[int(a),int(z)],fixedPigment=list(color),newColorSelected=False)

@dataclass(frozen=True)
class PhysicalRibbon:
    points: np.ndarray
    radii: np.ndarray
    normals: np.ndarray
    left: np.ndarray
    right: np.ndarray
    source_tags: np.ndarray
    diagnostics: dict
    def __post_init__(self):
        for value in (self.points,self.radii,self.normals,self.left,self.right,self.source_tags):value.setflags(write=False)
    def svg_path(self):return 'M'+' L'.join(f'{x:.6f},{y:.6f}' for x,y in np.r_[self.left,self.right[::-1]])+' Z'

def finalize(draft_points,draft_pressure,source_tags,source_guide,source_pressure_upper=None):
    """ONE final C1 physical curve, actual width validation, immutable edges."""
    curve=C1Curve(draft_points,draft_pressure);st=curve.stations();q=st['points'];requested=np.maximum(0,st['values'])
    tags=np.interp(st['parameter'],curve.knots_arc,source_tags)
    limit=CURVATURE_PRESSURE_LIMIT/np.maximum(st['curvature'],1.e-12)
    support_limit=None if source_pressure_upper is None else np.maximum(0.,source_pressure_upper(tags))
    if support_limit is not None:limit=np.minimum(limit,support_limit)
    limited=lower_envelope(np.minimum(requested,limit),st['arc'])
    u=(st['arc']-st['arc'][0])/max(1.e-9,st['arc'][-1]-st['arc'][0])
    pressure_cap=np.interp(u,[0,.08,.90,1],[.38,1.,1.,.30]);radius=limited*pressure_cap
    # A minimum load is a rejection condition, never an upward radius floor.
    core=(u>=.08)&(u<=.90);required=np.maximum(MIN_LOAD_RADIUS,requested*MIN_LOAD_RATIO)
    narrow=core&(radius<required)
    tangent=source_guide.curve.derivative()(np.interp(tags*source_guide.length,source_guide.dense_physical_arc,source_guide.dense_parameter))
    velocity=curve.curve.derivative()(st['parameter']);cos=(velocity*tangent).sum(axis=1)/(np.linalg.norm(velocity,axis=1)*np.linalg.norm(tangent,axis=1)+1.e-12)
    # Canonical edge serialization is chosen ONCE, before validation/source
    # sampling/raster/SVG. Consumers may not derive another pair of edges.
    left=np.round(q+st['normal']*radius[:,None],6);right=np.round(q-st['normal']*radius[:,None],6)
    centers=intersecting_segments(q);edges=intersecting_segments(np.r_[left,right[::-1]],True)
    reasons=[]
    if (cos<=0).any():reasons.append('actual_path_reverses_source_travel')
    if centers['count']:reasons.append('actual_centerline_self_intersection')
    if edges['count']:reasons.append('whole_physical_ribbon_self_intersection')
    if narrow.any():reasons.append('curvature_limit_would_make_local_load_filamentary')
    if (radius<=0).any():reasons.append('nonpositive_actual_pressure')
    diagnostics=dict(ready=not reasons,rejectionReasons=reasons,sourceReady=False,
        maximumActualRadiusCurvature=float((radius*st['curvature']).max()),minimumForwardCosine=float(cos.min()),
        backwardsStations=int((cos<=0).sum()),centerlineIntersections=centers,ribbonBoundaryIntersections=edges,
        filamentaryCoreStations=int(narrow.sum()),filamentaryCoreFraction=float(narrow.sum()/max(1,core.sum())),
        requestedMedianDiameter=float(2*np.median(requested)),actualMedianDiameter=float(2*np.median(radius)),
        minimumCoreRetainedPressureRatio=float((radius[core]/np.maximum(1e-9,requested[core])).min()),
        sourcePressureBoundApplied=support_limit is not None,
        maximumPressureOverSourceBound=0. if support_limit is None else float(np.maximum(0,radius-support_limit).max()),
        curvatureLimit=CURVATURE_PRESSURE_LIMIT,pressureSlope=PRESSURE_SLOPE,
        finalCurveLength=float(st['arc'][-1]-st['arc'][0]),sourceTagInterval=[float(tags[0]),float(tags[-1])],
        oneCanonicalGeometryForSourcePreviewPutSVG=True,postValidationSmoothing=False)
    return PhysicalRibbon(q,radius,st['normal'],left,right,tags,diagnostics)
