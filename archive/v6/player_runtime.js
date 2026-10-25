'use strict';
// BEGIN SHARED GEOMETRY PREFIX
'use strict';
// The same pure geometry is embedded in the offline HTML and required by the
// independent SVG renderer. No complete future shape is used for revelation.
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.RibbonPrefix=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  function geometry(stroke){
    const points=stroke.points,left=stroke.left,right=stroke.right;
    if(!Array.isArray(points)||points.length<2||!Array.isArray(left)||!Array.isArray(right)||left.length!==points.length||right.length!==points.length)throw new Error('笔触缺少对应的中心线与左右笔缘采样');
    let lengths;
    if(Array.isArray(stroke.lengths)&&stroke.lengths.length===points.length&&stroke.lengths.every((v,i,a)=>Number.isFinite(v)&&(i===0||v>=a[i-1]))&&stroke.lengths[stroke.lengths.length-1]>stroke.lengths[0])lengths=stroke.lengths.map(v=>v-stroke.lengths[0]);
    else{
      lengths=[0];
      for(let i=1;i<points.length;i++)lengths.push(lengths[i-1]+Math.hypot(points[i][0]-points[i-1][0],points[i][1]-points[i-1][1]));
    }
    // A stationary dab still advances its boundary samples in their order.
    if(lengths[lengths.length-1]===0)lengths=points.map((_,i)=>i);
    return{points,left,right,lengths,length:lengths[lengths.length-1]};
  }
  function partialRibbonD(stroke,progress,prepared){
    const p=Math.max(0,Math.min(1,Number(progress)||0));
    if(p<=0)return'';
    if(p>=1)return stroke.d;
    const g=prepared||geometry(stroke),distance=p*g.length;
    let cut=1;
    while(cut<g.lengths.length-1&&g.lengths[cut]<distance)cut++;
    const fraction=(distance-g.lengths[cut-1])/(g.lengths[cut]-g.lengths[cut-1]||1);
    const interpolate=(a,b)=>[a[0]+(b[0]-a[0])*fraction,a[1]+(b[1]-a[1])*fraction];
    const left=g.left.slice(0,cut),right=g.right.slice(0,cut);
    left.push(interpolate(g.left[cut-1],g.left[cut]));
    right.push(interpolate(g.right[cut-1],g.right[cut]));
    return'M'+left.concat(right.reverse()).map(point=>String(point[0])+','+String(point[1])).join(' L')+' Z';
  }
  return{geometry,partialRibbonD};
});

// END SHARED GEOMETRY PREFIX
// The envelope can be the original object, or {encoding:'gzip-base64',payload:'...'}.
// Native SVG keeps every mark as a path. No canvas, image or raster snapshot is
// ever inserted in the painting. Static reference previews are separate UI.
let data = __PAINTING_DATA__;
(async () => {
  const $ = id => document.getElementById(id);
  async function decodePayload(value) {
    if(value.encoding!=='gzip-base64')return value;
    if(typeof DecompressionStream==='undefined')throw new Error('此文件需要支持原生 gzip 解压的现代 Chrome 或 Edge。');
    $('loading').firstChild.textContent='正在解压逐笔画谱';
    const binary=atob(value.payload);
    const bytes=new Uint8Array(binary.length);
    for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);
    const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
    return await new Response(stream).json();
  }
  data=await decodePayload(data);
  const prefixLib=globalThis.RibbonPrefix;
  const svg=$('painting'),group=$('strokes'),NS='http://www.w3.org/2000/svg';
  const strokes=data.strokes,total=data.totalDuration;
  $('pen-overlay').setAttribute('viewBox',`0 0 ${data.width} ${data.height}`);
  let cursorEnabled=true;const cursorGeometry=new Map();
  let camera=[0,0,data.width,data.height];
  function setViewBox(values){camera=values;const text=values.join(' ');svg.setAttribute('viewBox',text);$('pen-overlay').setAttribute('viewBox',text);$('full-view').disabled=values[0]===0&&values[1]===0&&values[2]===data.width&&values[3]===data.height;return camera.slice();}
  function resetView(){return setViewBox([0,0,data.width,data.height]);}
  function focusGroup(first,last){
    let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
    for(let index=first;index<=last;index++){const stroke=strokes[index],radius=stroke.width/2;for(const point of geometry(index).points){minX=Math.min(minX,point[0]-radius);minY=Math.min(minY,point[1]-radius);maxX=Math.max(maxX,point[0]+radius);maxY=Math.max(maxY,point[1]+radius);}}
    if(!Number.isFinite(minX))return resetView();
    const margin=Math.max(18,Math.max(maxX-minX,maxY-minY)*.12);minX=Math.max(0,minX-margin);minY=Math.max(0,minY-margin);maxX=Math.min(data.width,maxX+margin);maxY=Math.min(data.height,maxY+margin);
    const ratio=data.width/data.height;let width=Math.max(maxX-minX,(maxY-minY)*ratio,180,240*ratio);width=Math.min(data.width,width);const height=width/ratio;
    const x=Math.max(0,Math.min(data.width-width,(minX+maxX-width)/2)),y=Math.max(0,Math.min(data.height-height,(minY+maxY-height)/2));
    return setViewBox([x,y,width,height]);
  }


  // Only the currently visible prefix owns DOM nodes. Future marks stay data.
  // Whole groups are removed on reverse seek, bounding individual removals.
  const CHUNK_SIZE=768,BUILD_BUDGET_MS=7,SYNC_LIMIT=512;
  let paths=[],chunks=[],mountedCount=0;
  let time=0,completed=0,currentActive=-1;
  let playing=false,request=0,lastFrame=0,speed=1,groupStopAt=null;
  let renderVersion=0,pendingTime=null,pendingPromise=Promise.resolve();
  const formatNumber=n=>n.toLocaleString('zh-CN');
  const formatTime=ms=>{const n=Math.floor(Math.max(0,ms)/100);return String(Math.floor(n/600)).padStart(2,'0')+':'+String(Math.floor(n/10)%60).padStart(2,'0')+'.'+n%10;};
  svg.setAttribute('viewBox',`0 0 ${data.width} ${data.height}`);
  $('artboard').style.setProperty('--ratio',`${data.width}/${data.height}`);
  $('artboard').style.setProperty('--image-width',data.width+'px');
  $('artboard').style.setProperty('--image-height',data.height+'px');
  $('total-strokes').textContent=formatNumber(strokes.length);
  $('duration').textContent=formatTime(total);$('seek').max=total;
  $('reference').src=data.sourceDataURL;
  const stageChecks=data.layers.map((layer,index)=>{
    const firstIndex=index===0?0:data.layers[index-1].endIndex,previousEnd=index===0?0:data.layers[index-1].endTime;
    const start=layer.endIndex>firstIndex&&strokes[firstIndex]?strokes[firstIndex].start:previousEnd;
    return{layer:index,name:layer.name,start,endTime:layer.endTime,previousComplete:start>=previousEnd};
  });
  function updateIntent(frame){
    const index=frame.active>=0?frame.active:frame.count>0?frame.count-1:0,stroke=strokes[index],layer=Number(stroke?.layer||0),check=stageChecks[layer];
    $('intent-label').textContent=frame.phase==='done'?'全图完成':stroke?.intentLabel||'先铺整体关系，再逐步加深';
    $('stage-prerequisite').textContent=frame.phase==='done'?'全部阶段完成':layer===0?'白纸开始':check?.previousComplete?'前阶段已完成':'阶段顺序待核';
  }
  const stageButtons=data.layers.map((layer,i)=>{
    const button=document.createElement('button');button.className='stage';button.setAttribute('title',`跳到${layer.name}完成时`);button.setAttribute('data-stage-ready',String(stageChecks[i].previousComplete));
    const number=document.createElement('span');number.className='stage-num';number.textContent=String(i+1).padStart(2,'0');
    const label=document.createElement('span'),strong=document.createElement('strong'),small=document.createElement('small');strong.textContent=layer.name;small.textContent=layer.description;const gate=document.createElement('small');gate.className='stage-gate';gate.textContent=i===0?'从白纸开始':stageChecks[i].previousComplete?'✓ 接续前阶段':'阶段顺序待核';label.append(strong,small,gate);
    const arrow=document.createElement('span');arrow.className='stage-arrow';arrow.textContent='↗';button.append(number,label,arrow);
    button.addEventListener('click',()=>{pause();renderAt(layer.endTime);$('reference-select').value=i;showReference();});$('stages').appendChild(button);
    const option=document.createElement('option');option.value=i;option.textContent=`第 ${i+1} 层 · ${layer.name}`;$('reference-select').appendChild(option);return button;
  });
  function showReference(){const selection=$('reference-select').value,source=selection==='source',layer=source?null:data.layers[Number(selection)];$('reference').src=source?data.sourceDataURL:layer.previewDataURL;$('reference').alt=source?'原图参考':`${layer.name}完成时的逐笔绘制预览`;$('reference-caption').textContent=source?'此处为原图参考；左侧画布从白纸逐笔重建。':`${layer.name} · ${formatNumber(layer.endIndex)} 笔累计。此图为该层完成后的实际渲染。`;}
  $('reference-select').addEventListener('change',showReference);
  function addGalleryCard(layer,index){const source=layer===null,card=document.createElement('button');card.className='gallery-card';card.type='button';const frame=document.createElement('span');frame.className='gallery-image';const image=document.createElement('img');image.src=source?data.sourceDataURL:layer.previewDataURL;image.alt=source?'原图':`${layer.name}完成后的渲染`;image.loading='lazy';frame.appendChild(image);const label=document.createElement('strong');label.textContent=source?'原图':`${String(index+1).padStart(2,'0')} · ${layer.name}`;const caption=document.createElement('small'),mae=source?null:Number(layer.mae);caption.textContent=source?'颜色与结构采样来源':`${formatNumber(layer.endIndex)} 笔累计${Number.isFinite(mae)?' · MAE '+mae.toFixed(2):''}`;card.append(frame,label,caption);card.addEventListener('click',()=>{if(!source){pause();renderAt(layer.endTime);}$('reference-select').value=source?'source':index;showReference();});$('gallery-grid').appendChild(card);}
  addGalleryCard(null,-1);data.layers.forEach(addGalleryCard);
  // A compressed source envelope avoids HTML closing-script delimiter hazards in
  // self-contained Python that itself contains this full HTML template.
  async function generatorSource(){
    const raw=$('generator-source').textContent;
    if(!raw.trim()||raw.includes('__GENERATOR'+'_'+'SOURCE__'))throw new Error('生成算法尚未内嵌');
    let envelope=null;
    if(raw.trim().startsWith('{')){try{envelope=JSON.parse(raw);}catch{}}
    if(envelope&&envelope.encoding==='gzip-base64'){
      if(typeof DecompressionStream==='undefined')throw new Error('此浏览器不支持原生 gzip 解压');
      const binary=atob(envelope.payload),bytes=new Uint8Array(binary.length);
      for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);
      const source=await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).text();
      return{source,filename:envelope.filename||'build_painting.py'};
    }
    return{source:raw,filename:'build_painting.py'};
  }
  $('download-generator').addEventListener('click',async()=>{
    const button=$('download-generator'),old=button.textContent;button.disabled=true;button.textContent='正在准备源码…';
    try{const result=await generatorSource();const url=URL.createObjectURL(new Blob([result.source],{type:'text/x-python;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=result.filename.split(/[\\/]/).pop()||'build_painting.py';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);button.textContent=old;}
    catch(error){console.error(error);button.textContent=error.message;}
    finally{button.disabled=false;}
  });
  $('gallery-grid').style.setProperty('--gallery-columns',String(data.layers.length+1));
  function upperBoundEnd(t){let lo=0,hi=strokes.length;while(lo<hi){const m=(lo+hi)>>>1,s=strokes[m];if(s.start+s.duration<=t)lo=m+1;else hi=m;}return lo;}
  function targetFrame(value){
    const v=Number(value),t=Math.max(0,Math.min(total,Number.isFinite(v)?v:0)),count=upperBoundEnd(t);
    let active=-1,progress=0;
    if(count<strokes.length){const next=strokes[count];if(t>=next.start&&t<next.start+next.duration){active=count;progress=(t-next.start)/next.duration;}}
    const phase=t===0?'idle':t>=total?'done':active>=0?'drawing':count>0?'lift':'idle';
    return{t,count,active,progress,phase,needed:count+(active>=0&&progress>0?1:0)};
  }
  function createPath(index){
    const stroke=strokes[index],path=document.createElementNS(NS,'path');
    // The actual filled geometry grows from its stored left/right boundaries.
    // No future part of a full ribbon is present in the active drawing path.
    path.setAttribute('d',stroke.d);path.setAttribute('fill',stroke.color);path.setAttribute('stroke','none');path.setAttribute('opacity',stroke.opacity===undefined?1:stroke.opacity);
    path.setAttribute('data-start',stroke.start);path.setAttribute('data-duration',stroke.duration);path.setAttribute('data-pause-after',stroke.pauseAfter||0);path.setAttribute('data-index',index);path.setAttribute('data-layer',stroke.layer);path.setAttribute('data-region',stroke.region);path.setAttribute('data-type',stroke.type||'sweep');
    if(stroke.phase!==undefined)path.setAttribute('data-phase',stroke.phase);if(stroke.patch!==undefined)path.setAttribute('data-patch',stroke.patch);if(stroke.group!==undefined)path.setAttribute('data-group',stroke.group);
    return path;
  }
  function clearActive(path,index){if(!path)return;path.removeAttribute('data-active');path.setAttribute('d',strokes[index].d);}
  function geometry(index){
    if(cursorGeometry.has(index))return cursorGeometry.get(index);
    const value=prefixLib.geometry(strokes[index]);cursorGeometry.set(index,value);if(cursorGeometry.size>4)cursorGeometry.delete(cursorGeometry.keys().next().value);return value;
  }
  function partialRibbonD(index,progress){return prefixLib.partialRibbonD(strokes[index],progress,geometry(index));}
  function pointAt(index,progress){
    const g=geometry(index),distance=Math.max(0,Math.min(1,progress))*g.length;
    let i=1;while(i<g.lengths.length-1&&g.lengths[i]<distance)i++;
    if(g.points.length===1)return g.points[0];
    const fraction=(distance-g.lengths[i-1])/(g.lengths[i]-g.lengths[i-1]||1),a=g.points[i-1],b=g.points[i];return[a[0]+(b[0]-a[0])*fraction,a[1]+(b[1]-a[1])*fraction];
  }
  function updateCursor(frame){
    const cursor=$('pen-cursor');
    if(!cursorEnabled||frame.t===0||frame.phase==='done'||!strokes.length){cursor.style.display='none';return;}
    let position=null,state='down';
    if(frame.active>=0)position=pointAt(frame.active,frame.progress);
    else if(frame.count>0){
      state='lift';const previous=strokes[frame.count-1],from=pointAt(frame.count-1,1),next=strokes[frame.count];
      if(next){const to=pointAt(frame.count,0),start=previous.start+previous.duration,ratio=Math.max(0,Math.min(1,(frame.t-start)/(next.start-start||1))),p=ratio*ratio*(3-2*ratio);position=[from[0]+(to[0]-from[0])*p,from[1]+(to[1]-from[1])*p];}else position=from;
    }
    if(!position){cursor.style.display='none';return;}
    cursor.style.display='';cursor.setAttribute('transform',`translate(${position[0].toFixed(3)} ${position[1].toFixed(3)})`);cursor.setAttribute('data-state',state);
  }
  function getState(){return{time,totalDuration:total,completed,activeIndex:currentActive,activeCount:currentActive>=0&&time>strokes[currentActive].start?1:0,phase:targetFrame(time).phase,cursorEnabled,groupStopAt,viewBox:camera.slice(),playing,speed,strokeCount:strokes.length,mountedPaths:mountedCount,pendingTime,rendering:pendingTime!==null};}
  function updateFrameUI(frame){$('seek').value=frame.t;$('seek').setAttribute('aria-valuetext',`${formatTime(frame.t)}，已完成 ${frame.count} 笔`);$('elapsed').textContent=formatTime(frame.t);$('live-count').textContent=`${formatNumber(frame.count)} 笔已完成`;$('stroke-position').textContent=`第 ${formatNumber(frame.active>=0?frame.active+1:frame.count)} / ${formatNumber(strokes.length)} 笔`;const labelStroke=frame.active>=0&&frame.progress>0?frame.active:(frame.count>0?frame.count-1:frame.active);let layerIndex=labelStroke>=0?Number(strokes[labelStroke].layer):-1;if(!Number.isInteger(layerIndex)||layerIndex<0||layerIndex>=data.layers.length){layerIndex=data.layers.findIndex(l=>frame.count<l.endIndex);if(layerIndex<0&&frame.count>0)layerIndex=data.layers.length-1;}const labelData=labelStroke>=0?strokes[labelStroke]:null;const phaseName=data.layers[layerIndex]?.name||labelData?.phase||'逐笔绘制';const patch=labelData?.patch;const patchHint='';const gesture=frame.phase==='lift'?'抬笔':frame.phase==='drawing'?(frame.progress<.12?'落笔':frame.progress>.85?'收笔':'行笔'):'';$('phase-label').textContent=frame.t===0?'白纸 / 尚未落笔':frame.t===total?'绘制完成':`第 ${layerIndex+1} 层 / ${phaseName}${patchHint}${gesture?' · '+gesture:''}`;stageButtons.forEach((b,i)=>{b.classList.toggle('current',i===layerIndex&&frame.t>0);b.setAttribute('aria-current',i===layerIndex&&frame.t>0?'step':'false');});$('prev').disabled=frame.t<=0;$('next').disabled=frame.count>=strokes.length;}
  function setPending(t,n){pendingTime=t;$('seek-status').textContent=t===null?'':`正在定位 ${formatTime(t)}${n?' · 已准备 '+formatNumber(n)+' 笔':''}`;$('artwell').setAttribute('aria-busy',String(t!==null));}
  function commitFrame(frame,stage,version){
    if(version!==renderVersion)return{...getState(),cancelled:true};
    // All following mutations run in one task; no intermediate paint can occur.
    clearActive(paths[currentActive],currentActive);
    if(frame.needed<mountedCount){
      const keepChunks=Math.ceil(frame.needed/CHUNK_SIZE);
      while(chunks.length>keepChunks)group.removeChild(chunks.pop());
      if(keepChunks){const last=chunks[keepChunks-1],keep=frame.needed-(keepChunks-1)*CHUNK_SIZE;while(last.childNodes.length>keep)last.removeChild(last.lastChild);}
      paths.length=frame.needed;
    }
    if(stage){
      for(const part of stage.parts){const existing=chunks[part.index];if(existing){const fragment=document.createDocumentFragment();while(part.group.firstChild)fragment.appendChild(part.group.firstChild);existing.appendChild(fragment);}else{chunks[part.index]=part.group;group.appendChild(part.group);}}
      for(let i=0;i<stage.nodes.length;i++)paths[stage.start+i]=stage.nodes[i];
    }
    mountedCount=frame.needed;
    if(frame.active>=0&&frame.progress>0){
      const path=paths[frame.active];path.setAttribute('d',partialRibbonD(frame.active,frame.progress));path.setAttribute('data-active','true');
    }
    time=frame.t;completed=frame.count;currentActive=frame.active;setPending(null);updateFrameUI(frame);updateIntent(frame);updateCursor(frame);return getState();
  }
  function newStage(start){return{start,nodes:[],parts:[]};}
  function addStagedPath(stage,index){const chunkIndex=Math.floor(index/CHUNK_SIZE);let part=stage.parts[stage.parts.length-1];if(!part||part.index!==chunkIndex){part={index:chunkIndex,group:document.createElementNS(NS,'g')};part.group.setAttribute('data-chunk',chunkIndex);stage.parts.push(part);}const p=createPath(index);part.group.appendChild(p);stage.nodes.push(p);}
  async function buildFrame(frame,version){
    const stage=newStage(mountedCount),start=mountedCount;
    let deadline=performance.now()+BUILD_BUDGET_MS;
    for(let i=start;i<frame.needed;i++){
      if(version!==renderVersion)return{...getState(),cancelled:true};
      addStagedPath(stage,i);
      if((i-start)%64===63&&performance.now()>=deadline){setPending(frame.t,i-start+1);await new Promise(resolve=>setTimeout(resolve,0));deadline=performance.now()+BUILD_BUDGET_MS;}
    }
    return commitFrame(frame,stage,version);
  }
  // Pure frame value: completed prefix plus at most one extending path.
  // For a long seek, only the computation is asynchronous; old frame stays
  // visible until the complete target is atomically committed. Await this
  // function (or whenRendered()) before inspecting the target SVG.
  function renderAt(value){
    const frame=targetFrame(value),version=++renderVersion;
    const missing=frame.needed-mountedCount;
    if(missing>SYNC_LIMIT){setPending(frame.t);pendingPromise=buildFrame(frame,version);return pendingPromise;}
    let stage=null;
    if(missing>0){stage=newStage(mountedCount);for(let i=mountedCount;i<frame.needed;i++)addStagedPath(stage,i);}
    const result=commitFrame(frame,stage,version);pendingPromise=Promise.resolve(result);return pendingPromise;
  }
  function cancelPending(){if(pendingTime!==null){renderVersion++;setPending(null);pendingPromise=Promise.resolve(getState());}}
  function updatePlay(){$('play-label').textContent=playing?'暂停':'播放';$('play-icon').innerHTML=playing?'<path d="M3 2h4v12H3zM9 2h4v12H9z"/>':'<path d="m4 2 10 6-10 6z"/>';$('play').setAttribute('aria-label',playing?'暂停':'播放');$('play').setAttribute('aria-pressed',String(playing));}
  async function tick(now){if(!playing)return;const delta=Math.max(0,now-lastFrame);lastFrame=now;const stop=groupStopAt===null?total:groupStopAt;await renderAt(Math.min(stop,time+delta*speed));if(!playing)return;if(time>=stop){pause();return;}request=requestAnimationFrame(tick);}
  function play(){if(playing)return;cancelPending();if(time>=total)renderAt(0);playing=true;lastFrame=performance.now();updatePlay();request=requestAnimationFrame(tick);}
  function pause(){playing=false;groupStopAt=null;cancelAnimationFrame(request);request=0;cancelPending();updatePlay();}
  async function playRange(start,end){
    pause();const startValue=Number(start),endValue=Number(end),from=Math.max(0,Math.min(total,Number.isFinite(startValue)?startValue:0)),to=Math.max(from,Math.min(total,Number.isFinite(endValue)?endValue:total));
    const requestedVersion=renderVersion+1;await renderAt(from);if(renderVersion!==requestedVersion)return{...getState(),cancelled:true};
    speed=1;$('speed').value='1';groupStopAt=to;if(to>from)play();else groupStopAt=null;return getState();
  }
  async function inspectStrokeRange(first=80,last=210){
    pause();resetView();if(!strokes.length)return getState();
    const from=Math.max(0,Math.min(strokes.length-1,Math.floor(Number(first)||80)-1)),to=Math.max(from,Math.min(strokes.length-1,Math.floor(Number(last)||210)-1));
    return playRange(strokes[from].start,strokes[to].start+strokes[to].duration);
  }
  async function showStrokeState(count){
    pause();resetView();const n=Math.max(0,Math.min(strokes.length,Math.floor(Number(count)||0)));
    await renderAt(n===0?0:strokes[n-1].start+strokes[n-1].duration);return getState();
  }
  async function playGroup(){
    pause();if(!strokes.length)return getState();
    const frame=targetFrame(time),anchor=frame.active>=0&&frame.progress>0?frame.active:frame.count>0?frame.count-1:0;
    const key=(s,index)=>String(s.layer)+'|'+String(s.group===undefined?(s.patch===undefined?'stroke-'+index:s.patch):s.group);
    const wanted=key(strokes[anchor],anchor);let first=anchor,last=anchor;
    while(first>0&&key(strokes[first-1],first-1)===wanted)first--;
    while(last+1<strokes.length&&key(strokes[last+1],last+1)===wanted)last++;
    const requestedVersion=renderVersion+1;await renderAt(strokes[first].start);if(renderVersion!==requestedVersion)return{...getState(),cancelled:true};
    focusGroup(first,last);speed=1;$('speed').value='1';groupStopAt=strokes[last].start+strokes[last].duration;play();return getState();
  }
  function next(){pause();const index=upperBoundEnd(time);return index<strokes.length?renderAt(strokes[index].start+strokes[index].duration):Promise.resolve(getState());}
  function prev(){pause();const count=upperBoundEnd(time);if(count<strokes.length&&time>strokes[count].start)return renderAt(strokes[count].start);if(count>0)return renderAt(strokes[count-1].start);return renderAt(0);}
  $('inspect-80-210').disabled=strokes.length<80;
  $('inspect-80-210').addEventListener('click',()=>inspectStrokeRange());
  [100,140,185,210].forEach(count=>{const button=$('inspect-state-'+count);button.disabled=strokes.length<count;button.addEventListener('click',()=>showStrokeState(count));});
  $('play-base').addEventListener('click',()=>playRange(0,data.layers[0]?.endTime||total));
  $('play-base-shadow').addEventListener('click',()=>playRange(0,data.layers[Math.min(1,data.layers.length-1)]?.endTime||total));
  $('full-view').addEventListener('click',resetView);
  $('play-group').addEventListener('click',playGroup);
  $('cursor-toggle').addEventListener('click',()=>{cursorEnabled=!cursorEnabled;$('cursor-toggle').setAttribute('aria-pressed',String(cursorEnabled));$('cursor-toggle').textContent=cursorEnabled?'笔尖：显示':'笔尖：隐藏';updateCursor(targetFrame(time));});
  $('play').addEventListener('click',()=>playing?pause():play());$('prev').addEventListener('click',prev);$('next').addEventListener('click',next);
  $('seek').addEventListener('input',event=>{pause();renderAt(event.target.value);});$('speed').addEventListener('change',event=>{speed=Number(event.target.value);lastFrame=performance.now();});$('restart').addEventListener('click',()=>{pause();renderAt(0);});$('fit').addEventListener('click',()=>{$('artwell').classList.toggle('actual-size');});
  document.addEventListener('keydown',event=>{if(event.target.matches('input,select,textarea,button')||event.altKey||event.ctrlKey||event.metaKey)return;if(event.code==='Space'){event.preventDefault();playing?pause():play();}else if(event.key==='ArrowRight'){event.preventDefault();next();}else if(event.key==='ArrowLeft'){event.preventDefault();prev();}else if(event.key==='Home'){event.preventDefault();pause();renderAt(0);}else if(event.key==='End'){event.preventDefault();pause();renderAt(total);}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)pause();});
  const ready=renderAt(0);
  window.paintingAPI={renderAt,data,stageChecks,getState,play,pause,next,prev,playGroup,playRange,inspectStrokeRange,showStrokeState,resetView,partialRibbonD,ready,whenRendered:()=>pendingPromise,getGeneratorSource:generatorSource};
  await ready;updatePlay();$('loading').hidden=true;window.dispatchEvent(new Event('paintingready'));
})().catch(error=>{console.error(error);document.getElementById('loading').textContent='画谱加载失败：'+error.message;window.paintingError=error.message;});
