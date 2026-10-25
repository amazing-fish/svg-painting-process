"""Deterministic local gallery baseline. Never uploads images or output."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from build_portrait import generate
from render_player import render_player

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def input_record(path, gallery):
    return {"relativePath": path.relative_to(gallery).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gallery", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "generated" / "gallery-v7.0")
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
    if args.manifest:
        records = json.loads(args.manifest.read_text(encoding="utf-8"))["inputs"]
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
    report = {"version": "7.0", "selection": "manifest" if args.manifest else "sorted-first-N (no content classification)",
              "parameters": {"maxEdge": args.max_edge, "through": args.through},
              "inputs": records, "results": [], "naturalnessAccepted": False}
    manifest = output / "manifest-v7.0.json"
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
            render_player(data_path, case / "portrait-painting-v7.0.html")
            export_svg(data, case / "painting-v7.0.svg")
            result.update(status="generated", strokes=len(data["strokes"]), guides=data["automaticGuides"]["guideCount"],
                          stages=len(data["layers"]), output=case.relative_to(output).as_posix())
        except Exception as error:
            result.update(status="error", error=type(error).__name__ + ": " + str(error))
        result["seconds"] = round(time.perf_counter() - started, 3)
        report["results"].append(result)
        manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), flush=True)
    if any(item["status"] == "error" for item in report["results"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
