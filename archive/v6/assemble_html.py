"""Package the complete new generator and pure SVG player in one offline HTML."""
from pathlib import Path
import base64
import gzip
import hashlib
import io
import json
import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT.parent/'recovered_v5/2f22ad27-e747-4500-b975-345352f21ba7.png'


def pack(raw,filename=None):
    value=dict(encoding='gzip-base64',payload=base64.b64encode(gzip.compress(raw,compresslevel=6,mtime=0)).decode())
    if filename:value['filename']=filename
    return json.dumps(value,separators=(',',':'))


def preview(path,width=480):
    im=Image.open(path).convert('RGB');im.thumbnail((width,2000),Image.Resampling.LANCZOS)
    out=io.BytesIO();im.save(out,format='JPEG',quality=94)
    return 'data:image/jpeg;base64,'+base64.b64encode(out.getvalue()).decode()


def complete_source(template):
    planner=(ROOT/'global_plan.py').read_text(encoding='utf-8').split('def _self_test():')[0]
    episodes=(ROOT/'construction_episodes.py').read_text(encoding='utf-8')
    painter=(ROOT/'build_scene.py').read_text(encoding='utf-8')
    painter=painter.replace('from global_plan import attention_route, validate_timeline\n','')
    painter=painter.replace('from construction_episodes import paint_construction_episode\n','')
    painter=painter.rsplit('if __name__ == "__main__":',1)[0]
    old=(ROOT.parent/'atelier_v5/build_shared_canvas.py').read_text(encoding='utf-8')
    footer=old[old.index('# The complete player is stored here, without any network dependency.'):]
    start=footer.index('PLAYER_TEMPLATE_GZIP = ');end=footer.index('\n',start)
    encoded=repr(base64.b64encode(gzip.compress(template.encode(),mtime=0)).decode())
    footer=footer[:start]+'PLAYER_TEMPLATE_GZIP = '+encoded+footer[end:]
    footer=footer.replace("(QA/'painting-data.json').read_text()","(QA/'painting-data.json').read_text(encoding='utf-8')")
    footer=footer.replace('output.write_text(result)',"output.write_text(result,encoding='utf-8')")
    source='''# Complete open-gesture construction generator and offline SVG player.
# Windows PowerShell:
#   py -m pip install numpy scipy opencv-python-headless Pillow
#   py .\\build_shared_canvas.py --source .\\reference.png --through 1 --reset
#   py .\\build_shared_canvas.py --source .\\reference.png --through 4
# Generated HTML contains its own complete source-download button.
# Sparse open guides are manually placed for this reference composition.
# They specify drawing movements and never divide source-image ownership.
'''+planner+'\n\n'+episodes+'\n\n'+painter+'\n\n'+footer
    compile(source,'build_shared_canvas.py','exec')
    return source


def main():
    data=json.loads((ROOT/'qa/painting-data.json').read_text(encoding='utf-8'))
    original=np.asarray(Image.open(SOURCE).convert('RGB'),float)
    for i,layer in enumerate(data['layers']):
        frame=ROOT/f'output/rendered-layer-{i+1}.png'
        actual=np.asarray(Image.open(frame).convert('RGB'),float)
        layer['mae']=round(float(np.abs(actual-original).mean()),4)
        layer['previewDataURL']=preview(frame)
    data['metrics']['finalMAE']=data['layers'][-1]['mae']
    data['metrics']['maeRasterizer']='actual SVG paths rendered by librsvg'
    template=(ROOT/'player_template.html').read_text(encoding='utf-8')
    template=template.replace('<title>逐笔 · 全图共享画布</title>','<title>逐笔 · 先让形体成立</title>')
    template=template.replace('<h1>逐笔<span>全图共同发展</span></h1>','<h1>逐笔<span>先让形体成立</span></h1>')
    source=complete_source(template)
    (ROOT/'qa/build_shared_canvas.py').write_text(source,encoding='utf-8')
    html=template.replace('__PAINTING_DATA__',pack(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()))
    html=html.replace('__GENERATOR_SOURCE__',pack(source.encode(),'build_shared_canvas.py'))
    assert '__PAINTING_DATA__' not in html and '__GENERATOR_SOURCE__' not in html
    assert '<script src=' not in html and '<mask' not in html and '<clipPath' not in html
    file=ROOT/'output/complex-stroke-painting.html';file.write_text(html,encoding='utf-8')
    report=dict(file=str(file),bytes=file.stat().st_size,paths=len(data['strokes']),minutes=data['totalDuration']/60000,
                layers=[dict(name=l['name'],paths=l['endIndex'],actualSVGMAE=l['mae']) for l in data['layers']],
                generatorSHA256=hashlib.sha256(source.encode()).hexdigest())
    (ROOT/'qa/package-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
