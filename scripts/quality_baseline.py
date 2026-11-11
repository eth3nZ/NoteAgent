"""Build fixed English review material and run offline fidelity checks. No API keys or network."""

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import nbformat
import pymupdf
from PIL import Image, ImageDraw

from app.core.cloud import CloudClient, FREE_MODEL, ModelConfig
from app.core.pipeline import Pipeline
from app.core.task_store import TaskStore


def create_pdf(path, cases):
    with pymupdf.open() as document:
        for case in cases:
            page = document.new_page(width=720, height=500)
            if case.get('image_only'):
                image = Image.new('RGB', (1440,1000), 'white')
                draw = ImageDraw.Draw(image)
                header = Image.new('RGB', (720,220), 'white')
                ImageDraw.Draw(header).multiline_text((30,20), case['source'], fill='black', spacing=8)
                image.paste(header.resize((1440,440)), (0,0))
                inputs, hidden, output = [(220,550),(220,800)], [(700,550),(700,800)], [(1180,675)]
                for left, right in [(a,b) for a in inputs for b in hidden]+[(a,b) for a in hidden for b in output]:
                    dx, dy = right[0]-left[0], right[1]-left[1]
                    length = math.hypot(dx,dy)
                    ux, uy = dx/length, dy/length
                    start, end = (left[0]+38*ux,left[1]+38*uy), (right[0]-38*ux,right[1]-38*uy)
                    draw.line((start,end), fill='black', width=4)
                    draw.polygon([end,(end[0]-20*ux-7*uy,end[1]-20*uy+7*ux),
                                      (end[0]-20*ux+7*uy,end[1]-20*uy-7*ux)], fill='black')
                for x,y in inputs+hidden+output:
                    draw.ellipse((x-35,y-35,x+35,y+35), fill='white', outline='black', width=4)
                import io
                buffer = io.BytesIO()
                image.save(buffer, format='PNG')
                page.insert_image(page.rect, stream=buffer.getvalue())
            else:
                remaining = page.insert_textbox(pymupdf.Rect(35,35,685,465), case['source'], fontname='cour', fontsize=13)
                if remaining < 0:
                    raise ValueError('English baseline text does not fit its source page')
        document.save(path, deflate=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path('dev_artifacts/english-quality'))
    args = parser.parse_args()
    fixtures = json.loads((Path(__file__).resolve().parents[1]/'tests/fixtures/english_quality.json').read_text())
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    version = hashlib.sha256(json.dumps(fixtures,sort_keys=True).encode()+Path(__file__).read_bytes()).hexdigest()[:8]
    source = output/f'English quality baseline-{version}.pdf'
    if not source.exists():
        create_pdf(source, fixtures['cases'])
    requests = []
    def handler(request):
        if request.method == 'GET':
            if request.url.path.endswith('/key'):
                return httpx.Response(200, json={'data':{'free_model_daily_requests':{'remaining':50,'limit':50}}})
            return httpx.Response(200, json={'data':[{'id':FREE_MODEL, 'context_length':1000000,
                'architecture':{'input_modalities':['text','image']}, 'supported_parameters':['structured_outputs']}]})
        payload = json.loads(request.content)
        requests.append(payload)
        numbers = payload['response_format']['json_schema']['schema']['properties']['notes']['items']['properties']['page']['enum']
        return httpx.Response(200, json={'model':FREE_MODEL, 'choices':[{'finish_reason':'stop', 'message':{
            'content':json.dumps({'notes':[{'page':page,'markdown':fixtures['cases'][page-1]['notes']} for page in numbers]})}}],
            'usage':{'cost':0,'prompt_tokens':0,'completion_tokens':0}})
    client = CloudClient(ModelConfig(api_key='offline-fixture'), httpx.MockTransport(handler))
    store = TaskStore(output/'baseline.sqlite3')
    pipeline = Pipeline(client, store=store)
    result = pipeline.run(str(source), str(output), language='en')
    markdown = Path(result.output_path).read_text()
    checks = {case['id']:all(value in markdown for value in case['literal_checks']) for case in fixtures['cases']}
    checks['adjacent_topic_merged'] = markdown.count('## Gradient Descent') == 1
    notebook = pipeline.run('', str(output), output_format='ipynb', task_id=result.task_id, action='export')
    cells = nbformat.read(notebook.output_path, as_version=4).cells
    checks['code_unexecuted'] = all(cell.execution_count is None and not cell.outputs for cell in cells if cell.cell_type == 'code')
    with pymupdf.open(source) as document:
        checks['scanned_page_has_no_text_layer'] = not document[-1].get_text().strip()
    report = {'mode':'offline-fixture', 'model_quality':'NOT ASSESSED: responses are fixed fixtures, not model outputs',
              'checks':checks, 'mock_inference_requests':len(requests), 'source':str(source),
              'markdown':result.output_path, 'notebook':notebook.output_path,
              'manual_review_for_future_live_run':fixtures['manual_review']}
    (output/'review.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
