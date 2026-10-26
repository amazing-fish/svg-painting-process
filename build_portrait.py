"""Paint one shared canvas: global gestures, value, volume, then detail.

No semantic polygons, source-region clipping, ownership maps or source-image
reveals are used. Automatically extracted open compositional gestures guide the broad movement;
colour, local structure and correction decisions are read from the reference.
Dependencies: numpy scipy opencv-python Pillow.
python build_portrait.py --source IMAGE --work-dir generated --through 1
python build_portrait.py --source IMAGE --work-dir generated --through 4
"""
from auto_guides import extract_guides
from global_plan import attention_route, validate_timeline
from construction_episodes import paint_construction_episode
from pathlib import Path
import argparse
import base64
import io
import json
import math
import pickle
import time
import cv2
import numpy as np
from scipy.spatial import cKDTree
from scipy.ndimage import gaussian_filter1d
from PIL import Image, ImageDraw, ImageOps

ROOT=Path(__file__).resolve().parent
OUT,QA=ROOT/'output',ROOT/'qa'
VERSION='7.2'
cv2.setNumThreads(1)
STAGES=('composition','value','volume','detail')
PHASES=[('连贯底形','顺着开放走势铺通主面，再建立主要转折'),
        ('大面明暗','留在当前走势调整明暗与边缘，再继续下一段'),
        ('体积冷暖','沿软走势塑造转折，允许跨越早期笔缘修形'),
        ('焦点细节','整体成立后加入线条、材质和少量点笔')]
INTENT_LABELS=dict(silhouette='整体主势',plane='大面铺色',turn='体积转折',edge='边缘与线条',accent='反光与点笔')


def image_url(rgb,width=480):
    image=Image.fromarray(rgb);image.thumbnail((width,2000),Image.Resampling.LANCZOS)
    stream=io.BytesIO();image.save(stream,format='JPEG',quality=93)
    return 'data:image/jpeg;base64,'+base64.b64encode(stream.getvalue()).decode()


def resample(points,radii,step=7):
    p=np.asarray(points,float);r=np.asarray(radii,float)
    d=np.r_[0,np.cumsum(np.linalg.norm(np.diff(p,axis=0),axis=1))]
    t=np.linspace(0,d[-1],max(3,int(d[-1]/step)+1))
    q=np.column_stack([np.interp(t,d,p[:,i]) for i in range(2)])
    rr=np.interp(t,d,r)
    if len(q)>6:
        q=gaussian_filter1d(q,2.,axis=0,mode='nearest')
        rr=gaussian_filter1d(rr,1.4,mode='nearest')
    # A short loaded-brush cap, expressed as additional travelled geometry.
    # It is not a hidden full outline and never exposes a future fat section.
    entry=q[1]-q[0];entry/=np.linalg.norm(entry)+1e-9
    exit_axis=q[-1]-q[-2];exit_axis/=np.linalg.norm(exit_axis)+1e-9
    q=np.r_[[q[0]-entry*rr[0]*.62,q[0]-entry*rr[0]*.35],q,
            [q[-1]+exit_axis*rr[-1]*.35,q[-1]+exit_axis*rr[-1]*.62]]
    rr=np.r_[rr[0]*.08,rr[0]*.72,rr,rr[-1]*.72,rr[-1]*.08]
    return q,rr


def edges(points,radii):
    tangent=np.gradient(points,axis=0);tangent/=np.linalg.norm(tangent,axis=1)[:,None]+1e-9
    normal=np.column_stack((-tangent[:,1],tangent[:,0]))
    return np.round(points+normal*radii[:,None],3),np.round(points-normal*radii[:,None],3)


def path_d(left,right):
    return 'M'+' L'.join(f'{x:.3f},{y:.3f}' for x,y in np.r_[left,right[::-1]])+' Z'


class SharedCanvasPainter:
    def __init__(self,source,max_edge=768):
        original=ImageOps.exif_transpose(Image.open(source)).convert('RGBA')
        if min(original.size)<32:raise ValueError('Image must be at least 32 pixels on each side')
        original.thumbnail((max_edge,max_edge),Image.Resampling.LANCZOS)
        self.src=np.array(Image.alpha_composite(Image.new('RGBA',original.size,'white'),original).convert('RGB'))
        self.h,self.w=self.src.shape[:2];self.canvas=np.full_like(self.src,255)
        self.rng=np.random.default_rng(83177);self.strokes=[];self.layers=[];self.events=[];self.clock=0
        self.construction_reports=[]
        self.guides=[]
        gestures,self.guide_metadata=extract_guides(self.src)
        if not gestures:raise ValueError("No usable open movements were found in this image")
        self.auto_guides=True
        for p,r in gestures:
            pts=np.array(p,float);rad=np.array(r,float)
            pts,rad=resample(pts,rad)
            self.guides.append((pts,rad))
        allp=np.concatenate([p for p,r in self.guides]);allt=np.concatenate([np.gradient(p,axis=0) for p,r in self.guides])
        allt/=np.linalg.norm(allt,axis=1)[:,None]+1e-9
        self.guide_tree=cKDTree(allp);self.guide_vectors=allt
        yy,xx=np.mgrid[0:self.h:20,0:self.w:20];xy=np.column_stack((xx.ravel(),yy.ravel()))
        distances,indices=self.guide_tree.query(xy,k=min(8,len(allp)))
        if distances.ndim==1:distances=distances[:,None];indices=indices[:,None]
        v=self.guide_vectors[indices];angle=np.arctan2(v[:,:,1],v[:,:,0]);weights=np.exp(-(distances/155)**2)/(distances+12)
        z=(np.exp(2j*angle)*weights).sum(axis=1);a=.5*np.angle(z).reshape(xx.shape)
        self.flow_x=cv2.resize(np.cos(2*a).astype(np.float32),(self.w,self.h))
        self.flow_y=cv2.resize(np.sin(2*a).astype(np.float32),(self.w,self.h))
        gray=cv2.cvtColor(self.src,cv2.COLOR_RGB2GRAY).astype(np.float32)
        mean=cv2.GaussianBlur(gray,(0,0),2)
        self.contrast=np.sqrt(np.maximum(0,cv2.GaussianBlur(gray*gray,(0,0),2)-mean*mean))
        gx=cv2.Sobel(cv2.GaussianBlur(gray,(0,0),.6),cv2.CV_32F,1,0)
        gy=cv2.Sobel(cv2.GaussianBlur(gray,(0,0),.6),cv2.CV_32F,0,1)
        xx=cv2.GaussianBlur(gx*gx,(0,0),1.7);yy=cv2.GaussianBlur(gy*gy,(0,0),1.7);xy=cv2.GaussianBlur(gx*gy,(0,0),1.7)
        self.texture_angle=.5*np.arctan2(2*xy,xx-yy)+np.pi/2
        self.coherence=np.sqrt((xx-yy)**2+4*xy*xy)/(xx+yy+1e-8)
        self.edge_distance=cv2.distanceTransform(255-cv2.Canny(gray.astype(np.uint8),24,60),cv2.DIST_L2,3)
        self.palettes={};self.routes=[]

    def pixel(self,x,y):return int(np.clip(round(x),0,self.w-1)),int(np.clip(round(y),0,self.h-1))

    def palette(self,n):
        if n in self.palettes:return self.palettes[n]
        small=cv2.resize(self.src,(min(320,self.w),max(1,round(self.h*min(320,self.w)/self.w))))
        flat=small.reshape(-1,3);take=self.rng.choice(len(flat),min(26000,len(flat)),False)
        lab=cv2.cvtColor(flat[take][None].astype(np.float32)/255,cv2.COLOR_RGB2LAB)[0]
        cv2.setRNGSeed(83177+n)
        _,_,centers=cv2.kmeans(lab,n,None,(cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_MAX_ITER,35,.08),2,cv2.KMEANS_PP_CENTERS)
        samples=np.column_stack((self.rng.integers(0,self.w,30000),self.rng.integers(0,self.h,30000)))
        rgb=self.src[samples[:,1],samples[:,0]]
        samplelab=cv2.cvtColor(rgb[None].astype(np.float32)/255,cv2.COLOR_RGB2LAB)[0]
        palette=[];coords=[]
        for center in centers:
            i=int(np.argmin(((samplelab-center)**2).sum(axis=1)));palette.append(rgb[i]);coords.append(samples[i].tolist())
        # White/near-white is real paint too: negative shapes can be corrected.
        white_index=np.unravel_index(np.argmax(self.src.sum(axis=2)),self.src.shape[:2])
        palette.append(self.src[white_index]);coords.append([int(white_index[1]),int(white_index[0])])
        result=np.array(palette),coords;self.palettes[n]=result;return result

    def choose_color(self,target,xy,n):
        colors,coords=self.palette(n);i=int(np.argmin(((colors.astype(float)-target)**2).sum(axis=1)))
        return colors[i].tolist(),coords[i]

    def footprint(self,left,right):
        polygon=np.r_[left,right[::-1]]
        x0,y0=np.maximum(0,np.floor(polygon.min(axis=0)).astype(int)-2)
        x1,y1=np.minimum([self.w,self.h],np.ceil(polygon.max(axis=0)).astype(int)+3)
        if x1<=x0 or y1<=y0:return None
        m=np.zeros(((y1-y0)*2,(x1-x0)*2),np.uint8)
        cv2.fillPoly(m,[np.rint((polygon-[x0,y0])*2).astype(np.int32)],255,cv2.LINE_AA)
        a=cv2.resize(m,(x1-x0,y1-y0),interpolation=cv2.INTER_AREA).astype(np.float32)/255
        return (x0,y0,x1,y1),a

    def put(self,points,radii,color,sample,layer,intent,opacity=1.,group=None,proposal=None):
        points=np.round(np.array(points,float),3);left,right=edges(points,np.array(radii))
        length=float(np.linalg.norm(np.diff(points,axis=0),axis=1).sum())
        if length<.1:return False
        crop=self.footprint(left,right) if proposal is None else proposal
        if crop is None:return False
        (x0,y0,x1,y1),a=crop;alpha=a[:,:,None]*opacity
        c=self.canvas[y0:y1,x0:x1];c[:]=np.rint(c*(1-alpha)+np.array(color)*alpha).astype(np.uint8)
        duration=round(np.clip(130+length*2.5,150,3300));pause=85 if intent not in ('accent','edge') else 100
        s=dict(d=path_d(left,right),left=left.tolist(),right=right.tolist(),points=points.tolist(),
               spine='M'+' L'.join(f'{x:g},{y:g}' for x,y in points),length=length,width=round(float(2*np.max(radii)),3),
               color='#'+''.join(f'{int(c):02x}' for c in color),opacity=opacity,start=self.clock,duration=duration,
               pauseAfter=pause,layer=layer,stage=STAGES[layer],phase=PHASES[layer][0],intent=intent,
               intentLabel=INTENT_LABELS[intent],group=group or f'{layer}-{len(self.strokes)//10}',
               colorSample=sample,sourceSample=sample,sample=sample,type='sweep' if length>12 else 'dab')
        self.strokes.append(s);self.clock+=duration+pause;return True

    def compose(self):
        return self.construction_sequence(0)

    def construction_sequence(self,layer):
        # This is a list of open drawing movements, not source regions. Nearby
        # complementary movements stay together; paint remains free to overlap.
        order=list(range(len(self.guides)))
        for i in order:
            report=paint_construction_episode(self,i,layer=layer,palette_size=12 if layer==0 else 28)
            self.construction_reports.append(report)
            print(json.dumps(dict(stage=STAGES[layer],guide=i,accepted=report['accepted'],status=report['status'],after=report['after']),ensure_ascii=False),flush=True)
            if report['needsRevisit']:
                # Stay on this movement for an immediate, more selective load.
                # A stalled continuation remains explicitly unresolved; it is
                # never labelled complete merely because a budget was spent.
                follow=paint_construction_episode(self,i,layer=layer,palette_size=28 if layer==0 else 96,
                                                  max_strokes=5,max_rounds=1,feedback=report)
                follow['continuationOf']=len(self.construction_reports)-1
                self.construction_reports.append(follow)
                print(json.dumps(dict(stage=STAGES[layer],guide=i,continuation=True,accepted=follow['accepted'],status=follow['status'],after=follow['after']),ensure_ascii=False),flush=True)
                report=follow
            # A move is now an explicit scheduling decision. Unresolved axes
            # stay visible with their stop reason; budget is never success.
            report['transitionDecision']=('proxy_ready' if not report['needsRevisit'] else
                                          'stalled_unresolved' if report['status']=='no_improvement' else
                                          'blocked_unresolved')
            if report['transitionDecision']=='blocked_unresolved':
                self.construction_blocked=dict(layer=layer,guideIndex=i,
                    reportIndex=len(self.construction_reports)-1,
                    reason='repair_budget_exhausted_before_coarse_ready')
                return False
        return True

    def direction(self,x,y,layer):
        ix,iy=self.pixel(x,y)
        theta=.5*math.atan2(float(self.flow_y[iy,ix]),float(self.flow_x[iy,ix]))
        if layer==3 and self.coherence[iy,ix]>.22:theta=float(self.texture_angle[iy,ix])
        v=np.array([math.cos(theta),math.sin(theta)])
        if v[1]<-.05:v=-v
        return v

    def gesture(self,x,y,width,length,layer,target,tolerance):
        center=np.array([x,y],float);initial=self.direction(x,y,layer)
        color=target[self.pixel(x,y)[1],self.pixel(x,y)[0]].astype(float)
        halves=[];step=max(.7,min(8.,width*.65))
        for sign in (-1,1):
            pos=center.copy();previous=initial*sign;chain=[]
            for _ in range(max(1,math.ceil(length/2/step))):
                v=self.direction(*pos,layer)
                if np.dot(v,previous)<0:v=-v
                v=.84*previous+.16*v;v/=np.linalg.norm(v)+1e-9
                if np.dot(v,initial*sign)<.55:v=previous
                nxt=pos+v*step
                if not(0<=nxt[0]<self.w and 0<=nxt[1]<self.h):break
                ix,iy=self.pixel(*nxt)
                if layer>=2 and np.max(np.abs(target[iy,ix].astype(float)-color))>tolerance:break
                chain.append(nxt.copy());pos=nxt;previous=v
            halves.append(chain)
        points=np.array(list(reversed(halves[0]))+[center]+halves[1])
        if len(points)<2:points=np.array([center-initial*.35,center+initial*.35])
        profile=np.interp(np.linspace(0,1,len(points)),[0,.12,.8,1],[.62,1,1,.50] if layer<3 else [.4,1,.95,.25])
        radii=profile*width/2
        if layer<3:
            points,radii=resample(points,radii,step=max(2.,width*.22))
        return points,radii

    def refine_pass(self,layer,extra=False):
        config={1:dict(sigma=11.,spacing=23.,maxw=62.,minw=16.,length=185.,error=10.,palette=28,opacity=.95,tolerance=999),
                2:dict(sigma=3.2,spacing=9.,maxw=23.,minw=5.5,length=90.,error=8.,palette=96,opacity=.9,tolerance=46),
                3:dict(sigma=0,spacing=3.5,maxw=7.,minw=1.0,length=32.,error=8.5,palette=None,opacity=1.,tolerance=24)}[layer]
        factor=max(.45,max(self.w,self.h)/1100.)
        for key in ('sigma','spacing','maxw','minw','length'):config[key]*=factor
        target=cv2.GaussianBlur(self.src,(0,0),config['sigma']) if config['sigma'] else self.src
        spacing=config['spacing']*(.76 if extra else 1.)
        ys,xs=np.mgrid[spacing*.3:self.h:spacing*.86,spacing*.3:self.w:spacing]
        seeds=np.column_stack((xs.ravel(),ys.ravel()))
        seeds+=self.rng.uniform(-.18,.18,seeds.shape)*spacing
        seeds=np.clip(seeds,[.1,.1],[self.w-1.1,self.h-1.1])
        ix=np.rint(seeds[:,0]).astype(int);iy=np.rint(seeds[:,1]).astype(int)
        errors=np.max(np.abs(self.canvas.astype(float)-target.astype(float)),axis=2)
        # All canvas positions, including white surroundings, remain eligible.
        valid=errors[iy,ix]>config['error']
        seeds=seeds[valid];initial=errors[iy[valid],ix[valid]]
        route,diag=attention_route(seeds,initial,self.w,self.h,window_radius=max(self.w,self.h)*(.20 if layer<3 else .15),bundle_size=10)
        summary={k:v for k,v in diag.items() if k not in ('bundles','bundleIds','relocations')};summary['stage']=STAGES[layer]
        self.routes.append(summary)
        accepted=0
        for position,index in enumerate(route):
            x,y=seeds[index];px,py=self.pixel(x,y)
            current_error=np.max(np.abs(self.canvas[py,px].astype(float)-target[py,px].astype(float)))
            if current_error<config['error']:continue
            width=min(config['maxw'],max(config['minw'],self.edge_distance[py,px]*(1.6 if layer<3 else 1.2)))
            length=config['length']*self.rng.uniform(.82,1.13)
            points,radii=self.gesture(x,y,width,length,layer,target,config['tolerance'])
            left,right=edges(points,radii);fp=self.footprint(left,right)
            if fp is None:continue
            (x0,y0,x1,y1),a=fp
            if config['palette']:
                # Fit the current load to a broad surface, but select an actual
                # original RGB sample from the global palette, never a region.
                weight=a.sum()
                wanted=(target[y0:y1,x0:x1]*a[:,:,None]).sum(axis=(0,1))/max(1,weight)
                center=target[py,px]
                if int(center.min())>246 and int(center.max())-int(center.min())<10:
                    wanted=np.array([255.,255.,255.])
                color,sample=self.choose_color(wanted,None,config['palette'])
            else:color=self.src[py,px].tolist();sample=[px,py]
            old=self.canvas[y0:y1,x0:x1].astype(float);truth=target[y0:y1,x0:x1].astype(float)
            alpha=a[:,:,None]*config['opacity'];new=old*(1-alpha)+np.array(color)*alpha
            before_error=((old-truth)**2).mean(axis=2)
            # A soft loss protects already correct light surroundings. The
            # entire physical brush is accepted or rejected; no pixels are cut.
            weight=1+4*np.exp(-before_error/60.)*(truth.mean(axis=2)/255.)**12 if layer in (1,2) else 1.
            gain=float((((old-truth)**2-(new-truth)**2)*
                        (weight[:,:,None] if isinstance(weight,np.ndarray) else weight)).sum())
            if gain<max(3.,a.sum()*1.8):continue
            intent='plane' if layer==1 else 'turn' if layer==2 else 'edge' if self.contrast[py,px]>8 else 'accent'
            group=f'{layer}-{int(diag["bundleIds"][position])}-{int(extra)}'
            self.put(points,radii,color,sample,layer,intent,config['opacity'],group,fp);accepted+=1
        print(json.dumps(dict(passStage=STAGES[layer],extra=extra,candidates=len(seeds),accepted=accepted)),flush=True)

    def low_frequency_evidence(self):
        result={}
        for sigma in (24,48):
            target=cv2.GaussianBlur(self.src.astype(np.float32),(0,0),sigma)
            painted=cv2.GaussianBlur(self.canvas.astype(np.float32),(0,0),sigma)
            baseline=float(np.abs(255-target).mean());mae=float(np.abs(painted-target).mean())
            result[f'sigma{sigma}']=dict(mae=round(mae,4),whiteBaseline=round(baseline,4),reduction=round(1-mae/max(.01,baseline),4))
        return result

    def paint_phase(self,layer):
        start=len(self.strokes);starttime=self.clock
        complete=True
        if layer==0:complete=self.compose()
        elif layer==1:
            complete=self.construction_sequence(1)
            if complete:self.refine_pass(1)
        else:self.refine_pass(layer)
        evidence=self.low_frequency_evidence()
        if layer==2 and min(v['reduction'] for v in evidence.values())<.62:
            self.refine_pass(layer,extra=True);evidence=self.low_frequency_evidence()
        if layer==3:
            # A final denser global pass restores small lines on the already
            # coherent painting, not tiny islands on untouched paper.
            self.refine_pass(3,extra=True)
            evidence=self.low_frequency_evidence()
        mae=float(np.abs(self.canvas.astype(float)-self.src.astype(float)).mean())
        info=dict(name=PHASES[layer][0],description=PHASES[layer][1],stage=STAGES[layer],startTime=starttime,complete=complete,
                  endTime=self.clock,endIndex=len(self.strokes),added=len(self.strokes)-start,mae=round(mae,4),
                  lowFrequency=evidence,previewDataURL=image_url(self.canvas))
        self.layers.append(info);self.events.append(dict(stage=STAGES[layer],start=starttime,end=self.clock))
        Image.fromarray(self.canvas).save(OUT/f'layer-{layer+1}.png')
        self.export();print(json.dumps(dict(layer=layer+1,added=info['added'],total=len(self.strokes),mae=mae,lowFrequency=evidence)),flush=True)
        return complete

    def export(self):
        report=validate_timeline(self.events,self.strokes,require_complete=len(self.layers)==4)
        data=dict(width=self.w,height=self.h,layers=self.layers,strokes=self.strokes,sourceDataURL=image_url(self.src,720),
                  totalDuration=self.clock,stageEvents=self.events,planValidation=report,constructionReports=self.construction_reports,
                  version=VERSION,constructionBlocked=getattr(self,'construction_blocked',None),
                  automaticGuides=self.guide_metadata,metrics=dict(totalPaths=len(self.strokes),sourceSize=[self.w,self.h],finalMAE=self.layers[-1]['mae'],
                  method='pixel-derived automatic open guides; shared canvas; no hand-authored coordinates; whole brush footprints',
                  brush='true growing ribbon geometry; unmasked paint can cross and cover earlier marks',routeDiagnostics=self.routes))
        (QA/'painting-data.json').write_text(json.dumps(data,separators=(',',':'),ensure_ascii=False),encoding='utf-8')


def generate(source,work_dir,max_edge=768,through=4):
    global OUT,QA
    work_dir=Path(work_dir);OUT=work_dir/'output';QA=work_dir/'qa'
    OUT.mkdir(parents=True,exist_ok=True);QA.mkdir(parents=True,exist_ok=True)
    painter=SharedCanvasPainter(source,max_edge=max_edge)
    (QA/'automatic-guides.json').write_text(json.dumps(dict(gestures=[(p.tolist(),r.tolist()) for p,r in painter.guides],metadata=painter.guide_metadata),ensure_ascii=False,indent=2),encoding='utf-8')
    for layer in range(through):
        started=time.time();complete=painter.paint_phase(layer)
        print(f'Automatic pass {layer+1} rendered in {time.time()-started:.1f}s',flush=True)
        if not complete:break
    painter.export()
    return painter


def main():
    parser=argparse.ArgumentParser(description=f'v{VERSION} automatic portrait painting; no image-specific guide coordinates')
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--work-dir',type=Path,default=ROOT/'generated')
    parser.add_argument('--max-edge',type=int,default=768)
    parser.add_argument('--through',type=int,default=4,choices=range(1,5))
    args=parser.parse_args()
    if not 128<=args.max_edge<=2048:parser.error('--max-edge must be between 128 and 2048')
    painter=generate(args.source,args.work_dir,args.max_edge,args.through)
    if getattr(painter,'construction_blocked',None):
        print(json.dumps(dict(status='incomplete',blocked=painter.construction_blocked)),flush=True)
        raise SystemExit(1)


if __name__=='__main__':main()
