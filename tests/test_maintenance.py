import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from app.core.cloud import SegmentResponse
from app.core.maintenance import WORKSPACE_MARKER, clean_stale_workspaces, create_workspace
from app.core.notion import NotionPublishResult
from app.core.pipeline import Pipeline
from app.core.task_store import BudgetExceeded, TaskBusy, TaskStore


class CacheMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = [2_000_000_000.0]
        self.store = TaskStore(self.root/'tasks.sqlite3', clock=lambda:self.now[0])

    def task(self, key, age=0, status='completed', reason='', publication=None, paid=False):
        current = self.now[0]
        self.now[0] -= age*86400
        try:
            options = {'input_path':str(self.root/'missing.pdf'), 'output_dir':str(self.root),
                       'output_format':'md', 'language':'en', 'title':'Notes'}
            if publication:
                options['publication_state'] = publication
            task = self.store.create(key, key, options)
            request = self.store.reserve(task, 'test/model', paid, .04 if paid else 0)
            self.store.commit_response(request,key,SegmentResponse({1:'## Topic\n\nA fact.'},'stop','test/model',cost=.01),True)
            markdown = '# Notes\n\n## Topic\n\nA fact.\n\n> Source: page 1\n'
            output = self.root/(task+'.md')
            output.write_text(markdown)
            self.store.update(task,status=status,reason=reason,page_count=1,markdown=markdown,output_path=str(output))
            return task
        finally:
            self.now[0] = current

    def ledger(self):
        with self.store._db() as db:
            return [tuple(row) for row in db.execute('SELECT * FROM requests ORDER BY id')]

    def test_expired_content_is_removed_but_outputs_history_and_budget_survive(self):
        old = self.task('old',age=40,paid=True)
        newest = self.task('new')
        output = Path(self.store.get(old)['output_path'])
        original = output.read_bytes()
        ledger = self.ledger()
        self.assertGreater(self.store.cache_info()['reclaimable_bytes'],0)
        result = self.store.clean_cache()
        self.assertGreater(result['removed_bytes'],0)
        self.assertFalse(self.store.segments('old'))
        self.assertEqual(self.store.get(old)['markdown'],'')
        self.assertEqual(output.read_bytes(),original)
        self.assertEqual(self.ledger(),ledger)
        self.assertEqual(self.store.latest()['id'],newest)
        self.assertEqual(self.store.cost(old),.01)
        with self.assertRaises(BudgetExceeded):
            self.store.reserve(old,'test/model',True,.10,document_limit=.10)
        self.assertEqual(self.store.cache_info()['reclaimable_bytes'],0)

    def test_current_incomplete_retry_and_uncertain_paid_notes_are_protected(self):
        protected = []
        for status in ['ready','paused','cancelled','failed','waiting_quota','waiting_paid']:
            protected.append(self.task(status,age=40,status=status))
        for publication in ['sending','uncertain']:
            protected.append(self.task(publication,age=40,publication=publication))
        for reason in ['notion_setup','notion_failed']:
            protected.append(self.task(reason,age=40,reason=reason))
        uncertain = self.task('paid',age=40,paid=True)
        request = self.store.reserve(uncertain,'test/model',True,.04)
        self.store.fail_request(request,uncertain=True)
        protected.append(uncertain)
        pinned = self.task('pinned',age=40)
        newest = self.task('newest')
        self.store.clean_cache(protect_task_id=pinned)
        for task in [*protected,pinned,newest]:
            saved = self.store.get(task)
            self.assertTrue(saved['markdown'])
            self.assertTrue(self.store.segments(saved['cache_key']))

    def test_latest_task_is_retained_even_after_retention_period(self):
        current = self.task('current',age=60)
        self.store.clean_cache()
        self.assertTrue(self.store.get(current)['markdown'])

    def test_missing_export_keeps_the_only_cached_copy(self):
        old = self.task('missing',age=40)
        Path(self.store.get(old)['output_path']).unlink()
        self.task('newest')
        self.store.clean_cache()
        self.assertTrue(self.store.get(old)['markdown'])
        self.assertTrue(self.store.segments('missing'))

    def test_duplicate_bodies_compact_without_losing_independent_export_or_publishing(self):
        old = self.task('same',age=2,paid=True)
        latest = self.task('same',age=1,paid=True)
        ledger = self.ledger()
        result = self.store.clean_cache()
        self.assertEqual(result['compacted_bodies'],1)
        self.assertFalse(self.store.get(old)['markdown'])
        self.assertTrue(self.store.get(latest)['markdown'])
        self.assertTrue(self.store.segments('same'))
        self.assertEqual(self.ledger(),ledger)
        class Notion:
            def append_to_page(self, page, markdown):
                self.markdown = markdown
                return NotionPublishResult(page,'https://notion.so/test','append')
        notion = Notion()
        pipeline = Pipeline(None,store=self.store)
        published = pipeline.run('',str(self.root),task_id=old,action='publish',notion_client=notion,
            notion_page_id='0123456789abcdef0123456789abcdef',notion_mode='append')
        self.assertEqual(published.reason,'')
        self.assertIn('A fact.',notion.markdown)
        self.assertNotIn('Source: page',notion.markdown)
        exported = pipeline.run('',str(self.root),task_id=old,action='export')
        self.assertIn('A fact.',Path(exported.output_path).read_text())
        self.assertEqual(self.ledger(),ledger)

    def test_cleanup_refuses_running_task_without_partial_deletion(self):
        old = self.task('old',age=40)
        self.task('running',status='running')
        before = self.store.get(old)['markdown']
        with self.assertRaises(TaskBusy):
            self.store.clean_cache()
        self.assertEqual(self.store.get(old)['markdown'],before)
        self.assertTrue(self.store.segments('old'))

    def test_shared_cache_is_retained_for_an_incomplete_task(self):
        old = self.task('shared',age=50)
        self.task('shared',age=40,status='paused')
        self.task('new')
        self.store.clean_cache()
        self.assertTrue(self.store.get(old)['markdown'])
        self.assertTrue(self.store.segments('shared'))

    def test_orphan_segments_are_reclaimed(self):
        self.task('current')
        with self.store._db() as db:
            db.execute('INSERT INTO segments VALUES(?,?,?,?,?,?)',('orphan',1,1,'{"1":"unused"}','model','host'))
        self.store.clean_cache()
        self.assertFalse(self.store.segments('orphan'))

    def test_acceptance_cache_check_does_not_leave_duplicate_exports(self):
        source = self.root/'sample.pdf'
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((40,60),'A short English fixture for export validation.')
            document.save(source)
        output = self.root/'acceptance'
        completed = subprocess.run([sys.executable,'scripts/validate_document.py',str(source),
            '--config',str(self.root/'empty.json'),'--output-dir',str(output)],
            cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=30)
        self.assertEqual(completed.returncode,0,completed.stderr+completed.stdout)
        report = json.loads((output/'report.json').read_text())
        self.assertEqual(report['mode'],'offline-fixture')
        self.assertEqual(report['language'],'en')
        self.assertEqual(report['cached_rerun_inference_attempts'],0)
        self.assertEqual(len(list(output.glob('*_notes.md'))),1)
        self.assertEqual(len(list(output.glob('*_notes.ipynb'))),1)
        self.assertFalse(list(output.glob('*_notes_2.md')))
        latest = TaskStore(output/'validation.sqlite3').latest()
        self.assertTrue(Path(latest['output_path']).is_file())


@unittest.skipUnless(os.name == 'posix','POSIX process-liveness check')
class WorkspaceMaintenanceTests(unittest.TestCase):
    def test_only_old_marked_dead_process_workspaces_are_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            now = 2_000_000_000
            def workspace(age,pid):
                path = Path(create_workspace('pdf',root))
                marker = json.loads((path/WORKSPACE_MARKER).read_text())
                marker.update(pid=pid,created=now-age)
                (path/WORKSPACE_MARKER).write_text(json.dumps(marker))
                return path
            dead = workspace(2*86400,1234567)
            active = workspace(2*86400,os.getpid())
            recent = workspace(60,1234567)
            legacy = root/'noteagent_pdf_legacy';legacy.mkdir()
            link = root/'noteagent_pdf_link';link.symlink_to(active,target_is_directory=True)
            def check(pid,signal):
                if pid == 1234567:
                    raise ProcessLookupError()
            with patch('app.core.maintenance.os.kill',side_effect=check):
                self.assertEqual(clean_stale_workspaces(root,now=now),1)
            self.assertFalse(dead.exists())
            for path in [active,recent,legacy,link]:
                self.assertTrue(path.exists())

