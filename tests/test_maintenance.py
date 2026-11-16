import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from app.core.cloud import SegmentResponse
from app.core.maintenance import (WORKSPACE_MARKER, clean_selected_storage, clean_stale_workspaces,
                                  create_workspace, storage_info)
from app.core.notion import NotionPublishResult
from app.core.pipeline import Pipeline
from app.core.task_store import BudgetExceeded, TaskBusy, TaskStore, selected_cache_plan


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

    def test_selected_recent_cache_keeps_files_publication_metadata_history_and_ledger(self):
        chosen = self.task('recent', paid=True)
        untouched = self.task('other')
        task = self.store.get(chosen)
        task['options'].update(published_signature='already-published', publication_state='sent',
                               notion_page_id='0123456789abcdef0123456789abcdef')
        self.store.update(chosen, options=task['options'])
        before = self.store.get(chosen)
        other = self.store.get(untouched)
        ledger = self.ledger()
        output = Path(before['output_path'])
        saved = output.read_bytes()
        source = self.root/'original.pdf'
        source.write_bytes(b'original document remains untouched')
        with patch('httpx.Client.send', side_effect=AssertionError('Cleanup must not use the network')):
            info = storage_info(self.store.path)
            self.assertEqual({row['id'] for row in info['tasks']}, {chosen, untouched})
            self.assertFalse(next(row for row in self.store.cache_tasks(chosen) if row['id'] == chosen)['protected_reason'])
            result = clean_selected_storage(self.store.path, [chosen], protect_task_id=chosen)
        self.assertGreater(result['removed_bytes'], 0)
        self.assertEqual(result['cleared_task_ids'], [chosen])
        self.assertEqual({row['id'] for row in result['tasks']}, {untouched})
        self.assertEqual(self.store.get(chosen), {**before, 'markdown':''})
        self.assertEqual(self.store.get(untouched), other)
        self.assertTrue(self.store.segments('other'))
        self.assertFalse(self.store.segments('recent'))
        self.assertEqual(output.read_bytes(), saved)
        self.assertEqual(source.read_bytes(), b'original document remains untouched')
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(self.store.cost(chosen), .01)

    def test_selected_shared_cache_counts_bytes_once_and_keeps_unselected_task_data(self):
        first = self.task('shared', age=2)
        second = self.task('shared', age=1)
        rows = self.store.cache_tasks()
        by_id = {row['id']:row for row in rows}
        one = selected_cache_plan(rows, [first])
        self.assertEqual(one['reclaimable_bytes'], by_id[first]['body_bytes'])
        self.assertTrue(one['shared_retained'])
        self.assertEqual(one['cache_keys'], [])
        both = selected_cache_plan(rows, [first, second, first])
        self.assertEqual(both['reclaimable_bytes'],
                         by_id[first]['body_bytes'] + by_id[second]['body_bytes'] + by_id[first]['shared_bytes'])
        self.assertFalse(both['shared_retained'])
        partial = self.store.clean_selected_cache([first])
        self.assertEqual(partial['cleared_task_ids'], [])
        self.assertFalse(self.store.get(first)['markdown'])
        self.assertTrue(self.store.get(second)['markdown'])
        self.assertTrue(self.store.segments('shared'))
        remaining = self.store.clean_selected_cache([first, second])
        self.assertGreater(remaining['removed_bytes'], 0)
        self.assertFalse(self.store.segments('shared'))
        self.assertEqual(set(remaining['cleared_task_ids']), {first, second})
        self.assertEqual(self.store.cache_tasks(), [])
        self.assertTrue(self.store.get(first))
        self.assertTrue(self.store.get(second))

    def test_selection_lists_and_enforces_protection_for_every_shared_task(self):
        pinned = self.task('pinned', status='paused')
        sibling = self.task('pinned')
        revision = self.task('revision')
        self.store.save_revision(revision, '# Edited notes')
        pending = self.task('notion', reason='notion_failed')
        waiting = self.task('unfinished', status='waiting_quota')
        uncertain = self.task('uncertain', paid=True)
        request = self.store.reserve(uncertain, 'model', True, .01)
        self.store.fail_request(request, uncertain=True)
        no_export = self.task('missing')
        Path(self.store.get(no_export)['output_path']).unlink()
        reasons = {row['id']:row['protected_reason'] for row in self.store.cache_tasks(pinned)}
        self.assertEqual(reasons[pinned], 'Current task')
        self.assertEqual(reasons[sibling], 'Current task')
        self.assertEqual(reasons[revision], 'Saved human revision')
        self.assertEqual(reasons[pending], 'Notion retry pending')
        self.assertEqual(reasons[waiting], 'Unfinished task')
        self.assertEqual(reasons[uncertain], 'Unresolved paid request')
        self.assertEqual(reasons[no_export], 'No saved local export')
        for task in reasons:
            with self.subTest(task=task), self.assertRaises(ValueError):
                self.store.clean_selected_cache([task], protect_task_id=pinned)
            self.assertTrue(self.store.get(task)['markdown'])
        self.assertEqual(self.store.latest_revision(revision)['markdown'], '# Edited notes')

    def test_selected_cleanup_revalidates_status_and_export_before_mutating_any_task(self):
        good = self.task('good')
        stale = self.task('stale')
        rows = self.store.cache_tasks()
        self.assertGreater(selected_cache_plan(rows, [good, stale])['reclaimable_bytes'], 0)
        self.store.update(stale, status='paused')
        with self.assertRaises(ValueError):
            self.store.clean_selected_cache([good, stale])
        self.assertTrue(self.store.get(good)['markdown'])
        self.assertTrue(self.store.segments('good'))
        self.store.update(stale, status='completed')
        Path(self.store.get(stale)['output_path']).unlink()
        with self.assertRaises(ValueError):
            self.store.clean_selected_cache([good, stale])
        with self.assertRaises(ValueError):
            self.store.clean_selected_cache([good, 'unknown-task'])
        self.assertTrue(self.store.get(good)['markdown'])

    def test_selected_cleanup_refuses_running_tasks_and_rolls_back_cancellation(self):
        good = self.task('good')
        busy = self.task('busy', status='running')
        with self.assertRaises(TaskBusy):
            self.store.clean_selected_cache([good])
        self.store.update(busy, status='completed')
        calls = []
        def cancel():
            calls.append(True)
            return len(calls) >= 2
        with self.assertRaises((ValueError, sqlite3.OperationalError)):
            self.store.clean_selected_cache([good], cancel_callback=cancel)
        self.assertTrue(self.store.get(good)['markdown'])
        self.assertTrue(self.store.segments('good'))
        empty = self.store.clean_selected_cache([])
        self.assertEqual(empty['removed_bytes'], 0)

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
