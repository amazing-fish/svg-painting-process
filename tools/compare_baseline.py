"""Compare fixed gallery runs at equal original times, using real SVG geometry.

Outputs remain local. The HTML contains vector replay, not video or screenshots.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import re

from export_svg import export_svg

LOCATORS = (100, 140, 185, 210)


def read_manifest(path):
    path = Path(path).resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    inputs = [(record["relativePath"], record["sha256"]) for record in manifest["inputs"]]
    if not inputs or len(set(inputs)) != len(inputs):
        raise ValueError("Manifest inputs must be nonempty and unique")
    results = {(record["relativePath"], record["sha256"]): record for record in manifest["results"]}
    if len(results) != len(manifest["results"]) or set(results) != set(inputs):
        raise ValueError("Manifest results must match every input exactly")
    return path, manifest, inputs, results


def load_case(manifest_path, result):
    if result["status"] not in ("generated", "incomplete") or not result.get("output"):
        return None
    root = manifest_path.parent
    folder = (root / result["output"]).resolve()
    if root not in folder.parents:
        raise ValueError("Result output must stay inside its manifest directory")
    return json.loads((folder / "qa" / "painting-data.json").read_text(encoding="utf-8"))


def stroke_time(data, count):
    """End of the Nth completed stroke; a missing locator is never clamped."""
    if count < 1 or count > len(data["strokes"]):
        return None
    stroke = data["strokes"][count - 1]
    return stroke["start"] + stroke["duration"]


def blocked_end(data):
    if not data.get("constructionBlocked"):
        return None
    reports = data.get("constructionReports", [])
    blocked = data["constructionBlocked"]
    index = blocked.get("reportIndex") if isinstance(blocked, dict) else None
    if not isinstance(index, int) or not 0 <= index < len(reports):
        index = next((i for i in range(len(reports) - 1, -1, -1) if reports[i].get("needsRevisit")), None)
    report = reports[index] if index is not None else {}
    return {"reportIndex": index, "guideIndex": report.get("guideIndex"), "layer": report.get("layer"),
            "status": report.get("status", "construction_blocked"), "strokeCount": len(data["strokes"]),
            "timeMs": data["totalDuration"], "kind": "blocked_end", "hasNextTransfer": False,
            "reason": blocked}


def unresolved_transition(data):
    reports = data.get("constructionReports", [])
    for index, report in enumerate(reports):
        following = reports[index + 1] if index + 1 < len(reports) else None
        same_movement = following and (following["guideIndex"], following["layer"]) == (report["guideIndex"], report["layer"])
        if not report.get("needsRevisit") or same_movement:
            continue
        count = report["endIndex"]
        if count < len(data["strokes"]):
            return {"reportIndex": index, "guideIndex": report["guideIndex"], "layer": report["layer"],
                    "status": report["status"], "strokeCount": count,
                    "timeMs": data["strokes"][count]["start"], "kind": "transfer", "hasNextTransfer": True}
    return blocked_end(data)


def interval(label, anchor, total, *, terminal=False, missing_reason="原版实际笔数不足，无法定位"):
    item = {"label": label, "anchorMs": anchor}
    if anchor is None or total is None or anchor > total or total <= 0 or (not terminal and anchor + 3000 > total):
        item.update(status="unavailable", reason=missing_reason if anchor is None else "两版共同原始时间不足，无法提供完整定位片段及后置3秒")
        return item
    item.update(status="available", startMs=max(0, anchor - 3000), endMs=min(total, anchor + 3000),
                trailingContextTruncated=anchor + 3000 > total, terminal=terminal)
    return item


def compare(before_path, after_path, output):
    before_path, before, inputs, before_results = read_manifest(before_path)
    after_path, after, after_inputs, after_results = read_manifest(after_path)
    if inputs != after_inputs or before["parameters"] != after["parameters"]:
        raise ValueError("Comparison requires identical ordered input paths, SHA256 values and parameters")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    cases, evidence = [], []
    for number, key in enumerate(inputs, 1):
        case = {"label": f"样例 {number}", "relativePath": key[0], "sha256": key[1], "clips": [], "runs": {}}
        record = {"relativePath": key[0], "sha256": key[1], "runs": {}}
        for label, manifest_path, result in (("before", before_path, before_results[key]), ("after", after_path, after_results[key])):
            data = load_case(manifest_path, result)
            run = {"status": result["status"], "seconds": result.get("seconds"), "frames": []}
            if data is None:
                run["error"] = result.get("error", "No painting data")
                case["runs"][label] = run
                record["runs"][label] = run.copy()
                continue
            folder = output / f"sample-{number:03d}" / label
            layers = data.get("layers", [])
            blocked = data.get("constructionBlocked") or result.get("blocked")
            partial = result["status"] == "incomplete" or bool(blocked) or any(layer.get("complete") is False for layer in layers)
            run.update(version=data.get("version", "unknown"), strokeCount=len(data["strokes"]),
                       incomplete=partial, blocked=blocked, stages=result.get("stages", len(layers)),
                       completedStages=result.get("completedStages", sum(layer.get("complete", True) is True for layer in layers)),
                       unresolvedTransition=unresolved_transition(data), blockedEnd=blocked_end(data))
            for count in LOCATORS:
                time_ms = stroke_time(data, count)
                frame = {"label": f"第 {count} 笔完成", "strokeCount": count, "timeMs": time_ms}
                if time_ms is None:
                    frame["status"] = "unavailable: fewer strokes"
                else:
                    target = folder / f"stroke-{count}.svg"
                    export_svg(data, target, time_ms)
                    frame.update(status="exported", href=target.relative_to(output).as_posix())
                run["frames"].append(frame)
            transition = run["unresolvedTransition"]
            events = [transition] if transition else []
            if run["blockedEnd"] and (not transition or transition["kind"] != "blocked_end"):
                events.append(run["blockedEnd"])
            for event in events:
                terminal = event["kind"] == "blocked_end"
                for offset, suffix in (((0, "blocked-final"),) if terminal else ((0, "before"), (1, "after"))):
                    count = event["strokeCount"] + offset
                    time_ms = event["timeMs"] if terminal else stroke_time(data, count) if count else 0
                    target = folder / ("blocked-final.svg" if terminal else f"transition-{suffix}.svg")
                    export_svg(data, target, time_ms)
                    frame_label = "预算停止末帧（无后续转移）" if terminal else "首次未完成转移" + ("前" if offset == 0 else "后")
                    run["frames"].append({"label": frame_label,
                                          "strokeCount": count, "timeMs": time_ms, "status": "exported",
                                          "href": target.relative_to(output).as_posix()})
            record["runs"][label] = run.copy()
            # The comparison renderer needs geometry and original timing only.
            run["data"] = {name: data[name] for name in ("width", "height", "strokes", "totalDuration", "sourceDataURL")}
            case["runs"][label] = run
        first, second = case["runs"].get("before", {}), case["runs"].get("after", {})
        total = min(first["data"]["totalDuration"], second["data"]["totalDuration"]) if "data" in first and "data" in second else None
        for count in LOCATORS:
            anchor = stroke_time(first["data"], count) if "data" in first else None
            case["clips"].append(interval(f"原版第 {count} 笔附近 · 同原始时间", anchor, total))
        for label, title in (("before", "原版"), ("after", "修改版")):
            transition = case["runs"][label].get("unresolvedTransition")
            events = [transition] if transition else []
            stop = case["runs"][label].get("blockedEnd")
            if stop and (not transition or transition["kind"] != "blocked_end"):
                events.append(stop)
            for event in events:
                terminal = event["kind"] == "blocked_end"
                event_title = title + ("预算停止前（无下一转移）" if terminal else "首次未完成转移")
                case["clips"].append(interval(f"{event_title} · 同原始时间", event["timeMs"], total, terminal=terminal))
        record["clips"] = case["clips"]
        cases.append(case)
        evidence.append(record)
    version = str(after.get("version", "unknown"))
    suffix = version if re.fullmatch(r"[\w.-]+", version) else "unknown"
    target = output / f"comparison-v{suffix}.html"
    payload = json.dumps(cases, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    target.write_text(HTML.replace("__VERSION__", html.escape(version)).replace("__CASES__", payload), encoding="utf-8")
    (output / "comparison-evidence.json").write_text(json.dumps({"parameters": before["parameters"], "cases": evidence}, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


HTML = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>人物早期过程对照 v__VERSION__</title><style>
:root{color-scheme:light dark;font:16px/1.55 system-ui,sans-serif}*{box-sizing:border-box}body{max-width:1200px;margin:24px auto;padding:0 16px;background:Canvas;color:CanvasText}h1{font-size:1.5rem}p{max-width:85ch}.controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;min-width:0;max-width:100%;margin:16px 0}.controls label{display:flex;flex:1 1 300px;gap:6px;align-items:center;min-width:0;max-width:100%}button,select,input{font:inherit;max-width:100%}.controls select{flex:1 1 0;width:0;min-width:0;max-width:100%}.views{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));grid-template-rows:auto auto 520px auto;column-gap:16px;row-gap:8px}.views>section{display:grid;grid-row:1/span 4;grid-template-rows:subgrid;min-width:0}.views h2,.views p{margin:0}.canvas{background:#fff;height:100%;min-height:0;position:relative;border:1px solid #999}.canvas svg,.canvas img{width:100%;height:100%;object-fit:contain;position:absolute;inset:0}.canvas img[hidden]{display:none}.meta{font-size:.9rem;overflow-wrap:anywhere}#seek{width:100%}#frames{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}a{color:LinkText}.warning{border-left:4px solid #bd6b1c;padding:8px;font-weight:650;overflow-wrap:anywhere}#unavailable{font-size:.9rem}@media(max-width:680px){.views{grid-template-rows:auto auto 340px auto;column-gap:6px}body{padding:0 8px}.meta{font-size:.75rem}}
</style><h1>人物早期过程对照 · v__VERSION__</h1>
<p>相同样例、SHA256、参数与原始时间。片段按画谱的真实时长连续播放，固定 1×，保留抬笔停顿。左右均为真实 SVG 路径，当前笔只增长到笔尖。可下载的 SVG 是静帧，完整动画在本页播放。</p>
<p>笔数仅作定位；请观察底色主面是否统一、笔宽与方向是否合适、关键转折是否成立。readableProxy 只测剖面对应，不能证明人能看懂。图库、画谱与本报告仅留本地。</p>
<div class="controls"><label>样例 <select id="case"></select></label><label>片段 <select id="clip"></select></label><button id="play" disabled>播放 · 原始 1×</button><button id="restart" disabled>回到片段起点</button><button id="reference" disabled>原图切换</button></div>
<input id="seek" type="range" min="0" max="1" step="1" value="0" aria-label="原始时间定位" disabled><output id="clock"></output>
<ul id="unavailable" aria-label="未提供的定位片段"></ul>
<div class="views"><section><h2>原版</h2><p id="before-status" role="status"></p><div id="before" class="canvas"></div><p id="before-meta" class="meta"></p></section><section><h2>修改版</h2><p id="after-status" role="status"></p><div id="after" class="canvas"></div><p id="after-meta" class="meta"></p></section></div>
<div id="frames"></div><script id="cases" type="application/json">__CASES__</script><script>
'use strict';
const cases=JSON.parse(document.getElementById('cases').textContent),$=id=>document.getElementById(id),NS='http://www.w3.org/2000/svg';
let current,clip,views={},time=0,playing=false,request=0,last=0,reference=false;
function path(d,s){const p=document.createElementNS(NS,'path');p.setAttribute('d',d);p.setAttribute('fill',s.color);p.setAttribute('opacity',s.opacity??1);return p;}
function prefix(s,progress){if(progress<=0)return '';if(progress>=1)return s.d;const pts=s.points,l=s.left,r=s.right;let lengths=s.lengths;
 if(!lengths||lengths.length!==pts.length||!lengths.every(Number.isFinite)||!lengths.every((v,i)=>!i||v>=lengths[i-1])||lengths.at(-1)<=lengths[0]){lengths=[0];for(let i=1;i<pts.length;i++)lengths.push(lengths.at(-1)+Math.hypot(pts[i][0]-pts[i-1][0],pts[i][1]-pts[i-1][1]));}else lengths=lengths.map(v=>v-lengths[0]);
 if(lengths.at(-1)===0)lengths=pts.map((_,i)=>i);const distance=progress*lengths.at(-1);let cut=1;while(cut<lengths.length-1&&lengths[cut]<distance)cut++;const f=(distance-lengths[cut-1])/(lengths[cut]-lengths[cut-1]||1),at=e=>e[cut-1].map((v,j)=>v+(e[cut][j]-v)*f),vertices=[...l.slice(0,cut),at(l),at(r),...r.slice(0,cut).reverse()];return 'M'+vertices.map(p=>p.join(',')).join(' L')+' Z';}
function createView(label,run){const box=$(label);box.replaceChildren();if(!run.data){box.textContent=run.error||run.status;return null;}const data=run.data,svg=document.createElementNS(NS,'svg');svg.setAttribute('viewBox',`0 0 ${data.width} ${data.height}`);const group=document.createElementNS(NS,'g'),active=document.createElementNS(NS,'g');svg.append(group,active);const image=document.createElement('img');image.src=data.sourceDataURL;image.alt='原始参考图';image.hidden=!reference;box.append(svg,image);return{data,group,active,image,count:0};}
function renderView(view,t){if(!view)return;const strokes=view.data.strokes;let count=0;while(count<strokes.length&&strokes[count].start+strokes[count].duration<=t)count++;if(count<view.count){view.group.replaceChildren();view.count=0;}for(let i=view.count;i<count;i++)view.group.append(path(strokes[i].d,strokes[i]));view.count=count;view.active.replaceChildren();const s=strokes[count];if(s&&t>s.start&&t<s.start+s.duration)view.active.append(path(prefix(s,(t-s.start)/s.duration),s));}
function render(t){time=t;for(const view of Object.values(views))renderView(view,t);$('seek').value=t;$('clock').textContent=`原始 ${(t/1000).toFixed(3)} 秒 · 片段 ${(clip.startMs/1000).toFixed(3)}–${(clip.endMs/1000).toFixed(3)} 秒 · 1× · 已完成：原版 ${views.before?.count??0} 笔 / 修改版 ${views.after?.count??0} 笔`+(clip.terminal?' · 阻塞末尾，无下一转移':'');}
function pause(){playing=false;cancelAnimationFrame(request);request=0;$('play').textContent='播放 · 原始 1×';}
function tick(now){if(!playing)return;const next=Math.min(clip.endMs,time+now-last);last=now;render(next);if(next>=clip.endMs){pause();return;}request=requestAnimationFrame(tick);}
function chooseClip(){pause();clip=current.clips[Number($('clip').value)];const available=clip?.status==='available';for(const id of ['play','restart','seek'])$(id).disabled=!available;$('reference').disabled=!Object.values(views).some(Boolean);if(available){$('seek').min=clip.startMs;$('seek').max=clip.endMs;render(clip.startMs);}else{for(const view of Object.values(views))if(view)renderView(view,view.data.totalDuration);$('clock').textContent='没有可比较的相同原始时间片段；各版现有末帧与失败原因保留如下。';}}
function runStatus(run){if(!run.incomplete)return !run.data?`失败：${run.error??run.status}`:'已生成（不代表自然度已验收）';const stopped=run.blocked&&typeof run.blocked==='object'?run.blocked:run.blockedEnd??run.unresolvedTransition??{},stages=['整体底形','大面明暗','体积冷暖','焦点细节'],stage=stages[stopped.layer]??'当前阶段',guide=Number.isInteger(stopped.guideIndex)?`、第 ${stopped.guideIndex+1} 条开放走势`:'';const reason=stopped.reason==='repair_budget_exhausted_before_coarse_ready'?'定向补画额度用尽，粗层仍未达到继续条件。':'粗层仍未满足继续条件，生成已停止。';return `未完成 · 在${stage}${guide}停止。${reason}已完成 ${run.completedStages} / 已启动 ${run.stages} 个阶段。${run.blockedEnd?'末尾没有下一转移。':''}`;}
function chooseCase(){pause();current=cases[Number($('case').value)];views={};$('frames').replaceChildren();for(const label of ['before','after']){const run=current.runs[label];views[label]=createView(label,run);$(label+'-meta').textContent=`${current.relativePath} · SHA256 ${current.sha256} · v${run.version??'?'} · ${run.strokeCount??0} 笔 · ${run.seconds??'?'} 秒生成`;const status=$(label+'-status');status.className=run.incomplete||!run.data?'warning':'';status.textContent=runStatus(run);const links=document.createElement('section');const title=document.createElement('h3');title.textContent=(label==='before'?'原版':'修改版')+' SVG静帧';links.append(title);for(const frame of run.frames){const row=document.createElement('p');if(frame.href){const link=document.createElement('a');link.href=frame.href;link.textContent=frame.label+` · ${(frame.timeMs/1000).toFixed(3)}s`;row.append(link);}else row.textContent=frame.label+'：实际笔数不足';links.append(row);}$('frames').append(links);}$('clip').replaceChildren(...current.clips.map((item,i)=>{const option=new Option(item.label+(item.status==='unavailable'?' · 不可用':''),i);option.disabled=item.status!=='available';return option;}));$('unavailable').replaceChildren(...current.clips.filter(item=>item.status==='unavailable').map(item=>{const li=document.createElement('li');li.textContent=item.label+'：未提供，'+item.reason;return li;}));const firstAvailable=current.clips.findIndex(item=>item.status==='available');$('clip').selectedIndex=firstAvailable;chooseClip();}
$('case').append(...cases.map((item,i)=>new Option(item.label+' · '+item.relativePath,i)));$('case').addEventListener('change',chooseCase);$('clip').addEventListener('change',chooseClip);$('play').addEventListener('click',()=>{if(playing){pause();return;}if(time>=clip.endMs)render(clip.startMs);playing=true;last=performance.now();$('play').textContent='暂停';request=requestAnimationFrame(tick);});$('restart').addEventListener('click',()=>{pause();render(clip.startMs);});$('seek').addEventListener('input',()=>{pause();render(Number($('seek').value));});$('reference').addEventListener('click',()=>{reference=!reference;for(const view of Object.values(views))if(view)view.image.hidden=!reference;});chooseCase();
window.comparisonAPI={cases,getState:()=>({time,playing,clip,reference,counts:Object.fromEntries(Object.entries(views).map(([k,v])=>[k,v?.count??0]))}),pause,render};
</script></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(compare(args.before, args.after, args.output))
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
