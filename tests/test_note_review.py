import ast
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import nbformat
import pymupdf
from pptx import Presentation
from pptx.util import Inches

from app.core.cloud import SegmentResponse
from app.core.generator.markdown import MarkdownGenerator
from app.core.generator.notebook import NotebookGenerator
from app.core.markdown_text import markdown_blocks
from app.core.note_structure import assemble_notes, navigation_context, source_outline
from app.core.notion import NotionPublishResult
from app.core.pipeline import Pipeline
from app.core.source_review import SourceRenderer
from app.core.task_store import TaskStore


class ContentFidelityTests(unittest.TestCase):
    def test_python_numbers_literals_whitespace_and_code_headings_survive_audit(self):
        code = 'answer = (\n    42\n)\nmessage = """\n## Page 9\n<a id="page-9"></a>\n```markdown\n123\n\n\nkept  \n"""\n'
        for marker in ['```', '````', '~~~']:
            with self.subTest(marker=marker):
                source = f'# Lesson\n\n{marker}python\n{code}{marker}\n'
                audited = MarkdownGenerator().audit_document(source, 'Lesson').content
                blocks = [block for fenced,block in markdown_blocks(audited) if fenced]
                self.assertEqual(blocks, [f'{marker}python\n{code}{marker}\n'])
                notebook = NotebookGenerator().generate_from_markdown(audited, 'Lesson')
                cells = [cell for cell in notebook.cells if cell.cell_type == 'code']
                self.assertEqual(cells[0].source, code.removesuffix('\n'))
                self.assertEqual(ast.literal_eval(ast.parse(cells[0].source).body[0].value), 42)
                self.assertIsNone(cells[0].execution_count)
                self.assertEqual(cells[0].outputs, [])

    def test_adjacent_topics_join_without_losing_pages_examples_or_later_recurrence(self):
        notes = {1:'## Gradient Descent\n\nDefinition.',
                 2:'## Gradient Descent\n\n### Worked example\n\n$w_1=1.6$\n\n```python\n## Gradient Descent\nanswer = (\n42\n)\n```',
                 3:'## Learning Rate\n\nA different concept.',
                 4:'## Gradient Descent\n\nA later revisit.'}
        output = assemble_notes('Optimization', notes, 5)
        prose = ''.join(block for fenced,block in markdown_blocks(output) if not fenced)
        self.assertEqual(prose.count('## Gradient Descent'), 2)
        self.assertIn('### Worked example', output)
        self.assertIn('$w_1=1.6$', output)
        self.assertIn('answer = (\n42\n)', output)
        for page in notes:
            self.assertIn(f'> Source: page {page}', output)
        self.assertIn('missing source pages: 5', output)

    def test_navigation_is_bounded_and_has_document_end_and_prior_topic_names(self):
        pages = [SimpleNamespace(page_num=page, text=f'Topic {page}\nSource body') for page in range(1, 201)]
        outline = source_outline(pages)
        self.assertIn('Page 1:', outline)
        self.assertIn('Page 200:', outline)
        self.assertLessEqual(len(outline.splitlines()), 60)
        context = navigation_context(outline, '## Accepted topic\n```python\n## Not a topic\n```')
        self.assertIn('Accepted topic', context)
        self.assertNotIn('Not a topic', context)
        self.assertLessEqual(len(context), 8500)


class SavedRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = [2000000000]
        self.store = TaskStore(self.root/'tasks.sqlite3', clock=lambda:self.now[0])
        self.original = '# Original\n\n## Topic\n\nGenerated fact.\n\n> Source: page 1\n'
        self.options = {'input_path':str(self.root/'missing.pdf'), 'output_dir':str(self.root),
                        'output_format':'md', 'language':'en', 'title':'Original'}
        self.task = self.store.create('key', 'document', self.options)
        request = self.store.reserve(self.task, 'fixture', False, 0)
        self.store.commit_response(request, 'key', SegmentResponse({1:'## Topic\n\nGenerated fact.'}, 'stop', 'fixture'), True)
        self.store.update(self.task, status='completed', page_count=1, markdown=self.original)
        self.pipeline = Pipeline(None, store=self.store)

    def ledger(self):
        with self.store._db() as db:
            return [tuple(row) for row in db.execute('SELECT * FROM requests ORDER BY id')]

    def test_revisions_export_and_publish_independently_without_touching_cache_or_ledger(self):
        ledger, segments = self.ledger(), self.store.segments('key')
        revised = '# My revision\n\nA corrected explanation.\n\n```python\nanswer = (\n    42\n)\n```\n'
        saved = self.pipeline.run('', '', task_id=self.task, action='revise', revision_markdown=revised)
        self.assertGreater(saved.revision_id, 0)
        self.assertEqual(saved.exported_revision_id, 0)
        self.assertFalse(saved.output_path)
        exported = self.pipeline.run('', str(self.root), task_id=self.task, action='export')
        self.assertEqual(Path(exported.output_path).read_text(), revised)
        self.assertEqual(exported.exported_revision_id, saved.revision_id)
        notebook = self.pipeline.run('', str(self.root), task_id=self.task, action='export', output_format='ipynb')
        cells = nbformat.read(notebook.output_path, as_version=4).cells
        self.assertEqual(cells[0].source.count('# My revision'), 1)
        self.assertNotIn('# Original', cells[0].source)
        class Notion:
            calls = 0
            def append_to_page(self, page, markdown):
                self.calls += 1
                self.markdown = markdown
                return NotionPublishResult(page, 'https://notion.so/test', 'append')
        notion = Notion()
        for _ in range(2):
            published = self.pipeline.run('', '', task_id=self.task, action='publish', notion_client=notion,
                notion_page_id='0123456789abcdef0123456789abcdef', notion_mode='append')
        self.assertEqual(notion.calls, 1)
        self.assertEqual(notion.markdown, revised)
        self.assertEqual(published.published_revision_id, saved.revision_id)
        self.assertEqual(self.store.get(self.task)['markdown'], self.original)
        self.assertEqual(self.store.segments('key'), segments)
        self.assertEqual(self.ledger(), ledger)

    def test_saved_actions_do_not_fall_back_to_legacy_generation_without_an_explicit_store(self):
        class LegacyClient:
            def analyze_page(self, *args, **kwargs):
                raise AssertionError('A saved action must not generate content')
        with patch('app.core.jobs.TaskStore', return_value=self.store):
            result = Pipeline(LegacyClient()).run('', '', task_id=self.task, action='revise',
                                                   revision_markdown='# A saved correction')
        self.assertGreater(result.revision_id, 0)
        self.assertEqual(self.store.latest_revision(self.task)['markdown'], '# A saved correction')

    def test_revisions_are_append_only_task_scoped_and_survive_cache_cleanup(self):
        first = self.store.save_revision(self.task, '# First revision')
        self.assertEqual(self.store.save_revision(self.task, '# First revision'), first)
        second = self.store.save_revision(self.task, '# Second revision')
        self.assertGreater(second, first)
        other = self.store.create('key', 'document', self.options)
        self.store.update(other, status='completed', page_count=1)
        self.assertIsNone(self.store.latest_revision(other))
        self.now[0] += 50*86400
        self.store.create('latest', 'new', {})
        self.store.clean_cache()
        with self.store._db() as db:
            bodies = [row[0] for row in db.execute('SELECT markdown FROM revisions ORDER BY id')]
        self.assertEqual(bodies, ['# First revision', '# Second revision'])
        self.assertTrue(self.store.segments('key'))
        self.assertEqual(self.store.latest_revision(self.task)['id'], second)
        for invalid in ['', '  ', None]:
            with self.assertRaises(ValueError):
                self.store.save_revision(self.task, invalid)

    def test_reconciliation_clears_pause_only_after_all_document_requests_settle(self):
        sibling = self.store.create('other-key', 'document', self.options)
        self.store.update(self.task, status='paused', reason='uncertain_cost')
        self.store.update(sibling, status='paused', reason='uncertain_cost')
        rows = [self.store.reserve(task, 'paid', True, .04) for task in [self.task, sibling]]
        for request in rows:
            self.store.fail_request(request, uncertain=True)
        self.store.reconcile(rows[0], .001)
        self.assertEqual(self.store.get(self.task)['reason'], 'uncertain_cost')
        self.store.reconcile(rows[1], 0)
        self.assertEqual(self.store.get(self.task)['reason'], '')
        self.assertEqual(self.store.get(sibling)['reason'], '')
        self.assertAlmostEqual(self.store.cost(sibling), .001)

    def test_local_source_viewing_validates_document_identity_and_page_range(self):
        path = self.root/'source.pdf'
        with pymupdf.open() as document:
            for number in [1,2]:
                page = document.new_page()
                page.insert_text((50,50), f'Original page {number}')
            document.save(path)
        renderer = SourceRenderer(path, hashlib.sha256(path.read_bytes()).hexdigest())
        self.addCleanup(renderer.close)
        first, second = renderer.render(1), renderer.render(2)
        self.assertTrue(first.startswith(b'\x89PNG'))
        self.assertNotEqual(first, second)
        with self.assertRaises(ValueError):
            renderer.render(3)
        with self.assertRaisesRegex(ValueError, 'differs'):
            SourceRenderer(path, 'different').render(1)

    def test_pptx_source_view_reuses_local_rendering_and_removes_temporary_files(self):
        source = self.root/'slides.pptx'
        deck = Presentation()
        for name in ['Definition', 'Worked example']:
            slide = deck.slides.add_slide(deck.slide_layouts[6])
            slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(2)).text = name
        deck.save(source)
        renderer = SourceRenderer(source, hashlib.sha256(source.read_bytes()).hexdigest())
        self.addCleanup(renderer.close)
        with patch('app.core.parser.pptx_parser.PPTXParser._find_soffice', return_value=None):
            first = renderer.render(1)
            workspace = Path(renderer.parser.temp_dir)
            with patch.object(renderer.parser, 'parse', side_effect=AssertionError('Repeated rendering')):
                second = renderer.render(2)
            self.assertNotEqual(first, second)
            self.assertTrue(workspace.exists())
            renderer.close()
            self.assertFalse(workspace.exists())

    def test_fixed_english_baseline_has_scanned_material_and_reports_only_offline_checks(self):
        root = Path(__file__).resolve().parents[1]
        completed = subprocess.run([sys.executable, str(root/'scripts/quality_baseline.py'), '--output-dir', str(self.root/'baseline')],
                                   cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr+completed.stdout)
        report = json.loads((self.root/'baseline/review.json').read_text())
        self.assertEqual(report['mode'], 'offline-fixture')
        self.assertIn('NOT ASSESSED', report['model_quality'])
        self.assertTrue(all(report['checks'].values()))
        self.assertEqual(report['mock_inference_requests'], 1)
