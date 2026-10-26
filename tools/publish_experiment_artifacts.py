"""Publish frozen portrait-coarse replays, without running the painting engine.

All local inputs are explicit. Source files come from a fixed Git commit, never
from untracked work. Only the selected players, comparison frames and source
archives enter the output; diagnostic logs and original image files do not.
"""
from __future__ import annotations

import argparse
import ast
import base64
import copy
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from pack_v7 import envelope, package_player
from package_review_artifacts import _PayloadScripts, _decode_payload, validate_html_artifact, write_index
from verify_artifact_manifest import verify_manifest

PRIVATE_PATH = re.compile(r"(?i)(?:(?<![a-z0-9])[a-z]:[\\/]|/(?:home|Users)/)")
EXPERIMENT = "portrait-coarse-v8.1"
ZIP_NAME = "source-portrait-coarse-v8.1.zip"
COMPARISON_NAME = "comparison-vexperimental-v9-source-checked-64.html"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def git_bytes(repo, *arguments):
    return subprocess.run(["git", "-C", str(repo), *arguments], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def frozen_source(repo, revision):
    """Read the actual module dependency closure at a committed revision."""
    commit = git_bytes(repo, "rev-parse", "--verify", revision + "^{commit}").decode().strip()
    names = set(git_bytes(repo, "ls-tree", "-r", "--name-only", commit).decode().splitlines())
    selected = {name for name in names if name.startswith("experiments/portrait_coarse/")
                and (name.endswith(".py") or name.endswith("README.md"))}
    selected.add("requirements.txt")
    pending = list(selected)
    contents = {}
    while pending:
        name = pending.pop()
        if name in contents:
            continue
        if name not in names:
            raise ValueError("Missing committed source dependency: " + name)
        raw = git_bytes(repo, "show", commit + ":" + name)
        contents[name] = raw
        if not name.endswith(".py"):
            continue
        # Absolute repository-local imports; relative imports are already in
        # the complete experiment package. External libraries remain requirements.
        for node in ast.walk(ast.parse(raw, filename=name)):
            modules = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                       else [node.module] if isinstance(node, ast.ImportFrom) and not node.level else [])
            for module in modules:
                if not module:
                    continue
                parts = module.split(".")
                for candidate in ("/".join(parts) + ".py", "/".join(parts) + "/__init__.py"):
                    if candidate in names and candidate not in contents:
                        pending.append(candidate)
                for index in range(1, len(parts)):
                    init = "/".join(parts[:index]) + "/__init__.py"
                    if init in names and init not in contents:
                        pending.append(init)
    if len([name for name in contents if name.startswith("experiments/portrait_coarse/")]) < 10:
        raise ValueError("The complete committed portrait-coarse package is required")
    return commit, contents


def source_archive(repo, revision):
    commit, contents = frozen_source(repo, revision)
    contents["SOURCE-README.md"] = ("# portrait-coarse-v8.1 完整实验源码\n\n"
        "此归档保留真实模块目录，不是默认 v7.2 自动生成器的拼接单文件。\n"
        "固定提交：`" + commit + "`。源码可离线查看；执行仍需要 Python 3.12 与 requirements.txt 中的库。\n\n"
        "安装依赖后，在解压根目录执行：\n\n"
        "```powershell\npython -m experiments.portrait_coarse --source inputs/image.png "
        "--guides inputs/guides.json --provenance inputs/provenance.json --output output --through 2\n```\n\n"
        "原图文件、显式 guide、provenance 及其绑定的原始候选文件需自行提供；未包含在归档中。"
        "公开 HTML 只包含已授权参考预览。输入校验与 SHA 绑定不放宽，不能从预览推造输入。\n"
        "详细输入契约见 experiments/portrait_coarse/README.md。未完成走势会保存部分画谱并返回 1；"
        "源码完整不等于本次底形已完成，也不代表人物效果或绘画自然度验收通过。\n").encode()
    manifest = dict(engineVersion="7.2", experimentVersion=EXPERIMENT, sourceCommit=commit,
                    engineVersionMeaning="painting data schema; generating engine is experimentVersion",
                    explicitInputsBundled=False, files={name: digest(raw) for name, raw in sorted(contents.items())})
    contents["source-manifest.json"] = dump(manifest).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, raw in sorted(contents.items()):
            item = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            archive.writestr(item, raw)
    return buffer.getvalue(), manifest


def public_copy(value, changes=None, location=""):
    """Redact private metadata strings, preserving every stroke byte-value."""
    changes = [] if changes is None else changes
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            result[key] = copy.deepcopy(item) if key == "strokes" else public_copy(item, changes, location + "/" + key)
        return result
    if isinstance(value, list):
        return [public_copy(item, changes, location + "/" + str(i)) for i, item in enumerate(value)]
    if isinstance(value, str) and PRIVATE_PATH.search(value):
        relative = "inputs/" + re.split(r"[\\/]", value)[-1]
        changes.append(dict(field=location, publicValue=relative))
        return relative
    return value


def replace_once(text, original, replacement):
    if text.count(original) != 1:
        raise ValueError("Expected exactly one template anchor: " + original[:75])
    return text.replace(original, replacement, 1)


def render_player(template, data, archive):
    """Keep renderer behavior; extend source download to actual ZIP bytes."""
    template = replace_once(template, '<title>逐笔 v7.2</title>', '<title>v8.1 实验 · 64 笔完整真实回放</title>')
    template = replace_once(template, '<h1>逐笔<small>v7.2</small></h1>',
                            '<h1>64 笔真实回放<small>portrait-coarse-v8.1 · 底形未完成</small></h1>')
    note = ('<p id="engine-version-note" class="hint">实际生成引擎 portrait-coarse-v8.1；画谱格式与播放器 v7.2。'
            '完整保留 64 笔 / 原始 59.971 秒；后上发在 42.855 秒转入 guide 11，尾部 guide 16 严格停止。'
            '未进入下一阶段，人物效果与自然度未验收。完整代码 ZIP 内嵌本页，可离线下载；原图/guide/provenance 自备。'
            '发布副本仅脱敏本地路径，真实笔迹与时间不变。'
            '<a href="index.html">返回入口</a> · <a download href="' + ZIP_NAME + '">直接下载对应实验源码 ZIP</a></p>')
    template = replace_once(template, '</header>', '</header>' + note)
    template = replace_once(template, '下载完整代码</button>', '下载完整实验代码 ZIP</button>')
    start = template.index('  async function generatorSource()')
    end = template.index("  on($('play'),'click'", start)
    template = template[:start] + """  async function generatorSource(){const value=JSON.parse($('generator-source').textContent);if(value.encoding!=='zip-base64')throw new Error('Expected complete ZIP source');return{source:Uint8Array.from(atob(value.payload),c=>c.charCodeAt(0)),filename:value.filename,mime:'application/zip'};}
  on($('download-generator'),'click',async()=>{const button=$('download-generator');button.disabled=true;try{const result=await generatorSource(),url=URL.createObjectURL(new Blob([result.source],{type:result.mime})),a=document.createElement('a');a.href=url;a.download=result.filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){button.textContent=error.message;}finally{button.disabled=false;}});
""" + template[end:]
    template = replace_once(template, 'await ready;updatePlay();', "await ready;setPlaybackMode('real');updatePlay();")
    source = json.dumps(dict(encoding="zip-base64", payload=base64.b64encode(archive).decode(),
                             filename=ZIP_NAME, mimeType="application/zip"))
    return package_player(template, envelope(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()), source)


def source_zip_validation(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip() is not None:
            raise ValueError("Invalid or duplicate source ZIP entries")
        manifest = json.loads(archive.read("source-manifest.json"))
        if set(names) != set(manifest["files"]) | {"source-manifest.json"}:
            raise ValueError("Source ZIP manifest must cover every file")
        for name, expected in manifest["files"].items():
            if name.startswith("/") or ".." in PurePosixPath(name).parts or "\\" in name:
                raise ValueError("Unsafe source ZIP entry")
            if digest(archive.read(name)) != expected:
                raise ValueError("Source ZIP hash mismatch: " + name)
    return manifest


def publish(repo, replay_data, delivery, output, source_ref, expected_data_sha256):
    repo, replay_data, delivery, output = map(lambda p: Path(p).resolve(), (repo, replay_data, delivery, output))
    if output.exists():
        raise ValueError("Output must not exist; published directories are never overwritten")
    original = replay_data.read_bytes()
    if digest(original) != expected_data_sha256:
        raise ValueError("Frozen replay SHA256 mismatch")
    raw = json.loads(original)
    if (len(raw["strokes"]) != 64 or raw["totalDuration"] != 59971 or raw["strokes"][41]["start"] != 42855
            or raw.get("constructionBlocked", {}).get("guideIndex") != 16
            or len(raw["layers"]) != 1 or raw["layers"][0].get("complete") is not False
            or raw.get("experiment", {}).get("version") != EXPERIMENT):
        raise ValueError("Expected frozen 64-stroke, incomplete portrait-coarse-v8.1 score")
    old = json.loads((delivery / "old41-vs-new64/before/qa/painting-data.json").read_bytes())
    v6_path = delivery / "true-v6-vs-new64/before/qa/painting-data.json"
    v6 = json.loads(v6_path.read_bytes())
    if len(old["strokes"]) != 41 or old["strokes"][:32] != raw["strokes"][:32] or len(v6["strokes"]) != 676:
        raise ValueError("Frozen historical comparisons do not match")
    archive, source = source_archive(repo, source_ref)
    source_zip_validation(archive)
    changes = []
    data = public_copy(raw, changes)
    output.mkdir(parents=True)
    (output / ZIP_NAME).write_bytes(archive)
    write(output / "painting-data-v8.1-64.json", dump(data))
    template = git_bytes(repo, "show", source["sourceCommit"] + ":player_v7.html").decode("utf-8")
    write(output / "replay-v8.1-64.html", render_player(template, data, archive))
    from export_svg import render_svg
    write(output / "final-v8.1-64.svg", render_svg(data))
    for tag in ("old41-vs-new64", "true-v6-vs-new64"):
        folder = delivery / tag / "comparison"
        text = (folder / COMPARISON_NAME).read_text(encoding="utf-8")
        parser = _PayloadScripts()
        parser.feed(text)
        cases = public_copy(json.loads(parser.payloads["cases"][0]))
        case = cases[0]
        if case["runs"]["after"]["data"]["strokes"] != raw["strokes"]:
            raise ValueError("Comparison does not contain the frozen 64 strokes")
        expected_before = old if tag == "old41-vs-new64" else v6
        if case["runs"]["before"]["data"]["strokes"] != expected_before["strokes"]:
            raise ValueError("Historical comparison strokes differ")
        text = text.replace(parser.payloads["cases"][0], json.dumps(cases, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c'), 1)
        text = text.replace('图库、画谱与本报告仅留本地。', '本页为已授权公开的选定对照与内嵌参考预览；原始图库不上传。')
        text = replace_once(text, '<div class="controls">',
            '<p><a href="../index.html">返回入口</a> · <a download href="../' + ZIP_NAME + '">新64笔对应实验完整源码 ZIP</a>' +
            (' · <a download href="../source-v6.0.zip">v6 原始归档源码 ZIP</a>' if tag.startswith("true-v6") else '') +
            '</p><div class="controls">')
        write(output / tag / "index.html", text)
        for file in sorted(folder.rglob("*.svg")):
            target = output / tag / file.relative_to(folder)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(file.read_bytes())
    v6_archive = git_bytes(repo, "show", source["sourceCommit"] + ":artifacts/v7.2/source-v6.0.zip")
    source_zip_validation(v6_archive)
    (output / "source-v6.0.zip").write_bytes(v6_archive)
    entries = [dict(title="64 笔完整真实回放", href="replay-v8.1-64.html", status="当前实验 · 底形未完成",
                    description="原始 59.971 秒；播放/暂停、原速/对数、逐笔前缀与原图切换。HTML 内嵌完整实验代码 ZIP，可独立下载离线使用。"),
               dict(title="旧 41 笔与新 64 笔", href="old41-vs-new64/index.html", status="同原始时间对照",
                    description="前32笔逐值一致；旧过程43.645秒停止，新过程继续至64笔。旧41笔是历史实验，不能标作当前源码重新生成。"),
               dict(title="真正 v6 与新 64 笔", href="true-v6-vs-new64/index.html", status="历史效果基线 · 676 笔",
                    description="v6两阶段完整395.429秒；新过程停止后保持末帧，不按完成比例对齐。v6人工走势仅作对照。")]
    write_index(output / "index.html", entries, "人物绘画实验 · v8.1 · 完整 HTML",
                "portrait-coarse-v8.1 显式输入实验；画谱格式/播放器 v7.2。保留64笔真实过程，后上发42.855秒转入guide 11，最后在guide 16停止。底形未完成，未进入下一阶段；人物效果与自然度未验收。")
    portal = (output / "index.html").read_text(encoding="utf-8")
    portal = portal.replace('</main>', '<p><a download href="replay-v8.1-64.html">下载完整回放 HTML</a> · '
        '<a download href="' + ZIP_NAME + '">下载对应完整实验源码 ZIP</a> · '
        '<a href="final-v8.1-64.svg">64笔末帧 SVG（静帧）</a> · '
        '<a href="publication.json">版本与哈希</a></p></main>')
    write(output / "index.html", portal)
    metadata = dict(version="v8.1", generatingExperiment=EXPERIMENT, dataSchemaVersion=raw["version"], playerVersion="7.2",
        sourceCommit=source["sourceCommit"], frozenInput=dict(filename=replay_data.name, sha256=digest(original)),
        sourceImageSHA256=raw["experiment"]["explicitInputs"]["sourceSHA256"],
        strokes=64, rawTimelineMs=59971, headTransferMs=42855, stoppedGuide=16, complete=False,
        semanticAcceptance=False, stage1Entered=False, strokesUnchanged=True, first32EqualToOld41=True,
        referencePreviewIncluded=True, originalImageFileIncluded=False, explicitInputsIncluded=False,
        sourceArchive=dict(filename=ZIP_NAME, sha256=digest(archive), files=source["files"]), metadataRedactions=changes,
        historicalComparisons=dict(old41=dict(strokes=41, rawTimelineMs=old["totalDuration"], sha256=digest((delivery / "old41-vs-new64/before/qa/painting-data.json").read_bytes())),
            trueV6=dict(strokes=676, rawTimelineMs=v6["totalDuration"], sha256=digest(v6_path.read_bytes()))))
    write(output / "publication.json", dump(metadata))
    write(output / "README.md", "# v8.1 完整 HTML 交付\n\n打开 index.html。完整回放 HTML 内嵌画谱、授权参考预览与对应实验源码 ZIP，单独下载也可离线播放和取出源码。\n\n"
        "64 笔 / 59.971 秒；42.855 秒转入 guide 11，最后在 guide 16 严格停止，底形未完成。没有后续阶段或人物效果/自然度通过声明。\n\n"
        "源码保留 experiments/portrait_coarse 真实模块及根依赖；固定提交与逐文件 SHA 见 publication.json 和 ZIP 内 source-manifest.json。"
        "原图、显式 guide/provenance 与绑定候选需自备。旧41笔为历史过程，v6为独立历史归档，不冒认当前算法结果。\n\n"
        "发布副本只脱敏本地路径；全部 strokes 字段保持原值，原始画谱不修改。artifact-manifest.json 对发布文件逐字节记 SHA256 和尺寸；.gitattributes 已为 artifacts/** 禁用文本换行转换。\n")
    records = {p.relative_to(output).as_posix(): dict(bytes=p.stat().st_size, sha256=digest(p.read_bytes()))
               for p in sorted(output.rglob("*")) if p.is_file()}
    write(output / "artifact-manifest.json", dump(dict(version="v8.1", files=records)))
    return validate_publication(output)


def validate_publication(output):
    """Validate self-contained HTML, embedded ZIP, privacy and exact bytes."""
    from html.parser import HTMLParser
    from urllib.parse import unquote, urlsplit

    output = Path(output).resolve()
    result = verify_manifest(output / "artifact-manifest.json")
    links = []
    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key in ("href", "src") and value:
                    links.append((self.path, value))

    for file in sorted(output.rglob("*")):
        if not file.is_file():
            continue
        if file.suffix in (".html", ".json", ".md", ".svg"):
            text = file.read_text(encoding="utf-8")
            if PRIVATE_PATH.search(text):
                raise ValueError("Private absolute path in publication: " + file.name)
        if file.suffix == ".html":
            validate_html_artifact(file)
            parser = Links(); parser.path = file; parser.feed(text)
            payloads = _PayloadScripts(); payloads.feed(text)
            if "cases" in payloads.payloads:
                def json_links(value):
                    if isinstance(value, dict):
                        for key, item in value.items():
                            if key == "href": links.append((file, item))
                            else: json_links(item)
                    elif isinstance(value, list):
                        for item in value: json_links(item)
                json_links(json.loads(payloads.payloads["cases"][0]))
    for file, href in links:
        parsed = urlsplit(href)
        if parsed.scheme == "data" or not parsed.path:
            continue
        if parsed.scheme or parsed.netloc or parsed.query:
            raise ValueError("Publication requires closed local links: " + href[:100])
        target = (file.parent / unquote(parsed.path)).resolve()
        if not target.is_relative_to(output) or not target.is_file():
            raise ValueError("Broken or escaping publication link: " + href)
    parser = _PayloadScripts(); parser.feed((output / "replay-v8.1-64.html").read_text(encoding="utf-8"))
    embedded = json.loads(parser.payloads["generator-source"][0])
    archive = (output / ZIP_NAME).read_bytes()
    if embedded.get("encoding") != "zip-base64" or base64.b64decode(embedded["payload"], validate=True) != archive:
        raise ValueError("Player must embed the exact complete source ZIP")
    source = source_zip_validation(archive)
    if source.get("experimentVersion") != EXPERIMENT:
        raise ValueError("Wrong generating experiment source")
    data = _decode_payload(json.loads(parser.payloads["painting-data"][0]))
    if data != json.loads((output / "painting-data-v8.1-64.json").read_bytes()):
        raise ValueError("HTML and public JSON data differ")
    return dict(**result, htmlVerified=len(list(output.rglob("*.html"))), localLinksVerified=len(links),
                sourceFiles=len(source["files"]), embeddedSourceSHA256=digest(archive))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--replay-data", type=Path, required=True)
    parser.add_argument("--delivery", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--expected-data-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.repo, args.replay_data, args.delivery, args.output,
                             args.source_ref, args.expected_data_sha256), ensure_ascii=False))


if __name__ == "__main__":
    main()
