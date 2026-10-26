"""Build only the reviewed artifact deliveries into a GitHub Pages site.

No repository-root publishing, image discovery, or regeneration happens here.
Run from any directory: python tools/build_pages_site.py --output _site
"""
import argparse
import json
import posixpath
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from publish_experiment_artifacts import validate_publication
from verify_artifact_manifest import verify_manifest


DELIVERIES = ('v8.1', 'v7.2')
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_SITE_BYTES = 250 * 1024 * 1024
MANIFEST = 'artifact-manifest.json'
ROOT_HTML = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SVG 绘画过程 · 作品入口</title><style>
:root{color-scheme:light dark;font:17px/1.65 system-ui,"Microsoft YaHei",sans-serif;
background:Canvas;color:CanvasText}*{box-sizing:border-box}
body{max-width:760px;margin:12vh auto;padding:0 24px}h1{font-size:2rem;line-height:1.25}
p{color:GrayText}nav{display:grid;gap:16px;margin:32px 0}
a{display:block;padding:22px 24px;border:1px solid GrayText;border-radius:12px;
color:LinkText;text-decoration:none}a:hover,a:focus-visible{outline:2px solid LinkText}
a strong,a span{display:block}a strong{font-size:1.2rem}a span{margin-top:6px;color:GrayText}
.latest{border:2px solid LinkText}@media(max-width:520px){body{margin:48px auto;padding:0 18px}}
</style></head><body><main><h1>SVG 绘画过程</h1>
<p>选择最新完整 HTML 回放与对照，或查看历史交付。能播放不等于绘画效果已通过验收。</p>
<nav aria-label="交付版本">
<a class="latest" href="artifacts/v8.1/index.html"><strong>最新 v8.1 · 完整回放与对照</strong>
<span>64 笔实验过程、历史对照与对应完整源码</span></a>
<a href="artifacts/v7.2/index.html"><strong>历史 v7.2 · 交付归档</strong>
<span>查看前版回放、基线对照与对应源码</span></a>
</nav></main></body></html>'''


def safe_name(name):
    """Manifest paths must be canonical relative URL-safe filesystem names."""
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or '%' in name or '?' in name or '#' in name
            or any(ord(char) < 32 for char in name)):
        raise ValueError('Unsafe artifact path: ' + str(name))
    path = PurePosixPath(name)
    if (not path.parts or path.is_absolute() or path.as_posix() != name
            or any(part in ('.', '..') or part.startswith('.') for part in path.parts)):
        raise ValueError('Unsafe artifact path: ' + name)
    return path


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.styles = []
        self.in_style = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'style':
            self.in_style = True
        if tag == 'base':
            raise ValueError('HTML base elements are not allowed in the relative Pages site')
        for key in ('href', 'src', 'xlink:href', 'poster', 'action', 'data'):
            if key in attrs and attrs[key]:
                self.links.append(attrs[key])
        for key in ('style', 'fill', 'stroke', 'filter', 'clip-path', 'mask',
                    'marker-start', 'marker-mid', 'marker-end'):
            if attrs.get(key):
                self.styles.append(attrs[key])
        # Deliveries use embedded previews; unsupported URL lists must not evade validation.
        if attrs.get('srcset'):
            raise ValueError('Use individually validated src URLs instead of srcset')
        if tag == 'meta' and attrs.get('http-equiv', '').lower() == 'refresh':
            match = re.fullmatch(r'\s*\d+(?:\.\d+)?\s*;\s*url\s*=\s*(.+?)\s*',
                                 attrs.get('content', ''), re.IGNORECASE)
            if not match:
                raise ValueError('Invalid HTML refresh URL')
            self.links.append(match.group(1).strip('\"\''))

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        if tag == 'style':
            self.in_style = False

    def handle_data(self, data):
        if self.in_style:
            self.styles.append(data)


def validate_link(source, value, names):
    value = value.strip()
    if '\\' in value or any(ord(char) < 32 for char in value):
        raise ValueError(f'Unsafe local URL in {source}: {value!r}')
    parsed = urlsplit(value)
    if parsed.scheme:
        if parsed.scheme.lower() in ('https', 'http') and parsed.netloc:
            return
        if parsed.scheme.lower() in ('data', 'blob', 'mailto'):
            return
        raise ValueError(f'Unsupported or local absolute URL in {source}: {value!r}')
    if parsed.netloc or parsed.path.startswith('/'):
        raise ValueError(f'Absolute URL is not project-relative in {source}: {value!r}')
    path = unquote(parsed.path)
    if '\\' in path or ':' in path or any(ord(char) < 32 for char in path):
        raise ValueError(f'Unsafe decoded URL in {source}: {value!r}')
    if not path:
        return  # Same-document anchors and runtime-populated download controls.
    target = posixpath.normpath(posixpath.join(posixpath.dirname(source), path))
    if target.startswith('../') or target == '..' or target.startswith('/'):
        raise ValueError(f'URL escapes published site in {source}: {value!r}')
    if path.endswith('/'):
        target += '/index.html'
    if target not in names:
        raise ValueError(f'Broken or unpublished local URL in {source}: {value!r}')


def collect_deliveries(repo, max_file_bytes, max_site_bytes):
    files = {}
    total = len(ROOT_HTML.encode('utf-8'))
    for version in DELIVERIES:
        folder = repo / 'artifacts' / version
        if folder.is_symlink() or (repo / 'artifacts').is_symlink():
            raise ValueError('Delivery directories cannot be symbolic links')
        manifest = folder / MANIFEST
        if not manifest.is_file():
            raise ValueError('Required delivery manifest is missing: ' + str(manifest))
        records = json.loads(manifest.read_text(encoding='utf-8')).get('files')
        if not isinstance(records, dict) or 'index.html' not in records:
            raise ValueError('Delivery needs a manifested index.html: ' + version)
        for name in records:
            safe_name(name)
        for path in folder.rglob('*'):
            if path.is_symlink():
                raise ValueError('Symbolic links are not allowed: ' + str(path))
            if path.is_file() and path.stat().st_nlink != 1:
                raise ValueError('Hard links are not allowed: ' + str(path))
        verify_manifest(manifest)
        for name in sorted([*records, MANIFEST]):
            source = folder / name
            size = source.stat().st_size
            if size > max_file_bytes:
                raise ValueError('Artifact exceeds file size limit: ' + str(source))
            total += size
            if total > max_site_bytes:
                raise ValueError('Published site exceeds total size limit')
            files[f'artifacts/{version}/{name}'] = source
    return files, total


def build_site(repo, output, *, max_file_bytes=MAX_FILE_BYTES, max_site_bytes=MAX_SITE_BYTES):
    repo = Path(repo).resolve()
    output = Path(output).absolute()
    # Never recursively delete a caller-selected path or mix old/unreviewed files into a deployment.
    if output.exists() or output.is_symlink():
        raise ValueError('Output must be a new directory: ' + str(output))
    if output == repo or output.is_relative_to(repo / 'artifacts'):
        raise ValueError('Output cannot replace the repository or delivery sources')
    files, total = collect_deliveries(repo, max_file_bytes, max_site_bytes)
    # A recomputed outer manifest cannot prove the player embeds the matching
    # source ZIP or the same painting data as its downloadable JSON.
    validate_publication(repo / 'artifacts' / 'v8.1')
    names = set(files) | {'index.html'}
    documents = {'index.html': ROOT_HTML}
    for name, source in files.items():
        if source.suffix.lower() in ('.html', '.svg'):
            documents[name] = source.read_text(encoding='utf-8')
    for name, document in documents.items():
        parser = Links()
        parser.feed(document)
        parser.close()
        for link in parser.links:
            validate_link(name, link, names)
        for link in re.findall(r'url\(\s*[\"\']?([^\s\"\')]+)[\"\']?\s*\)', '\n'.join(parser.styles),
                               re.IGNORECASE):
            validate_link(name, link, names)
    # Validation is complete before creating any output; errors never produce an empty success page.
    output.mkdir(parents=True)
    (output / 'index.html').write_text(ROOT_HTML, encoding='utf-8', newline='\n')
    for name, source in files.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for version in DELIVERIES:
        verify_manifest(output / 'artifacts' / version / MANIFEST)
    return dict(files=len(names), bytes=total, latest=DELIVERIES[0], deliveries=list(DELIVERIES))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(build_site(args.repo, args.output), ensure_ascii=False))
    except (OSError, ValueError, TypeError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
