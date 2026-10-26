"""Audit automatic open movements against the original twenty v6 movements.

This compares axes, width and visit order. Geometry scores do not establish
anatomy or natural drawing, and the manual paths are never used for generation.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from scipy.spatial.distance import cdist


def sample_curve(points, radii, count=64):
    points, radii = np.asarray(points, float), np.asarray(radii, float)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3 or radii.shape != (len(points),):
        raise ValueError("Each open movement needs Nx2 points and N radii")
    if not np.isfinite(points).all() or not np.isfinite(radii).all() or np.any(radii <= 0):
        raise ValueError("Open geometry must be finite with positive radii")
    distance = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    if distance[-1] <= 0:
        raise ValueError("Open movement must have positive length")
    t = np.linspace(0., distance[-1], count)
    q = np.column_stack([np.interp(t, distance, points[:, axis]) for axis in (0, 1)])
    rr = np.interp(t, distance, radii)
    tangent = np.gradient(q, axis=0)
    tangent /= np.linalg.norm(tangent, axis=1)[:, None] + 1.e-9
    return q, rr, tangent


def load_guides(path, source_size):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    size = np.asarray(data["metadata"]["sourceSize"], float)
    if size.shape != (2,) or not np.isfinite(size).all() or np.any(size <= 0):
        raise ValueError("Guide metadata needs positive sourceSize")
    scale = np.asarray(source_size, float) / size
    guides = [(np.asarray(p, float) * scale, np.asarray(r, float) * np.sqrt(scale.prod()))
              for p, r in data["gestures"]]
    if not guides:
        raise ValueError("Guide set is empty")
    for p, r in guides:
        sample_curve(p, r)
    order = data["metadata"].get("guideOrder", list(range(len(guides))))
    if sorted(order) != list(range(len(guides))):
        raise ValueError("guideOrder must be a permutation")
    return data["metadata"], guides, order


def pair_metrics(manual, automatic, diagonal):
    mp, mr, mt = sample_curve(*manual)
    ap, ar, at = sample_curve(*automatic)
    distances = cdist(mp, ap)
    near = distances.argmin(axis=1)
    md, ad = distances.min(axis=1), distances.min(axis=0)
    dot = np.sum(mt * at[near], axis=1)
    angle = np.degrees(np.arccos(np.clip(np.abs(dot), 0., 1.)))
    same_end = np.linalg.norm(mp[0]-ap[0]) + np.linalg.norm(mp[-1]-ap[-1])
    reversed_end = np.linalg.norm(mp[0]-ap[-1]) + np.linalg.norm(mp[-1]-ap[0])
    return dict(
        meanAxisDistancePx=round(float(md.mean()), 3),
        reverseMeanAxisDistancePx=round(float(ad.mean()), 3),
        symmetricAxisDistanceDiagonalFraction=round(float((md.mean()+ad.mean())*.5/diagonal), 6),
        manualCoverageWithinManualRadius=round(float(np.mean(md <= mr)), 4),
        medianUndirectedAngleDegrees=round(float(np.median(angle)), 2),
        meanDirectedTangentAgreement=round(float(dot.mean()), 4),
        traversal="same" if same_end <= reversed_end else "reversed",
        endpointDistancePx=round(float(min(same_end, reversed_end)*.5), 3),
        widthRatioAutomaticToManual=round(float(np.median(ar)/np.median(mr)), 4),
        manualMedianWidthPx=round(float(2*np.median(mr)), 3),
        automaticMedianWidthPx=round(float(2*np.median(ar)), 3),
    )


def compare(manual_path, automatic_path, source_path, automatic_source_sha256, output):
    source_path, output = Path(source_path).resolve(), Path(output).resolve()
    sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if automatic_source_sha256 != sha:
        raise ValueError("Automatic source SHA256 does not match the reference")
    source = ImageOps.exif_transpose(Image.open(source_path)).convert("RGB")
    width, height = source.size
    mm, manual, order = load_guides(manual_path, source.size)
    am, automatic, auto_order = load_guides(automatic_path, source.size)
    if mm.get("sourceSHA256") != sha:
        raise ValueError("Manual baseline SHA256 does not match the reference")
    diagonal = float(np.hypot(width, height))
    auto_ranks = {index: rank+1 for rank, index in enumerate(auto_order)}
    pairs = []
    for rank, index in enumerate(order, 1):
        measurements = [pair_metrics(manual[index], movement, diagonal) for movement in automatic]
        closest = min(range(len(measurements)), key=lambda i: measurements[i]["symmetricAxisDistanceDiagonalFraction"])
        pairs.append(dict(manualRank=rank, manualIndex=index, nearestAutomaticIndex=closest,
                          automaticRank=auto_ranks[closest], visitRankDifference=auto_ranks[closest]-rank,
                          **measurements[closest]))
    report = dict(sourceSHA256=sha, sourceSize=[width, height],
                  manualWorkingSize=mm["sourceSize"], automaticWorkingSize=am["sourceSize"],
                  sameWorkingResolution=mm["sourceSize"] == am["sourceSize"],
                  manualGuideCount=len(manual), automaticGuideCount=len(automatic), pairs=pairs,
                  distinctAutomaticMatches=len({p["nearestAutomaticIndex"] for p in pairs}),
                  metricsMeaning={
                      "matching": "Nearest symmetric axis distance; many-to-one matches are preserved, not hidden by forced assignment.",
                      "distance": "Distances are measured in original-source pixels after normalizing both coordinate systems.",
                      "coverage": "Fraction of 64 manual axis samples within that manual brush radius of its nearest automatic axis.",
                      "direction": "Undirected tangent angle measures orientation; signed agreement and endpoint traversal expose reversal.",
                      "order": "Actual construction visit ranks, not list positions of the manual GESTURES constant.",
                  },
                  acceptance="Geometry diagnostics only. No semantic recognition or naturalness pass is inferred.",
                  manualGuidesUsedByAutomaticGenerator=False)
    output.mkdir(parents=True, exist_ok=True)
    (output / "guide-comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    image = source.copy(); image.thumbnail((900, 1200))
    stream = io.BytesIO(); image.save(stream, format="JPEG", quality=92)
    reference = "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode()
    payload = dict(report=report, manualOrder=order, automaticOrder=auto_order,
                   manual=[dict(points=p.tolist(), radii=r.tolist()) for p, r in manual],
                   automatic=[dict(points=p.tolist(), radii=r.tolist()) for p, r in automatic], reference=reference)
    packed = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    target = output / "v6-open-guide-comparison-v7.2.html"
    target.write_text(HTML.replace("__DATA__", packed), encoding="utf-8")
    return target


HTML = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>v6 指定走势与自动走势 · v7.2</title>
<style>*{box-sizing:border-box}[hidden]{display:none!important}body{font:15px/1.6 system-ui,sans-serif;margin:24px auto;padding:0 16px;max-width:1300px;background:#f5f5f1;color:#25342e}h1{font-size:1.5rem}p{max-width:95ch}.controls{display:flex;flex-wrap:wrap;gap:16px;margin:18px 0}label{cursor:pointer}select,button{font:inherit}.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px;align-items:start}.paper{background:white;border:1px solid #aab4ac;position:sticky;top:10px}.paper svg{display:block;width:100%;max-height:80vh}table{border-collapse:collapse;width:100%;font-size:12px;background:white}td,th{padding:7px;border-bottom:1px solid #d9ded9;text-align:left}tbody tr{cursor:pointer}tbody tr.current{background:#dae7df}.table-wrap{overflow:auto;max-width:100%}.warning{overflow-wrap:anywhere;border-left:4px solid #ba6a25;padding:10px;background:#fff4e5}#description{overflow-wrap:anywhere}@media(max-width:760px){body{padding:0 8px}.grid{grid-template-columns:1fr}.paper{position:relative;top:0}.paper svg{height:65vh}td,th{padding:5px;font-size:11px}}</style>
<h1>v6 的 20 条指定走势，自动算法能否模拟？</h1><p>蓝色为 v6 指定的开放轴及真实访问顺序，橙色为自动提取的开放轴及顺序。箭头表示起止方向，线旁数字为访问次序。它们只用于分析对照；v6 的人工坐标没有输入自动生成器。</p>
<p class="warning" id="warning"></p><div class="controls"><label><input id="reference" type="checkbox">原参考图</label><label><input id="manual" type="checkbox" checked>v6 指定轴</label><label><input id="automatic" type="checkbox" checked>自动轴</label><button id="all">显示全部</button></div><p id="description"></p>
<div class="grid"><div class="paper"><svg id="canvas" xmlns="http://www.w3.org/2000/svg"><defs><marker id="m-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10Z" fill="#1355b6"/></marker><marker id="a-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10Z" fill="#c4660d"/></marker></defs><image id="source" x="0" y="0" opacity=".6" hidden/><g id="manual-paths"></g><g id="auto-paths"></g></svg></div><div><div class="table-wrap"><table><thead><tr><th>v6 顺序/轴</th><th>最近自动顺序</th><th>距离%对角线</th><th>覆盖%</th><th>方向差°</th><th>宽比</th><th>顺序差</th></tr></thead><tbody id="rows"></tbody></table></div><p>距离为两条轴的双向平均最近距离；覆盖为手工轴采样点落在自身笔半径内的比例。方向差忽略反向，反向情况单列提示。多个 v6 轴可能匹配同一自动轴，因此表格不把被合并或丢失的走势隐藏成唯一匹配。数值不能代替人物可读性验收。</p><p><a href="guide-comparison.json">下载逐条测量 JSON</a></p></div></div>
<script id="data" type="application/json">__DATA__</script><script>
'use strict';const data=JSON.parse(document.getElementById('data').textContent),$=id=>document.getElementById(id),NS='http://www.w3.org/2000/svg',report=data.report;let selected=null;
const canvas=$('canvas');canvas.setAttribute('viewBox',`0 0 ${report.sourceSize[0]} ${report.sourceSize[1]}`);const source=$('source');source.setAttribute('href',data.reference);source.setAttribute('width',report.sourceSize[0]);source.setAttribute('height',report.sourceSize[1]);
function add(guides,order,group,color,arrow){order.forEach((index,rank)=>{const g=document.createElementNS(NS,'g');g.dataset.index=index;const points=guides[index].points,path=document.createElementNS(NS,'path');path.setAttribute('d','M'+points.map(p=>p.join(',')).join(' L'));path.setAttribute('fill','none');path.setAttribute('stroke',color);path.setAttribute('stroke-width',4);path.setAttribute('marker-end',`url(#${arrow})`);const text=document.createElementNS(NS,'text');text.setAttribute('x',points[0][0]+5);text.setAttribute('y',points[0][1]-5);text.setAttribute('fill',color);text.setAttribute('font-size',22);text.setAttribute('font-weight','700');text.textContent=rank+1;g.append(path,text);group.append(g);});}
add(data.manual,data.manualOrder,$('manual-paths'),'#1355b6','m-arrow');add(data.automatic,data.automaticOrder,$('auto-paths'),'#c4660d','a-arrow');
function focus(pair){selected=pair;for(const [id,index] of [['manual-paths',pair?.manualIndex],['auto-paths',pair?.nearestAutomaticIndex]])for(const g of $(id).children)g.setAttribute('opacity',!pair||Number(g.dataset.index)===index?'1':'.12');for(const row of $('rows').children)row.classList.toggle('current',pair&&Number(row.dataset.index)===pair.manualIndex);$('description').textContent=pair?`v6 第 ${pair.manualRank} 条（轴索引 ${pair.manualIndex}）→ 自动第 ${pair.automaticRank} 条。方向 ${pair.traversal==='same'?'同向':'反向'}；轴距离 ${pair.meanAxisDistancePx.toFixed(2)}px，原手工中位笔宽 ${pair.manualMedianWidthPx.toFixed(2)}px，自动中位笔宽 ${pair.automaticMedianWidthPx.toFixed(2)}px。`:'点击任意一行，单独核对该条 v6 指定走势与它的最近自动走势。';}
for(const pair of report.pairs){const row=document.createElement('tr');row.dataset.index=pair.manualIndex;const values=[`${pair.manualRank} / ${pair.manualIndex}`,pair.automaticRank,(pair.symmetricAxisDistanceDiagonalFraction*100).toFixed(2),(pair.manualCoverageWithinManualRadius*100).toFixed(1),pair.medianUndirectedAngleDegrees,pair.widthRatioAutomaticToManual,pair.visitRankDifference];for(const value of values){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}row.addEventListener('click',()=>focus(pair));$('rows').append(row);}
$('warning').textContent=`参考图 SHA256 ${report.sourceSHA256}。v6 ${report.manualGuideCount} 条，自动 ${report.automaticGuideCount} 条；20 条 v6 轴最近匹配涉及 ${report.distinctAutomaticMatches} 条不同自动轴。`+(report.sameWorkingResolution?'两版工作分辨率相同。':'注意：工作分辨率不同，本页只归一坐标做诊断，不能冒充同分辨率效果比较。');
$('reference').addEventListener('change',e=>source.toggleAttribute('hidden',!e.target.checked));$('manual').addEventListener('change',e=>$('manual-paths').toggleAttribute('hidden',!e.target.checked));$('automatic').addEventListener('change',e=>$('auto-paths').toggleAttribute('hidden',!e.target.checked));$('all').addEventListener('click',()=>focus(null));focus(null);window.guideComparisonAPI={report,focus};
</script></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manual", type=Path, required=True)
    parser.add_argument("--automatic", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--automatic-source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(compare(args.manual, args.automatic, args.source, args.automatic_source_sha256, args.output))


if __name__ == "__main__":
    main()
