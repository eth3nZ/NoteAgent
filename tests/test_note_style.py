import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import nbformat

from app.core.cloud import CloudClient, ModelConfig, SegmentResponse
from app.core.generator.markdown import MarkdownGenerator, render_source_pages
from app.core.markdown_text import NON_CONTENT_PAGE, is_non_content_page, markdown_blocks
from app.core.note_structure import assemble_notes
from app.core.notion import NotionPublishResult
from app.core.pipeline import Pipeline
from app.core.task_store import TaskStore


DEFINITION = 'A variational autoencoder represents each input with a latent distribution.'


class NoteStyleTests(unittest.TestCase):
    def test_exact_continuation_definition_is_joined_with_new_facts_and_example_intact(self):
        notes = {1:f'## VAE\n\n{DEFINITION}\n\nFirst fact.',
                 2:f'## VAE\n\n{DEFINITION}\n\n### Worked example\n\n$z=1+1\\times2=3$\n\nNew fact.'}
        content = assemble_notes('Lesson', notes, 2)
        self.assertEqual(content.count('## VAE'), 1)
        self.assertEqual(content.count(DEFINITION), 1)
        for value in ['First fact.', 'New fact.', '$z=1+1\\times2=3$', '### Worked example',
                      '> Source: page 1', '> Source: page 2']:
            self.assertIn(value, content)

    def test_different_definitions_and_later_revisits_are_not_guessed_away(self):
        second = 'A variational autoencoder can be viewed as a probabilistic encoder and decoder.'
        notes = {1:f'## VAE\n\n{DEFINITION}', 2:f'## VAE\n\n{second}',
                 3:'## Other concept\n\nA different concept.', 4:f'## VAE\n\n{DEFINITION}'}
        content = assemble_notes('Lesson', notes, 4)
        self.assertEqual(content.count(DEFINITION), 2)
        self.assertIn(second, content)
        self.assertEqual(content.count('## VAE'), 2)

    def test_repeated_words_in_source_code_and_distinct_subsections_remain(self):
        code = 'message = """\n'+DEFINITION+'\n\n\nkept  \n"""\nanswer = (\n    42\n)\n'
        notes = {1:f'## VAE\n\n{DEFINITION}',
                 2:f'## VAE\n\n{DEFINITION}\n\n```python\n{code}```\n',
                 3:f'## VAE\n\n### A separate example\n\n{DEFINITION}'}
        content = assemble_notes('Lesson', notes, 3)
        blocks = [block for fenced,block in markdown_blocks(content) if fenced]
        self.assertEqual(blocks, ['```python\n'+code+'```\n'])
        self.assertEqual(content.count(DEFINITION), 3)
        self.assertIn('### A separate example', content)

    def test_lists_math_and_comparison_tables_are_never_deduplicated_as_definitions(self):
        for body in ['- A detailed list item that happens to be repeated across these two pages.',
                     '$$\nL = L_{reconstruction} + L_{KL}\n$$',
                     r'\[L = L_{reconstruction} + L_{KL}; a mathematical expression\]',
                     '    result = sample(mean, log_variance, epsilon, preserve_this_code)',
                     'SELECT descriptive_column_name FROM a_long_descriptive_table_name;',
                     'The formula is $L=L_{reconstruction}+L_{KL}$ for this experiment.',
                     'The exact literal `a  b` preserves two spaces and must remain unchanged.',
                     '| Method | Benefit |\n| --- | --- |\n| VAE | Probabilistic generation |']:
            with self.subTest(body=body):
                content = assemble_notes('Lesson', {1:'## VAE\n\n'+body, 2:'## VAE\n\n'+body}, 2)
                self.assertEqual(content.count(body), 2)

    def test_non_content_receipts_keep_coverage_without_visible_filler(self):
        content = assemble_notes('Lesson', {1:NON_CONTENT_PAGE, 2:'## Topic\n\nA useful fact.', 3:NON_CONTENT_PAGE}, 4)
        self.assertNotIn(NON_CONTENT_PAGE, content)
        self.assertIn('missing source pages: 4', content)
        self.assertIn('> Source: page 2', content)
        self.assertNotIn('> Source: page 1', content)
        self.assertNotIn('> Source: page 3', content)

    def test_all_non_content_pages_have_an_explanation_and_no_missing_page_claim(self):
        for language, message in [('en','No study content was included'), ('zh','没有可纳入的学习内容')]:
            content = assemble_notes('Lesson', {1:NON_CONTENT_PAGE, 2:NON_CONTENT_PAGE}, 2, language)
            self.assertIn(message, content)
            self.assertNotIn('missing source', content)
            self.assertNotIn(NON_CONTENT_PAGE, content)

    def test_non_content_page_does_not_break_a_continuing_topic(self):
        notes = {1:f'## VAE\n\n{DEFINITION}', 2:NON_CONTENT_PAGE,
                 3:f'## VAE\n\n{DEFINITION}\n\n### Example\n\nA new result.'}
        content = assemble_notes('Lesson', notes, 3)
        self.assertEqual(content.count(DEFINITION), 1)
        self.assertEqual(content.count('## VAE'), 1)
        self.assertIn('A new result.', content)

    def test_receipt_is_only_special_when_it_is_the_entire_note(self):
        self.assertTrue(is_non_content_page(' \n'+NON_CONTENT_PAGE+'\n'))
        self.assertEqual(MarkdownGenerator().sanitize_fragment(NON_CONTENT_PAGE), '')
        for text in ['```html\n'+NON_CONTENT_PAGE+'\n```', '## Actual content\n\n'+NON_CONTENT_PAGE+'\nA useful fact.']:
            self.assertFalse(is_non_content_page(text))
            self.assertIn(NON_CONTENT_PAGE, MarkdownGenerator().sanitize_fragment(text))

    def test_cloud_accepts_an_explicit_receipt_but_still_rejects_blank_or_missing_entries(self):
        for body, expected in [(NON_CONTENT_PAGE,'stop'), ('','incomplete'), (' ','incomplete')]:
            rows = [{'page':1,'markdown':body}, {'page':2,'markdown':'## Fact\nA useful fact.'}]
            client = CloudClient(ModelConfig(api_key='test'), httpx.MockTransport(lambda request:httpx.Response(200,
                json={'choices':[{'message':{'content':json.dumps({'notes':rows})},'finish_reason':'stop'}]})))
            self.assertEqual(client._send({}, [1,2], None).finish_reason, expected)

    def test_style_prompt_changes_invalidate_new_generation_cache_keys(self):
        from app.core.jobs import cache_key_for
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'NOTEAGENT_PROMPTS_DIR':directory}):
            path = Path(directory)/'segment_system.md'
            path.write_text('Original style in {{language}}.')
            client = CloudClient(ModelConfig(api_key='test'))
            before = cache_key_for('document',client.config.fingerprint(),client.prompt_hash(),'en')
            path.write_text('Concise style in {{language}}.')
            after = cache_key_for('document',client.config.fingerprint(),client.prompt_hash(),'en')
        self.assertNotEqual(before, after)

    def test_non_content_receipt_does_not_remove_a_paid_request_charge(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory)/'tasks.sqlite3')
            task = store.create('key','document',{})
            request = store.reserve(task,'fixture',True,.01)
            store.commit_response(request,'key',SegmentResponse({1:NON_CONTENT_PAGE},'stop','fixture',cost=.002),True)
            self.assertEqual(store.cost(task),.002)
            self.assertEqual(store.segments('key')[0]['notes'],{1:NON_CONTENT_PAGE})

    def test_style_fixture_exports_and_reuses_omitted_pages_without_additional_inference(self):
        root = Path(__file__).resolve().parents[1]
        fixture = root/'tests/fixtures/note_style.json'
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run = subprocess.run([sys.executable,str(root/'scripts/quality_baseline.py'),'--fixture',str(fixture),
                                  '--output-dir',directory],cwd=root,capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            report = json.loads((output/'review.json').read_text())
            self.assertTrue(all(report['checks'].values()),report['checks'])
            self.assertIn('NOT ASSESSED',report['model_quality'])
            self.assertEqual(report['reviewed_non_content_pages'],[1,6])
            self.assertEqual(report['mock_inference_requests'],1)
            markdown = Path(report['markdown']).read_text()
            cells = nbformat.read(report['notebook'],as_version=4).cells
            self.assertNotIn(NON_CONTENT_PAGE,'\n'.join(cell.source for cell in cells))
            code = [cell for cell in cells if cell.cell_type=='code']
            self.assertIn('exp(log_var)',code[0].source)
            self.assertNotIn('exp(0.5',code[0].source)
            self.assertTrue(all(cell.execution_count is None and not cell.outputs for cell in code))
            store = TaskStore(output/'baseline.sqlite3')
            task = store.latest()
            self.assertEqual(task['page_count'],7)
            ledger = store.cost(task['id'])
            class Notion:
                calls = 0
                def append_to_page(self,page,content):
                    self.calls += 1
                    self.content = content
                    return NotionPublishResult(page,'https://notion.so/fixture','append')
            notion = Notion()
            for _ in range(2):
                Pipeline(None,store=store).run('',directory,task_id=task['id'],action='publish',notion_client=notion,
                    notion_page_id='0123456789abcdef0123456789abcdef',notion_mode='append')
            self.assertEqual(notion.calls,1)
            self.assertEqual(notion.content,render_source_pages(markdown,False))
            self.assertEqual(store.cost(task['id']),ledger)
