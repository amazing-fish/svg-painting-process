"""Deterministic local gallery baseline. Never uploads images or output."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import time
import platform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from build_portrait import generate, VERSION
from render_player import render_player

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def input_record(path, gallery):
    return {"relativePath": path.relative_to(gallery).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gallery", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "generated" / f"gallery-v{VERSION}")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--max-edge", type=int, default=640)
    parser.add_argument("--through", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--manifest", type=Path, help="Reuse the exact relative paths and hashes from an earlier baseline")
    parser.add_argument("--list-only", action="store_true", help="Write selection manifest without generating")
    args = parser.parse_args()
    gallery, output = args.gallery.resolve(), args.output.resolve()
    if not gallery.is_dir():
        parser.error("Gallery is not a readable directory: " + str(gallery))
    if args.limit < 1 or not 128 <= args.max_edge <= 2048:
        parser.error("--limit must be positive; --max-edge must be 128..2048")
    if output == gallery or output in gallery.parents or gallery in output.parents:
        parser.error("--output must be separate from the gallery tree")
    previous = {}
    if args.manifest:
        previous = json.loads(args.manifest.read_text(encoding="utf-8"))
        records = previous["inputs"]
        selected = []
        for record in records:
            path = (gallery / record["relativePath"]).resolve()
            if gallery not in path.parents or not path.is_file():
                parser.error("Manifest path is outside the gallery or missing")
            if input_record(path, gallery)["sha256"] != record["sha256"]:
                parser.error("Input changed: " + record["relativePath"])
            selected.append(path)
    else:
        selected = sorted((p for p in gallery.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONS
                           and gallery in p.resolve().parents), key=lambda p: p.relative_to(gallery).as_posix().casefold())[:args.limit]
        records = [input_record(p, gallery) for p in selected]
    if not selected:
        parser.error("No supported images found")
    output.mkdir(parents=True, exist_ok=True)
    report = {"version": VERSION, "selection": previous.get("selection", "manifest") if args.manifest else "sorted-first-N (no content classification)",
              "parameters": {"maxEdge": args.max_edge, "through": args.through},
              "inputs": records, "results": [], "naturalnessAccepted": False}
    for key in ("selectionBy", "coverageLimitations"):
        if key in previous:
            report[key] = previous[key]
    report["sourceHashes"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                              for name in ("auto_guides.py", "build_portrait.py", "construction_episodes.py",
                                           "global_plan.py", "player_v7.html", "playback_clock.js")}
    report["runtime"] = {"python": platform.python_version()}
    manifest = output / f"manifest-v{VERSION}.json"
    manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.list_only:
        print(manifest)
        return
    from export_svg import export_svg
    for index, (path, record) in enumerate(zip(selected, records)):
        case = output / f"sample-{index + 1:03d}-{record['sha256'][:8]}"
        started = time.perf_counter()
        result = {"relativePath": record["relativePath"], "sha256": record["sha256"]}
        try:
            generate(path, case, args.max_edge, args.through)
            data_path = case / "qa" / "painting-data.json"
            data = json.loads(data_path.read_text(encoding="utf-8"))
            render_player(data_path, case / f"portrait-painting-v{VERSION}.html")
            export_svg(data, case / f"painting-v{VERSION}.svg")
            result.update(status="incomplete" if data.get("constructionBlocked") else "generated",
                          blocked=data.get("constructionBlocked"),
                          completedStages=sum(layer.get("complete", True) for layer in data["layers"]),
                          strokes=len(data["strokes"]), guides=data["automaticGuides"]["guideCount"],
                          stages=len(data["layers"]), output=case.relative_to(output).as_posix())
        except Exception as error:
            result.update(status="error", error=type(error).__name__ + ": " + str(error))
        result["seconds"] = round(time.perf_counter() - started, 3)
        report["results"].append(result)
        manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), flush=True)
    if any(item["status"] != "generated" for item in report["results"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
