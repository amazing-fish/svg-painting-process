"""Package reviewable historical players and an explicit relative-link portal.

The supplied engine label and painting data are preserved. The current player
is only a renderer; source downloads point to the supplied matching ZIP.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
import sys
from urllib.parse import unquote, urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pack_v7 import GENERATOR_VERSION as PLAYER_VERSION, envelope, package_player
from export_svg import render_svg, partial_ribbon_d


class _PayloadScripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.payloads = {}
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "script":
            return
        attributes = dict(attrs)
        element = attributes.get("id")
        if element in ("painting-data", "generator-source") or attributes.get("type") == "application/json":
            self.current = [element or "application/json", []]

    def handle_data(self, data):
        if self.current is not None:
            self.current[1].append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self.current is not None:
            element, pieces = self.current
            self.payloads.setdefault(element, []).append("".join(pieces).strip())
            self.current = None


def _decode_payload(value):
    if isinstance(value, dict) and value.get("encoding") == "gzip-base64":
        try:
            return json.loads(gzip.decompress(base64.b64decode(value["payload"], validate=True)))
        except (KeyError, ValueError, OSError, TypeError) as error:
            raise ValueError("Painting payload cannot be decoded") from error
    return value


def _check_painting(value):
    value = _decode_payload(value)
    if not isinstance(value, dict):
        raise ValueError("Painting payload must be an object")
    if "paintings" in value:
        if not isinstance(value["paintings"], list) or not value["paintings"]:
            raise ValueError("Painting bundle must contain examples")
        for item in value["paintings"]:
            if not isinstance(item, dict) or "data" not in item:
                raise ValueError("Painting bundle entry is missing data")
            _check_painting(item["data"])
    elif not isinstance(value.get("strokes"), list) or not value["strokes"]:
        raise ValueError("Painting data must contain playable strokes")
    else:
        # Reuse the authoritative export contract for dimensions, complete
        # timeline, colours and opacity, then exercise every growing prefix.
        render_svg(value)
        for stroke in value["strokes"]:
            partial_ribbon_d(stroke, .5)
    return value


def validate_html_artifact(path):
    """Reject source templates and unfilled top-level inert JSON containers."""
    path = Path(path)
    parser = _PayloadScripts()
    parser.feed(path.read_text(encoding="utf-8"))
    for element, values in parser.payloads.items():
        if len(values) != 1:
            raise ValueError(f"{path.name}: duplicate {element} payload containers")
        body = values[0]
        if re.fullmatch(r"__[A-Z_]+__", body):
            raise ValueError(f"{path.name}: unfilled {element} payload; this is a source template")
        if element != "generator-source":
            try:
                value = json.loads(body)
            except ValueError as error:
                raise ValueError(f"{path.name}: invalid {element} JSON payload") from error
            if element == "painting-data":
                _check_painting(value)
    return path


def _relative_file(base, href):
    if not isinstance(href, str) or not href.strip() or "\\" in href:
        raise ValueError("Artifact href must be a nonempty relative URL using forward slashes")
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc or parsed.query or not parsed.path or parsed.path.startswith("/"):
        raise ValueError("Artifact href must be a relative file URL")
    decoded = unquote(parsed.path)
    if decoded.startswith("/") or "\\" in decoded or re.match(r"^[A-Za-z]:", decoded):
        raise ValueError("Artifact href must remain relative after URL decoding")
    target = (Path(base) / decoded).resolve()
    if not target.is_file():
        raise ValueError("Artifact link does not exist: " + href)
    return target


def _validate_source_archive(archive, data):
    """Bind the downloadable source files to the recorded generating engine."""
    expected_version = data.get("version")
    if not isinstance(expected_version, str) or not expected_version.strip():
        raise ValueError("Painting data must record its generating engine version for source matching")
    try:
        with zipfile.ZipFile(archive) as source:
            if source.testzip() is not None:
                raise ValueError("Matching source archive failed its integrity check")
            names = source.namelist()
            if names.count("source-manifest.json") != 1:
                raise ValueError("Source ZIP must contain exactly one source-manifest.json")
            try:
                manifest = json.loads(source.read("source-manifest.json").decode("utf-8"))
            except (UnicodeError, ValueError) as error:
                raise ValueError("source-manifest.json must be valid UTF-8 JSON") from error
            if not isinstance(manifest, dict) or manifest.get("engineVersion") != expected_version:
                raise ValueError("Source manifest engineVersion does not match painting data.version")
            files = manifest.get("files")
            if not isinstance(files, dict) or not files:
                raise ValueError("Source manifest files must be a nonempty filename-to-SHA256 map")
            for name, digest in files.items():
                if (not isinstance(name, str) or not name or "\\" in name or
                        PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or
                        PurePosixPath(name).as_posix() != name or re.match(r"^[A-Za-z]:", name) or
                        name == "source-manifest.json" or name.endswith("/")):
                    raise ValueError("Source manifest filenames must be canonical relative files, excluding itself")
                if names.count(name) != 1 or source.getinfo(name).is_dir():
                    raise ValueError("Source manifest file is missing or duplicated: " + name)
                if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                    raise ValueError("Source manifest needs a SHA256 value for " + name)
                if hashlib.sha256(source.read(name)).hexdigest() != digest.lower():
                    raise ValueError("Source manifest SHA256 mismatch: " + name)
            actual_files = {item.filename for item in source.infolist() if not item.is_dir()}
            if actual_files != set(files) | {"source-manifest.json"}:
                raise ValueError("Source manifest must list every archive file except itself")
    except (zipfile.BadZipFile, KeyError) as error:
        raise ValueError("Source archive cannot be verified") from error
    # A single archive cannot truthfully label a bundle of different engines.
    pending = [data]
    while pending:
        nested = _decode_payload(pending.pop())
        if nested.get("version") != expected_version:
            raise ValueError("Painting bundle engines do not match the source archive")
        pending.extend(item["data"] for item in nested.get("paintings", []))
    return manifest


def package_review_player(data_path, output, engine_label, source_zip):
    """Render any existing engine's data without claiming current-engine origin."""
    if not isinstance(engine_label, str) or not engine_label.strip():
        raise ValueError("An explicit generating engine label is required")
    output = Path(output).resolve()
    archive = _relative_file(output.parent, source_zip)
    if archive.suffix.lower() != ".zip" or not zipfile.is_zipfile(archive):
        raise ValueError("Matching source archive must be a ZIP file")
    data = json.loads(Path(data_path).read_text(encoding="utf-8"))
    _check_painting(data)
    _validate_source_archive(archive, data)
    template = (ROOT / "player_v7.html").read_text(encoding="utf-8")
    # Keep the hidden legacy button for the player's listener lifecycle. Its
    # payload is an honest text notice, never the current generator mislabeled
    # as historical Python; the only visible source action is the archive link.
    button = re.compile(r'(<button\b[^>]*\sid="download-generator")([^>]*>)(.*?)(</button>)', re.S)
    if len(button.findall(template)) != 1:
        raise ValueError("Player template must expose exactly one source-download button")
    archive_link = ('<a id="engine-source-archive" class="quiet" download href="' +
                    html.escape(source_zip, quote=True) + '">下载对应引擎源码 ZIP</a>')
    template = button.sub(lambda match: match.group(1) + ' hidden disabled tabindex="-1" aria-hidden="true"' +
                          match.group(2) + match.group(3) + match.group(4) + archive_link, template, count=1)
    label = html.escape(engine_label)
    recorded = html.escape(str(data.get("version") or "历史画谱未记录版本"))
    note = (f'<p id="engine-version-note" class="hint" style="margin:0 0 12px">生成引擎：{label}；'
            f'画谱版本：{recorded}；播放器：v{PLAYER_VERSION}。源码下载为对应引擎归档 ZIP，'
            '播放器版本不改写画谱来源。</p>')
    if template.count('</header>') != 1:
        raise ValueError("Player template must expose exactly one header")
    template = template.replace('</header>', '</header>' + note, 1)
    template = re.sub(r'<title>.*?</title>', lambda _: f'<title>{label} · 逐笔播放器 v{PLAYER_VERSION}</title>',
                      template, count=1, flags=re.S)
    template = template.replace('<meta name="painting-version"',
                                '<meta name="painting-engine" content="' + html.escape(engine_label, quote=True) +
                                '"><meta name="painting-version"', 1)
    notice = (f"Generating engine: {engine_label}\nPlayer: {PLAYER_VERSION}\n"
              f"The matching complete source is the linked ZIP: {source_zip}\n"
              "This notice is not a Python generator.\n")
    result = package_player(template, envelope(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()),
                            envelope(notice.encode(), "source-archive-info.txt"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result, encoding="utf-8")
    validate_html_artifact(output)
    return output


def write_index(output, entries, title="人物绘画过程 · v6 基线交付", summary="状态以各产物的说明和实际回放为准。"):
    """Build a portal only from explicit, present and validated HTML entries."""
    output = Path(output).resolve()
    if not isinstance(entries, list) or not entries:
        raise ValueError("The artifact index requires explicit entries")
    if not isinstance(title, str) or not title.strip() or not isinstance(summary, str):
        raise ValueError("The artifact index title and summary must be text")
    cards = []
    for entry in entries:
        if not isinstance(entry, dict) or any(not isinstance(entry.get(key), str) or not entry[key].strip()
                                              for key in ("title", "href", "status")):
            raise ValueError("Every artifact entry needs title, href and an explicit status")
        if not isinstance(entry.get("description", ""), str):
            raise ValueError("Artifact description must be text")
        target = _relative_file(output.parent, entry["href"])
        if target.suffix.lower() not in (".html", ".htm"):
            raise ValueError("Artifact index entries must link to HTML files")
        validate_html_artifact(target)
        cards.append('<article><p class="status">' + html.escape(entry["status"]) + '</p><h2><a href="' +
                     html.escape(entry["href"], quote=True) + '">' + html.escape(entry["title"]) +
                     '</a></h2><p>' + html.escape(entry.get("description", "")) + '</p></article>')
    output.parent.mkdir(parents=True, exist_ok=True)
    document = INDEX.replace("__TITLE__", html.escape(title)).replace("__SUMMARY__", html.escape(summary))
    output.write_text(document.replace("__ENTRIES__", "".join(cards)), encoding="utf-8")
    return output


INDEX = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title><style>
:root{color-scheme:light dark;font:16px/1.6 system-ui,"Microsoft YaHei",sans-serif;background:Canvas;color:CanvasText}*{box-sizing:border-box}body{max-width:1000px;margin:48px auto;padding:0 24px}h1{font-size:1.8rem;line-height:1.3}h2{font-size:1.18rem;margin:.3em 0}p{margin:.5em 0;overflow-wrap:anywhere}.intro{max-width:75ch;color:GrayText}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-top:28px}article{border:1px solid GrayText;border-radius:8px;padding:20px}a{color:LinkText;text-underline-offset:.18em}.status{font-size:.8rem;font-weight:650;color:GrayText}footer{margin-top:28px;color:GrayText;font-size:.85rem}@media(max-width:620px){body{margin:24px auto;padding:0 16px}.grid{grid-template-columns:1fr}h1{font-size:1.5rem}}
</style></head><body><main><h1>__TITLE__</h1><p class="intro">__SUMMARY__</p><div class="grid">__ENTRIES__</div></main><footer>回放页保留真实矢量笔迹与时间轴。生成引擎、播放器和源码归档版本分别标明；能播放不等于绘画效果已通过验收。</footer></body></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--data", type=Path, help="Existing painting-data.json, without rewriting its engine version")
    inputs.add_argument("--entries", type=Path, help="Explicit portal entries JSON list, or {entries,title,summary}")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--engine-label")
    parser.add_argument("--source-zip", help="Existing matching source ZIP, linked relative to the output HTML")
    parser.add_argument("--title", default="人物绘画过程 · v6 基线交付")
    args = parser.parse_args()
    try:
        if args.data:
            if not args.engine_label or not args.source_zip:
                parser.error("--data requires --engine-label and --source-zip")
            target = package_review_player(args.data, args.output, args.engine_label, args.source_zip)
        else:
            specification = json.loads(args.entries.read_text(encoding="utf-8"))
            if isinstance(specification, dict):
                target = write_index(args.output, specification["entries"], specification.get("title", args.title),
                                     specification.get("summary", "状态以各产物的说明和实际回放为准。"))
            else:
                target = write_index(args.output, specification, args.title)
        print(target)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
