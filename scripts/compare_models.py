"""Bounded free-only comparison. Offline fixtures verify plumbing, never model quality."""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import httpx
from app.core.cloud import CloudClient, CloudError, ModelConfig, FREE_MODEL
from app.core.configuration import make_client, migrate_config
from app.core.generator.notebook import NotebookGenerator
from app.core.markdown_text import NON_CONTENT_PAGE, markdown_blocks
from app.core.pipeline import Pipeline
from app.core.settings_store import SettingsStore
from app.core.task_store import TaskStore
from app.utils.helpers import load_config
from scripts.quality_baseline import create_pdf


FIXTURE = Path(__file__).resolve().parents[1]/'tests/fixtures/quality_comparison.json'


def mechanical_review(notes, cases):
    checks = {}
    for page, case in enumerate(cases,1):
        actual = notes.get(page,'')
        if case.get('non_content'):
            checks[case['id']] = {'non_content_receipt':actual.strip() == NON_CONTENT_PAGE}
            continue
        expected_blocks = [block for fenced,block in markdown_blocks(case['notes']) if fenced and block.startswith('```python')]
        actual_blocks = [block for fenced,block in markdown_blocks(actual) if fenced and block.startswith('```python')]
        checks[case['id']] = {'literal_checks':{value:value in actual for value in case.get('literal_checks',[])}}
        if expected_blocks:
            checks[case['id']]['python_code_exact'] = expected_blocks == actual_blocks
            visible = lambda blocks: ['\n'.join(line.rstrip() for line in block.splitlines()) for block in blocks]
            checks[case['id']]['python_visible_lines_exact'] = visible(expected_blocks) == visible(actual_blocks)
    return checks


def bounded(client, observations, cap):
    original = client.generate_segment
    def generate(*args, **kwargs):
        if len(observations) >= cap:
            raise CloudError('Acceptance inference cap reached; no more requests will be sent.','acceptance_limit')
        item = {'attempt':len(observations)+1,'elapsed_seconds':0}
        observations.append(item)
        started = time.monotonic()
        try:
            response = original(*args,**kwargs)
            item.update(finish=response.finish_reason,input_tokens=response.input_tokens,
                        output_tokens=response.output_tokens,reported_cost=response.cost)
            return response
        except CloudError as exc:
            item.update(error=exc.kind)
            raise
        finally:
            item['elapsed_seconds'] = round(time.monotonic()-started,3)
    client.generate_segment = generate
    return client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',help='Use only fixed free OpenRouter and a previously confirmed unbilled Google project')
    parser.add_argument('--config',type=Path,default=Path.home()/'.noteagent_config.json')
    parser.add_argument('--output-dir',type=Path,default=Path('dev_artifacts')/('quality-comparison-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')))
    parser.add_argument('--full-document',type=Path,help='Optional full document after manual comparison review')
    parser.add_argument('--winner',choices=['openrouter','gemini'],help='Explicitly reviewed candidate for full-document acceptance (maximum seven attempts)')
    args = parser.parse_args()
    args.output_dir = args.output_dir.expanduser().resolve()
    if bool(args.full_document) != bool(args.winner):
        parser.error('--full-document and --winner must be provided together after manual review')
    fixture = json.loads(FIXTURE.read_text())
    args.output_dir.mkdir(parents=True,exist_ok=True)
    source = args.output_dir/'English quality comparison.pdf'
    if not source.exists():
        create_pdf(source,fixture['cases'])
    config = (SettingsStore(args.config).load(migrate=False,fields=('api_key','gemini_api_key')).config if args.live else migrate_config(load_config(str(args.config))))
    report = {'mode':'live-free' if args.live else 'offline-fixture',
              'model_quality':'MANUAL REVIEW REQUIRED' if args.live else 'NOT ASSESSED: fixed responses',
              'paid_requests':0,'routes':{},'manual_checklist':fixture['manual_review'],
              'limitations':['Exact trailing spaces in authored code may not be observable in the PDF. Visible-line checks retain blank lines but ignore line-end spaces.',
                             'Literal checks can reject equivalent mathematical notation; manual source review is required.']}
    for service in ([args.winner] if args.winner else ['openrouter','gemini']):
        observations = []
        route = {'provider':service,'observations':observations,'manual_review':'pending'}
        report['routes'][service] = route
        selected = {**config,'provider':service,'primary_paid':False,'paid_enabled':False,'language':'en'}
        if service == 'openrouter':
            model = str(selected.get('vision_model') or FREE_MODEL)
            selected['vision_model'] = model if model.endswith(':free') and not model.startswith('openrouter/') else FREE_MODEL
        try:
            if args.live:
                client = make_client(selected)
                if client.config.paid or (client.is_openrouter and not client.model.endswith(':free')):
                    raise ValueError('Only fixed free routes are allowed by this runner.')
            else:
                def handler(request):
                    if request.method == 'GET':
                        if request.url.path.endswith('/key'):
                            return httpx.Response(200,json={'data':{'free_model_daily_requests':{'remaining':50,'limit':50}}})
                        return httpx.Response(200,json={'data':[{'id':FREE_MODEL,'context_length':1000000,'architecture':{'input_modalities':['text','image']},'supported_parameters':['structured_outputs']}]})
                    numbers = json.loads(request.content)['response_format']['json_schema']['schema']['properties']['notes']['items']['properties']['page']['enum']
                    return httpx.Response(200,json={'model':FREE_MODEL,'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'notes':[{'page':page,'markdown':fixture['cases'][page-1]['notes']} for page in numbers]})}}],'usage':{'cost':0}})
                client = CloudClient(ModelConfig(api_key='offline-fixture'),httpx.MockTransport(handler))
            full = args.full_document is not None and args.winner == service
            document = args.full_document if full else source
            store = TaskStore(args.output_dir/service/'tasks.sqlite3')
            started = time.monotonic()
            result = Pipeline(bounded(client,observations,7 if full else 3),store=store).run(str(document),str(args.output_dir/service),language='en')
            notes = {page:note for segment in store.segments(store.get(result.task_id)['cache_key']) for page,note in segment['notes'].items()}
            route.update(status=result.status,reason=result.reason,model=client.model,
                         completed_pages=result.completed_pages,page_count=result.page_count,
                         elapsed_seconds=round(time.monotonic()-started,3),markdown=result.output_path,
                         checks=mechanical_review(notes,fixture['cases']) if not full else {})
            if result.status == 'completed':
                notebook = Pipeline(None,store=store).run('',str(args.output_dir/service),task_id=result.task_id,action='export',output_format='ipynb')
                route['notebook'] = notebook.output_path
        except (ValueError,CloudError) as exc:
            route.update(status='pending',reason=str(exc))
        (args.output_dir/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(f"{service}: {route.get('status')} · {len(observations)} generation attempt(s)",flush=True)
    print('Report:',args.output_dir/'report.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
