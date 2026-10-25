'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),zlib=require('node:zlib');
const {createClock}=require('./playback_clock.js');
let checks=0;
function check(value,message){assert.ok(value,message);checks++;}
for(const raw of [100,100000,48000000])for(const overview of [60000,240000,900000])for(const curve of [0,1,99,10000]){
  const clock=createClock(raw,overview,curve);
  for(const mode of ['log','real']){
    check(clock.rawToPresentation(0,mode)===0,'zero maps exactly');
    check(clock.presentationToRaw(clock.duration(mode),mode)===raw,'end maps exactly');
    let prev=-1;
    for(let i=0;i<=100;i++){
      const s=raw*i/100,t=clock.rawToPresentation(s,mode),back=clock.presentationToRaw(t,mode);
      check(Math.abs(back-s)<Math.max(1e-7,raw*1e-12),'mapping is reversible');
      check(t>prev,'mapping is strictly monotonic');prev=t;
    }
    check(clock.advance(raw,1000,mode)===raw,'advance clamps final boundary');
  }
}
const clock=createClock(48000000,240000,99);
const early=clock.advance(0,1000),late=clock.advance(24000000,1000)-24000000;
check(late>early*20,'log pacing accelerates toward later strokes');
class MockContext{
  constructor(){this.ops=[];this.globalAlpha=1;this.fillStyle='';this.globalCompositeOperation='source-over';}
  fillRect(){this.ops=[];}
  fill(path){this.ops.push({d:path.d,color:this.fillStyle,opacity:this.globalAlpha});}
  drawImage(canvas){this.ops=canvas.getContext('2d').ops.slice();}
}
class MockPath2D{constructor(d){this.d=d;}}
class Node extends EventTarget{
  constructor(tag='div'){super();this.tagName=tag;this.childNodes=[];this.attributes={};this.parentNode=null;this.value='';this.textContent='';this.style={setProperty(){}};this.hidden=false;}
  setAttribute(k,v){this.attributes[k]=String(v);}getAttribute(k){return this.attributes[k]??null;}removeAttribute(k){delete this.attributes[k];}
  appendChild(node){if(node.tagName==='#fragment'){while(node.firstChild)this.appendChild(node.firstChild);return node;}if(node.parentNode)node.parentNode.removeChild(node);this.childNodes.push(node);node.parentNode=this;return node;}
  removeChild(node){const i=this.childNodes.indexOf(node);if(i<0)throw new Error('missing child');this.childNodes.splice(i,1);node.parentNode=null;return node;}
  replaceChildren(...nodes){while(this.firstChild)this.removeChild(this.firstChild);nodes.forEach(n=>this.appendChild(n));}
  get firstChild(){return this.childNodes[0]||null;}get lastChild(){return this.childNodes[this.childNodes.length-1]||null;}
  matches(){return false;}click(){this.dispatchEvent(new Event('click'));}
  getContext(){return this.context||(this.context=new MockContext());}
}
const template=fs.readFileSync(__dirname+'/player_v7.html','utf8');
const ids=[...template.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]),nodes=Object.fromEntries(ids.map(id=>[id,new Node()]));
nodes.reference.hidden=true;
nodes['generator-source'].textContent=JSON.stringify({encoding:'plain',filename:'test.py'});
const document=new EventTarget();document.getElementById=id=>{assert.ok(nodes[id],id+' exists');return nodes[id];};document.createElement=tag=>new Node(tag);document.createElementNS=(_,tag)=>new Node(tag);document.createDocumentFragment=()=>new Node('#fragment');
const strokes=Array.from({length:850},(_,i)=>({start:i*300,duration:200,pauseAfter:100,layer:0,color:'#446655',opacity:1,points:[[i,0],[i+10,0],[i+20,0]],left:[[i,-3],[i+10,-3],[i+20,-3]],right:[[i,3],[i+10,3],[i+20,3]],d:`M${i},-3 L${i+20},-3 L${i+20},3 L${i},3 Z`}));
const makeData=(n,label)=>({width:600,height:800,strokes:strokes.slice(0,n),totalDuration:n*300,layers:[{name:label}],sourceDataURL:'data:image/png;base64,AA==',constructionBlocked:n===10?{reason:'repair_budget_exhausted_before_coarse_ready'}:null});
const pack=data=>({encoding:'gzip-base64',payload:zlib.gzipSync(JSON.stringify(data)).toString('base64')});
const bundle={version:'7.0',paintings:[{id:'one',label:'人物一',data:pack(makeData(850,'底形'))},{id:'two',label:'人物二',data:pack(makeData(10,'另一底形'))}]};
nodes['painting-data'].textContent=JSON.stringify(bundle);
let frameId=0;const frames=new Map(),events=new EventTarget();
let decompressions=0;
function CountedDecompressionStream(format){decompressions++;return new DecompressionStream(format);}
const context={console,document,performance,Event,AbortController,setTimeout,clearTimeout,Blob,Response,DecompressionStream:CountedDecompressionStream,Path2D:MockPath2D,atob,Uint8Array,URL,requestAnimationFrame:fn=>{frames.set(++frameId,fn);return frameId;},cancelAnimationFrame:id=>frames.delete(id),dispatchEvent:events.dispatchEvent.bind(events)};
context.window=context;context.globalThis=context;vm.createContext(context);
function playerScript(text){const match=text.match(/<script>\s*([\s\S]*?)<\/script>/);assert.ok(match,'inline player script exists');return match[1];}
let script=playerScript(template);
check(playerScript(template.replace(/\r?\n/g,'\r\n')).replace(/\r\n/g,'\n')===script.replace(/\r\n/g,'\n'),'script extraction handles Windows CRLF');
const firstReady=new Promise(resolve=>events.addEventListener('paintingready',resolve,{once:true}));
vm.runInContext(script,context);
(async()=>{
  await firstReady;
  check(!context.paintingError,'player initializes without error');
  check(decompressions===1,'only the selected example is decompressed on load');
  check(nodes['painting-data'].textContent==='','large inert text is released after parsing');
  let api=context.paintingAPI;await api.ready;
  check(api.getState().playbackMode==='log','log mode is default');
  check(api.getState().presentationDuration===240000,'default overview is 240 seconds');
  check(nodes['sample-select'].childNodes.length===2,'bundle exposes example choices');
  await api.renderAt(300*100+100);
  let state=api.getState();check(state.completed===100&&state.activeIndex===100&&state.activeCount===1,'one partial stroke follows completed prefix');
  const painted=nodes.strokes.childNodes,active=painted.filter(p=>p.getAttribute('data-active')==='true');
  check(active.length===1,'DOM contains at most one active stroke');
  check(painted.length===1,'completed marks do not create SVG DOM nodes');
  check(state.cachedStrokes===100&&nodes['paint-cache'].getContext('2d').ops.length===100,'only completed marks are cached as Path2D fills');
  check(nodes['paint-cache'].getContext('2d').ops[0].d===strokes[0].d&&nodes['paint-cache'].getContext('2d').ops[0].color===strokes[0].color,'cache is drawn from original stroke geometry and color');
  check(active[0].getAttribute('d')!==strokes[100].d,'active geometry is only a prefix');
  const fixed=state.time;
  api.setPlaybackMode('real');check(api.getState().time===fixed,'switching to original time preserves canvas');
  api.setOverviewDuration(60);check(api.getState().time===fixed,'duration change preserves canvas');
  api.setPlaybackMode('log');check(api.getState().time===fixed,'switching to log time preserves canvas');
  check(api.getState().presentationDuration===60000,'overview duration is applied');
  api.toggleReference();check(nodes.painting.getAttribute('hidden')===''&&!nodes.reference.hidden,'reference mode hides SVG using an actual attribute');
  api.toggleReference();check(nodes.painting.getAttribute('hidden')===null&&nodes.reference.hidden,'painting returns without changing time');
  await api.renderAt(api.data.totalDuration);state=api.getState();check(state.completed===850&&state.activeCount===0,'last frame is exactly complete');
  await api.renderAt(0);check(nodes['paint-cache'].getContext('2d').ops.length===0&&api.getState().completed===0,'reverse seek fully removes cached marks');
  await api.next();check(api.getState().completed===1,'next advances one stroke');
  await api.prev();check(api.getState().time===0,'previous returns to prior stroke start');
  const oldApi=api;
  const secondReady=new Promise(resolve=>events.addEventListener('paintingready',resolve,{once:true}));
  nodes['sample-select'].value='1';nodes['sample-select'].dispatchEvent(new Event('change'));
  await secondReady;
  check(decompressions===2,'a second example is decompressed only after selection');
  check(oldApi.getState().disposed&&oldApi.getState().mountedPaths===0,'previous paths and geometry state are disposed');
  api=context.paintingAPI;check(api.getState().strokeCount===10&&api.getState().completed===0,'example switch resets to new canvas');
  check(!nodes['completion-note'].hidden,'blocked partial process is visibly identified');
  await api.renderAt(api.data.totalDuration);check(nodes['phase-label'].textContent.includes('粗层未完成'),'partial end is not labelled finished painting');
  await api.renderAt(0);
  nodes.play.click();check(api.getState().playing,'a single play listener remains after example switch');
  api.pause();check(frames.size===0,'pause cancels animation callback');
  // A large forward frame yields between chunks. A quick pause/play during
  // that yield must not let the old tick schedule a second playback loop.
  api.setPlaybackMode('real');
  let fakeNow=0;context.performance={now:()=>fakeNow+=8};
  api.play();
  const [callbackId,callback]=frames.entries().next().value;frames.delete(callbackId);
  const oldTick=callback(2800);
  check(api.getState().rendering,'playback is awaiting a chunked render');
  api.pause();api.play();
  await oldTick;
  check(frames.size===1,'an obsolete tick cannot duplicate the resumed playback loop');
  api.pause();check(frames.size===0,'pause cancels the sole resumed animation callback');
  console.log(JSON.stringify({passed:checks,clockInversion:true,modeSwitchPreservesRawTime:true,strictSingleActive:true,bundleSwitch:true,lazyDecompression:true,previousStateDisposed:true,pauseResumeDuringRender:true,browserTest:false}));
})().catch(error=>{console.error(error);process.exitCode=1;});
