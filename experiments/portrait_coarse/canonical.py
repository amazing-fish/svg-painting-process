"""Canonical physical ribbon shared by preview, painting, and SVG export."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import numpy as np
import build_portrait as b
from .contracts import (geometry_digest, preserving_resample, reducing_caps,
                        observe_final_support, validate_final)
from . import physical, energy

class CanonicalFootprint(tuple):
    def __new__(cls,footprint,geometry):
        result=super().__new__(cls,footprint);result.geometry=geometry;return result


def source_geometry(candidate,provenance):
    prepared=candidate.get('preparedGeometry')
    if prepared:
        binding=provenance['bindings'].get(prepared['finalGeometrySHA256'])
        if binding is None:raise ValueError('Prepared attention geometry lacks explicit bound raw provenance')
        raw_path=Path(binding['path'])
        if not raw_path.is_absolute():
            raw_path=Path(provenance['_baseDirectory'])/raw_path
        digest=hashlib.sha256(raw_path.read_bytes()).hexdigest()
        if digest!=binding['sha256'] or digest!=prepared['inputSourceFileSHA256']:raise ValueError('Raw attention provenance SHA mismatch')
        raw=json.loads(raw_path.read_text(encoding='utf-8'))
        if raw.get('metadata',{}).get('sourceSHA256') != prepared['sourceSHA256']:
            raise ValueError('Raw attention provenance belongs to another source')
        if (binding.get('observationDomain') != 'model-attention' or
                binding.get('sourceReady') is not False):
            raise ValueError('Raw attention binding must preserve its unverified source status')
        matches=[c for c in raw['candidates'] if c.get('name')==candidate.get('name') and c.get('person')==candidate.get('person') and c.get('attention')==candidate.get('attention')]
        if len(matches)!=1:raise ValueError('Ambiguous raw attention candidate identity')
        c=matches[0];q,r=preserving_resample(c['points'],c['radii']);r=reducing_caps(r)
        if geometry_digest(q,r)!=prepared['finalGeometrySHA256']:raise ValueError('Raw geometry does not reconstruct the frozen prepared contract')
        return np.array(c['points']),np.array(c['radii']),dict(kind='bound raw attention geometry',path=str(raw_path),sha256=digest,
            reconstructsPreparedHashExactly=True,sourceReady=False,pressureCapsNotUsedAsSupport=True)
    ready=candidate.get('geometryReady')
    if ready:
        # The source certificate is for final capped r. No independent raw
        # support field exists, so use those final values conservatively.
        return np.array(candidate['points']),np.array(candidate['radii']),dict(kind='source-verified final capped radii used as conservative upper bound',
            sourceReady=True,capsInverted=False,pressureCapsNotUsedToIncreaseSupport=True)
    raise ValueError('Canonical geometry adapter requires an explicit contract')


class Engine:
    def __init__(self,painter,payload,order,provenance):
        self.p=painter;self.guides={};self.cache={};self.calls=Counter();self.unique=Counter();self.accepted=[]
        self.background=energy.estimate_background(painter.src)
        for visited,i in enumerate(order):
            candidate=payload['candidates'][i]
            if not candidate.get('preparedGeometry') and not candidate.get('geometryReady'):continue
            points,support,origin=source_geometry(candidate,provenance)
            curve=physical.C1Curve(points,support);stations=curve.stations()
            conservative=bool(candidate.get('geometryReady'))
            def support_at(parameter,_curve=curve,_support=support,_conservative=conservative):
                value=_curve.value(parameter)
                return np.minimum(value,np.interp(parameter,_curve.knots_arc,_support)) if _conservative else value
            radius_upper=support_at(stations['parameter'])
            self.guides[visited]=dict(curve=curve,reference=float(np.median(support)),origin=origin,name=candidate.get('name'),support_at=support_at,
                points=stations['points'],radii=radius_upper)
            painter.guides[visited]=(stations['points'],radius_upper)
        self.active=None;self.layer=None

    def count(self,reason,unique=False):
        key=(self.active,self.layer,reason);self.calls[key]+=1
        if unique:self.unique[key]+=1

    def draft(self,template,points,radii):
        guide=self.guides[self.active];curve=guide['curve'];lane,width,begin,end=template['geometry']
        distances=physical.arc(points);lo=distances[begin]/distances[-1];hi=distances[end-1]/distances[-1]
        st=curve.stations(lo,hi);tags=st['arc']/curve.length;support=guide['support_at'](st['parameter']);offset=guide['reference']*lane
        # Stage1 may adapt pressure. It never steers the lane center or expands
        # the certified support. Legacy pressure adaptation itself is intact.
        requested=np.interp(tags,distances/distances[-1],radii)*width
        upper=support-abs(offset);eligible=upper>=np.maximum(physical.MIN_LOAD_RADIUS,requested*physical.MIN_LOAD_RATIO)
        starts=np.flatnonzero(np.diff(np.r_[False,eligible,False].astype(int))==1);stops=np.flatnonzero(np.diff(np.r_[False,eligible,False].astype(int))==-1)
        if not len(starts):return None,'no_long_loadable_support_interval'
        lengths=[st['arc'][z-1]-st['arc'][a] for a,z in zip(starts,stops)];k=int(np.argmax(lengths));a=starts[k];z=stops[k]
        if z-a<3 or st['arc'][z-1]-st['arc'][a]<32.:return None,'no_long_loadable_support_interval'
        path=st['points'][a:z]+st['normal'][a:z]*offset
        def bound(values):
            parameter=np.interp(values*curve.length,curve.dense_physical_arc,curve.dense_parameter)
            return guide['support_at'](parameter)-abs(offset)
        return dict(points=path,pressure=requested[a:z],tags=tags[a:z],bound=bound,curve=curve,offset=offset),None

    def geometry(self,template,points,radii,target,palette_size):
        # This cache contains source-only geometry, never state-dependent gain.
        key=(self.active,self.layer,tuple(template['geometry']),bool(template.get('fitLoad',True)),
            hashlib.sha256(np.asarray(radii).tobytes()).hexdigest(),palette_size)
        if key in self.cache:return self.cache[key]
        draft,reason=self.draft(template,points,radii)
        if draft is None:
            self.count(reason,True);self.cache[key]=(None,reason);return self.cache[key]
        initial=physical.finalize(draft['points'],draft['pressure'],draft['tags'],draft['curve'],source_pressure_upper=draft['bound'])
        if not initial.diagnostics['ready']:
            reason='initial_geometry:'+','.join(initial.diagnostics['rejectionReasons']);self.count(reason,True);self.cache[key]=(None,reason);return self.cache[key]
        fp=self.p.footprint(initial.left,initial.right)
        if fp is None:
            reason='physical_footprint_outside_canvas';self.count(reason,True);self.cache[key]=(None,reason);return self.cache[key]
        (x0,y0,x1,y1),alpha=fp;area=float(alpha.sum())
        if area<2.:
            reason='physical_area_too_small';self.count(reason,True);self.cache[key]=(None,reason);return self.cache[key]
        wanted=(target[y0:y1,x0:x1]*alpha[:,:,None]).sum(axis=(0,1))/area
        color,source_sample=self.p.choose_color(wanted,None,palette_size)
        geometry=initial;load_info=None
        if template.get('fitLoad',True):
            fitted,load_info=physical.fit_fixed_color_load(target,draft['points'],draft['pressure'],color,draft['tags'])
            if fitted is None:
                reason='color_load:'+load_info['rejection'];self.count(reason,True);self.cache[key]=(None,reason);return self.cache[key]
            geometry=physical.finalize(fitted['points'],fitted['pressure'],fitted['sourceTags'],draft['curve'],source_pressure_upper=draft['bound'])
            if not geometry.diagnostics['ready']:
                reason='final_geometry:'+','.join(geometry.diagnostics['rejectionReasons']);self.count(reason,True);self.cache[key]=(None,reason);return self.cache[key]
            fp=self.p.footprint(geometry.left,geometry.right)
            if fp is None:
                reason='physical_footprint_outside_canvas';self.count(reason,True);self.cache[key]=(None,reason);return self.cache[key]
        if self.guides[self.active]['origin']['sourceReady']:
            # Input stations certify only their supplied geometry. Recheck the
            # final ribbon after interpolation, load displacement, and caps,
            # using its canonical normals without creating alternate edges.
            upper,records=observe_final_support(self.p.src,geometry.points,geometry.radii,
                                                self.background,normals=geometry.normals)
            check=validate_final(geometry.points,geometry.radii,upper,records)
            if not check['ready']:
                reason='final_source_support:'+','.join(check['rejectionReasons'])
                self.count(reason,True);self.cache[key]=(None,reason);return self.cache[key]
            geometry.diagnostics['sourceReady']=True
            geometry.diagnostics['sourceSupportValidation']={k:v for k,v in check.items() if k!='rawSourceStationRecords'}
            geometry.diagnostics['sourceSupportValidation']['finalGeometrySHA256']=geometry_digest(geometry.points,geometry.radii)
        fp=CanonicalFootprint(fp,geometry)
        result=dict(geometry=geometry,footprint=fp,color=color,sourceSample=source_sample,initialWantedRGB=np.asarray(wanted).tolist(),
            fixedLaneOffset=draft['offset'],requestedLength=float(physical.arc(draft['points'])[-1]),load=load_info)
        self.cache[key]=(result,None);return self.cache[key]

    def proposal(self,painter,template,points,radii,target,palette_size):
        result,reason=self.geometry(template,points,radii,target,palette_size)
        if result is None:self.count('rejected:'+reason);return None
        fp=result['footprint'];(x0,y0,x1,y1),alpha=fp;area=float(alpha.sum());geometry=result['geometry']
        truth=target[y0:y1,x0:x1].astype(float);old=painter.canvas[y0:y1,x0:x1].astype(float);opacity=template['opacity']
        new=old*(1-alpha[:,:,None]*opacity)+np.array(result['color'])*alpha[:,:,None]*opacity
        old_error=((old-truth)**2).mean(axis=2);protection=1.+2.*np.exp(-old_error/70.)*(truth.mean(axis=2)/255.)**10
        gain=float((((old-truth)**2-(new-truth)**2)*protection[:,:,None]).sum())
        if gain<=max(10.,area*1.8):self.count('pixel_gain_not_positive');return None
        length=float(physical.arc(geometry.points)[-1]);self.count('physical_proposal_available')
        return dict(points=geometry.points,radii=geometry.radii,color=result['color'],sourceSample=result['sourceSample'],footprint=fp,
            gain=gain,area=area,length=length,priority=gain/(area**.45)*(1.+min(1.,length/240.)*.22),
            canonicalGeometry=geometry,canonicalRequestedLength=result['requestedLength'],canonicalFixedLaneOffset=result['fixedLaneOffset'])

    def evidence(self):
        def rows(counter):return [dict(guideIndex=i,guideName=self.guides[i]['name'],layer=layer,reason=reason,count=n) for (i,layer,reason),n in counter.items()]
        return dict(enabledGuides=[dict(guideIndex=i,name=g['name'],origin=g['origin'],sourceReferenceHalfwidth=g['reference']) for i,g in self.guides.items()],
            callCounts=rows(self.calls),uniqueGeometryCounts=rows(self.unique),accepted=self.accepted,
            canonicalModuleSHA256=hashlib.sha256(Path(physical.__file__).read_bytes()).hexdigest(),supportAndPressureSeparated=True,
            sourceValidationPreviewPutSVGReuseSameEdges=True,stage0EnergyThresholdsChanged=False)
