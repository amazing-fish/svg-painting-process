"""Build the versioned offline player and complete standalone generator."""
from pathlib import Path
import base64
import gzip
import json

ROOT = Path(__file__).resolve().parent
DELIVERY = ROOT / 'delivery'
GENERATOR_VERSION = '7.1'


def envelope(raw, filename=None):
    value = dict(encoding='gzip-base64', payload=base64.b64encode(gzip.compress(raw, compresslevel=6, mtime=0)).decode())
    if filename:
        value['filename'] = filename
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def complete_source():
    pieces = []
    for name in ('global_plan.py', 'auto_guides.py', 'construction_episodes.py', 'build_portrait.py'):
        source = (ROOT / name).read_text(encoding='utf-8')
        source = source.replace('from __future__ import annotations\n', '')
        source = source.replace('from auto_guides import extract_guides\n', '')
        source = source.replace('from global_plan import attention_route, validate_timeline\n', '')
        source = source.replace('from construction_episodes import paint_construction_episode\n', '')
        if name == 'global_plan.py' and 'def _self_test():' in source:
            source = source.split('def _self_test():')[0]
        if name == 'build_portrait.py':
            source = source[:source.index('def main():')]
        pieces.append(source)
    template = (ROOT / 'player_v7.html').read_text(encoding='utf-8')
    encoded = base64.b64encode(gzip.compress(template.encode(), mtime=0)).decode()
    header = '''# portrait-painting-v7.1.py -- complete offline automatic portrait painter
# Windows PowerShell:
#   py -m pip install numpy scipy opencv-python-headless Pillow
#   py .\\portrait-painting-v7.1.py --source .\\person.jpg --output .\\person-painting-v7.1.html
# All guide coordinates come from the selected image. No per-image tuning is required.
# Classical image structure is used, not a trained pose/anatomy model.
from __future__ import annotations
import gzip
'''
    footer = '''
def _envelope_v7(raw, filename=None):
    result=dict(encoding='gzip-base64',payload=base64.b64encode(gzip.compress(raw,compresslevel=6,mtime=0)).decode())
    if filename:result['filename']=filename
    return json.dumps(result,ensure_ascii=False,separators=(',',':'))

def main():
    import tempfile
    parser=argparse.ArgumentParser(description='Automatic portrait painting v7.1 and offline player')
    parser.add_argument('--source',type=Path,required=True,help='Any local RGB/RGBA portrait or figure image')
    parser.add_argument('--output',type=Path,default=Path('portrait-painting-v7.1.html'))
    parser.add_argument('--max-edge',type=int,default=768,help='Analysis/rendering resolution, default 768px longest side')
    args=parser.parse_args()
    if not 128<=args.max_edge<=2048:parser.error('--max-edge must be between 128 and 2048')
    with tempfile.TemporaryDirectory(prefix='portrait-v7-') as temporary:
        painter=generate(args.source,Path(temporary),args.max_edge,4)
        data=json.loads((Path(temporary)/'qa/painting-data.json').read_text(encoding='utf-8'))
    template=gzip.decompress(base64.b64decode(PLAYER_TEMPLATE_V7)).decode()
    full_source=Path(__file__).read_text(encoding='utf-8')
    output=template.replace('__PAINTING_DATA__',_envelope_v7(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()))
    output=output.replace('__GENERATOR_SOURCE__',_envelope_v7(full_source.encode(),'portrait-painting-v7.1.py'))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(output,encoding='utf-8')
    print('Saved '+str(args.output.resolve()),flush=True)
    if getattr(painter,'construction_blocked',None):
        print(json.dumps(dict(status='incomplete',blocked=painter.construction_blocked)),flush=True)
        raise SystemExit(1)

if __name__=='__main__':main()
'''
    source = header + '\n\n'.join(pieces) + '\n\nPLAYER_TEMPLATE_V7 = ' + repr(encoded) + '\n' + footer
    compile(source, 'portrait-painting-v7.1.py', 'exec')
    return source


def main():
    DELIVERY.mkdir(exist_ok=True)
    source = complete_source()
    (DELIVERY / 'portrait-painting-v7.1.py').write_text(source, encoding='utf-8')
    items = []
    for name, label in [('illustration', '原插画'), ('grace_hopper', '人物照 · Hopper'), ('astronaut', '人物照 · 宇航员')]:
        data = json.loads((ROOT / 'runs' / name / 'qa/painting-data.json').read_text(encoding='utf-8'))
        if data.get('version') != GENERATOR_VERSION:
            raise ValueError(name + ': regenerate with v' + GENERATOR_VERSION + ' before packaging')
        if len(data['layers']) != 4:
            raise RuntimeError(name + ' is not complete')
        # Compact each stroke's genuine geometry; do not quantize or replace it.
        keep = ('d','points','left','right','color','opacity','start','duration','pauseAfter','layer')
        data['strokes'] = [{k: s[k] for k in keep if k in s} for s in data['strokes']]
        for layer in data['layers']:
            layer.pop('previewDataURL', None)
        for key in ('constructionReports','routeDiagnostics','stageEvents'):
            data.pop(key, None)
        items.append(dict(id=name, label=label, data=json.loads(envelope(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()))))
    bundle = dict(version='7.1', paintings=items)
    template = (ROOT / 'player_v7.html').read_text(encoding='utf-8')
    html = template.replace('__PAINTING_DATA__', json.dumps(bundle,ensure_ascii=False,separators=(',',':')))
    html = html.replace('__GENERATOR_SOURCE__', envelope(source.encode(), 'portrait-painting-v7.1.py'))
    if '__PAINTING_DATA__' in html or '__GENERATOR_SOURCE__' in html:
        raise RuntimeError('Unfilled template')
    target = DELIVERY / 'portrait-painting-v7.1.html'
    target.write_text(html, encoding='utf-8')
    print(json.dumps(dict(file=str(target),bytes=target.stat().st_size,samples=[p['id'] for p in items]),ensure_ascii=False))


if __name__ == '__main__':
    main()
