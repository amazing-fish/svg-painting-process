"""Reproduce the archived, image-specific v6 in an isolated local directory.

The archived files are copied byte-for-byte, never edited or imported together
with the current generator. The runner only chooses the source/output paths and
records provenance; all painting functions are the original v6 implementation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("build_scene.py", "global_plan.py", "construction_episodes.py")

RUNNER = r'''from pathlib import Path
import hashlib,json,sys,time
import build_scene as v6
source=Path(sys.argv[1]).resolve(); output=Path(sys.argv[2]).resolve(); through=int(sys.argv[3])
v6.OUT=output/'output';v6.QA=output/'qa'
v6.OUT.mkdir(parents=True,exist_ok=True);v6.QA.mkdir(parents=True,exist_ok=True)
started=time.perf_counter();painter=v6.SharedCanvasPainter(source)
# This is the visit order in the archived construction_sequence, not a new plan.
order=[2,4,3,13,6,5,12,1,0,10,11,17,18,19,14,15,7,9,16,8]
metadata=dict(version='6.0',method='archived image-specific twenty manual open movements',
 sourceSize=[painter.w,painter.h],guideCount=len(painter.guides),guideOrder=order,
 sourceSHA256=hashlib.sha256(source.read_bytes()).hexdigest(),manualCoordinateCount=20,
 sourceSpecificRules=True,paintClipping=False,
 limitation='Manual geometry and order only apply to the original reference composition.')
guides=dict(gestures=[(p.tolist(),r.tolist()) for p,r in painter.guides],metadata=metadata)
(v6.QA/'manual-guides.json').write_text(json.dumps(guides,ensure_ascii=False,indent=2),encoding='utf-8')
times=[]
for layer in range(through):
 phase=time.perf_counter();painter.paint_phase(layer);times.append(time.perf_counter()-phase)
painter.export()
data=json.loads((v6.QA/'painting-data.json').read_text(encoding='utf-8'))
(v6.QA/'painting-data-original-v6.json').write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
# Annotation only. No path, colour, opacity, timing or stage result is changed.
data.update(version='6.0',manualGuides=metadata,constructionBlocked=None)
for layer in data['layers']:layer['complete']=True
(v6.QA/'painting-data.json').write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
result=dict(version='6.0',sourceSHA256=metadata['sourceSHA256'],sourceSize=metadata['sourceSize'],
 parameters=dict(resolution='native; no resize',through=through),seconds=round(time.perf_counter()-started,3),
 stageSeconds=[round(t,3) for t in times],strokes=len(data['strokes']),
 layers=[dict(stage=l['stage'],strokes=l['endIndex'],rawEndMs=l['endTime'],mae=l['mae']) for l in data['layers']],
 sourceSpecificManualBaseline=True,naturalnessAccepted=False)
(output/'baseline-v6.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False),flush=True)
'''


def reproduce(source: Path, output: Path, through: int = 2) -> Path:
    source, output = source.resolve(), output.resolve()
    if not source.is_file():
        raise ValueError("Source image is missing")
    if through not in (1, 2, 3, 4):
        raise ValueError("through must be 1..4")
    src = output / "src"
    src.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in MODULES:
        original, copied = ROOT / "archive" / "v6" / name, src / name
        if copied.exists() and copied.read_bytes() != original.read_bytes():
            raise ValueError("Refusing to overwrite a changed v6 reproduction module: " + name)
        shutil.copyfile(original, copied)
        hashes[name] = hashlib.sha256(original.read_bytes()).hexdigest()
    runner = src / "run_baseline.py"
    runner.write_text(RUNNER, encoding="utf-8")
    command = [sys.executable, str(runner), str(source), str(output), str(through)]
    with (output / "generation.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=src, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError(f"Archived v6 failed with exit {result.returncode}; see {output / 'generation.log'}")
    target = output / "baseline-v6.json"
    record = json.loads(target.read_text(encoding="utf-8"))
    record["archiveSourceSHA256"] = hashes
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "generated" / "v6-baseline-v7.2")
    parser.add_argument("--through", type=int, choices=range(1, 5), default=2)
    args = parser.parse_args()
    print(reproduce(args.source, args.output, args.through))


if __name__ == "__main__":
    main()
