import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import nbformat
import pymupdf

from app.core.cloud import CloudError, ModelConfig, SegmentResponse
from app.core.jobs import next_free_reset
from app.core.notion import NotionPublishResult
from app.core.pipeline import Pipeline
from app.core.task_store import BudgetExceeded, TaskBusy, TaskStore


class StubCloud:
    def __init__(self, paid=False):
        self.config = ModelConfig(api_key="test", model="qwen/qwen3.8-27b" if paid else "nvidia/nemotron-3.5-super-vl:free", paid=paid)
        self.model = self.config.model
        self.calls = []
        self.handler = None
        self.cost = .002 if paid else 0

    def prompt_hash(self):
        return "prompt-v1"

    def prepare(self):
        pass

    def reservation_usd(self):
        return .04 if self.config.paid else 0

    def generate_segment(self, pages, language, cancel):
        numbers = [p.page_num for p in pages]
        self.calls.append(numbers)
        if self.handler:
            value = self.handler(numbers)
            if value is not None:
                return value
        return SegmentResponse({n: f"## Topic {n}\n\n- formula $x={n}$\n\n```python\nvalue = {n}\n```" for n in numbers},
                               "stop", self.model, "stub", 100, 50, self.cost)


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = [datetime(2026, 10, 30, 12, tzinfo=timezone.utc).timestamp()]
        self.store = TaskStore(self.root / "tasks.sqlite3", clock=lambda: self.now[0])
        self.source = self.root / "Lecture.pdf"
        self.pdf(17)
        self.client = StubCloud()

    def pdf(self, count):
        with pymupdf.open() as doc:
            for i in range(count):
                page = doc.new_page(width=300, height=180)
                page.insert_text((30, 40), f"source content page {i+1}")
            doc.save(self.source)

    def run_job(self, **kwargs):
        pipeline = Pipeline(self.client, store=self.store, paid_client=kwargs.pop("backup", None),
                            document_limit=kwargs.pop("document_limit", .10), monthly_limit=kwargs.pop("monthly_limit", 1))
        return pipeline.run(str(self.source), str(self.root), language="en", **kwargs)

    def test_eight_page_segments_cache_and_notebook(self):
        first = self.run_job(include_source_pages=True)
        self.assertEqual(first.status, "completed")
        profile = self.store.get(first.task_id)["options"]["primary_profile"]
        self.assertEqual(profile, self.client.config.fingerprint())
        self.assertNotIn("api_key", profile)
        self.assertEqual([len(c) for c in self.client.calls], [8, 8, 1])
        self.assertEqual(first.completed_pages, list(range(1, 18)))
        self.assertEqual(list((self.root/'workspaces').iterdir()),[])
        output = Path(first.output_path).read_text()
        for n in range(1, 18):
            self.assertIn(f"Source: page {n}", output)
        second = self.run_job(output_format="ipynb")
        self.assertEqual(len(self.client.calls), 3)
        notebook = nbformat.read(second.output_path, as_version=4)
        self.assertEqual(sum(c.cell_type == "code" for c in notebook.cells), 17)

    def test_checkpoint_cancel_resume_does_not_repeat_successful_pages(self):
        cancel = [False]
        def state(result):
            if len(result.completed_pages) >= 8:
                cancel[0] = True
        first = self.run_job(cancel_callback=lambda: cancel[0], state_callback=state)
        self.assertEqual(first.status, "cancelled")
        self.assertEqual(list((self.root/'workspaces').iterdir()),[])
        self.assertEqual(len(first.completed_pages), 8)
        second = self.run_job(task_id=first.task_id)
        self.assertEqual(second.status, "completed")
        self.assertEqual(self.client.calls, [list(range(1, 9)), list(range(9, 17)), [17]])

    def test_cross_segment_navigation_reuses_only_previously_accepted_topic_names(self):
        contexts = []
        self.client.set_document_context = contexts.append
        result = self.run_job()
        self.assertEqual(result.status, 'completed')
        self.assertEqual(len(contexts), 3)
        self.assertIn('Page 17:', contexts[0])
        self.assertIn('Topic 1', contexts[1])
        self.assertNotIn('Topic 9', contexts[1])
        self.assertIn('Topic 16', contexts[2])

    def test_source_pages_are_export_only_and_do_not_invalidate_cached_notes(self):
        first = self.run_job()
        hidden = Path(first.output_path).read_text()
        self.assertNotIn("> Source: page", hidden)
        original = self.store.get(first.task_id)
        notes = self.store.segments(original['cache_key'])
        self.assertIn("> Source: page 17", original['markdown'])
        before = len(self.client.calls)
        pipeline = Pipeline(None, store=self.store)
        shown = pipeline.run("",str(self.root),task_id=first.task_id,action="export",
                             output_format="ipynb",include_source_pages=True)
        notebook = nbformat.read(shown.output_path,as_version=4)
        text = "\n".join(c.source for c in notebook.cells if c.cell_type == "markdown")
        self.assertIn("> Source: page 17",text)
        self.assertTrue(all(c.execution_count is None and not c.outputs for c in notebook.cells if c.cell_type == "code"))
        hidden_nb = pipeline.run("",str(self.root),task_id=first.task_id,action="export",
                                 output_format="ipynb",include_source_pages=False)
        notebook = nbformat.read(hidden_nb.output_path,as_version=4)
        self.assertNotIn("> Source: page","\n".join(c.source for c in notebook.cells if c.cell_type == "markdown"))
        self.assertEqual(sum(c.cell_type == "code" for c in notebook.cells),17)
        exported = pipeline.run("",str(self.root),task_id=first.task_id,action="export")
        self.assertEqual(Path(exported.output_path).read_text(),hidden)
        self.assertEqual(self.store.get(first.task_id)['markdown'],original['markdown'])
        self.assertEqual(self.store.segments(original['cache_key']),notes)
        cached = self.run_job(include_source_pages=True)
        self.assertIn("> Source: page 17",Path(cached.output_path).read_text())
        self.assertEqual(self.store.get(cached.task_id)['cache_key'],original['cache_key'])
        self.assertEqual(len(self.client.calls),before)

    def test_notion_uses_source_page_setting_without_altering_canonical_notes(self):
        class Notion:
            def __init__(self):
                self.contents = []
            def append_to_page(self, page, markdown):
                self.contents.append(markdown)
                return NotionPublishResult(page,"https://notion.so/test","append")
        notion = Notion()
        parent = "01234567-89ab-cdef-0123-456789abcdef"
        first = self.run_job(notion_client=notion,notion_page_id=parent,notion_mode="append")
        self.assertEqual(notion.contents,[Path(first.output_path).read_text()])
        self.assertNotIn("> Source: page",notion.contents[0])
        before = len(self.client.calls)
        pipeline = Pipeline(None,store=self.store)
        for _ in range(2):
            pipeline.run("","",task_id=first.task_id,action="publish",notion_client=notion,
                         notion_page_id=parent,notion_mode="append",include_source_pages=True)
        self.assertEqual(len(notion.contents),2)
        self.assertIn("> Source: page 17",notion.contents[-1])
        self.assertIn("> Source: page 17",self.store.get(first.task_id)['markdown'])
        self.assertEqual(len(self.client.calls),before)

    def test_daily_quota_waits_then_requires_manual_paid_action(self):
        backup = StubCloud(paid=True)
        self.client.handler = lambda pages: (_ for _ in ()).throw(CloudError("daily", "daily_quota"))
        first = self.run_job(backup=backup)
        self.assertEqual(first.status, "waiting_quota")
        self.assertFalse(first.paid_eligible)
        self.run_job(task_id=first.task_id, backup=backup)
        self.assertEqual(len(self.client.calls), 1)
        self.assertFalse(backup.calls)
        self.now[0] = first.retry_at + 1
        second = self.run_job(task_id=first.task_id, backup=backup)
        self.assertEqual(second.status, "waiting_paid")
        self.assertTrue(second.paid_eligible)
        self.assertFalse(backup.calls)
        final = self.run_job(task_id=first.task_id, backup=backup, allow_paid=True)
        self.assertEqual(final.status, "completed")
        self.assertEqual(len(backup.calls), 3)
        self.assertAlmostEqual(final.cost_usd, .006)

    def test_budget_blocks_before_network_and_counts_previous_document_spend(self):
        task = self.run_job(cancel_callback=lambda: len(self.client.calls) >= 1)
        self.store.update(task.task_id, paid_eligible=1)
        backup = StubCloud(paid=True)
        result = self.run_job(task_id=task.task_id, backup=backup, allow_paid=True, document_limit=.01)
        self.assertEqual(result.reason, "budget")
        self.assertFalse(backup.calls)
        request = self.store.reserve(task.task_id, backup.model, True, .02)
        self.store.commit_response(request, "other-key", SegmentResponse({}, "length", backup.model, cost=.02), False)
        new_task = self.store.create("other-key", self.store.get(task.task_id)["doc_hash"], {})
        with self.assertRaises(BudgetExceeded):
            self.store.reserve(new_task, backup.model, True, .09, .10, 1)

    def test_truncation_reduces_later_segments_in_the_same_task(self):
        def truncated(numbers):
            if len(numbers) > 4:
                return SegmentResponse({}, "length", self.client.model, cost=0)
        self.client.handler = truncated
        result = self.run_job()
        self.assertEqual(result.status, "completed")
        self.assertEqual([len(c) for c in self.client.calls], [8, 4, 4, 4, 4, 1])
        self.assertEqual(self.store.get(result.task_id)["options"]["segment_pages"], 4)

    def test_new_run_reuses_learned_size_and_accepted_pages(self):
        cancelled = [False]
        def handler(numbers):
            if len(numbers) > 4:
                return SegmentResponse({}, "length", self.client.model, cost=0)
        def progress(message):
            if message.startswith("Saved source pages"):
                cancelled[0] = True
        self.client.handler = handler
        first = self.run_job(progress_callback=progress, cancel_callback=lambda: cancelled[0])
        self.assertEqual(first.status, "cancelled")
        self.assertEqual(first.completed_pages, [1, 2, 3, 4])
        self.client.calls.clear()
        second = self.run_job()
        self.assertEqual(second.status, "completed")
        self.assertEqual([len(pages) for pages in self.client.calls], [4, 4, 4, 1])
        self.assertTrue(all(min(pages) >= 5 for pages in self.client.calls))

    def test_single_page_truncation_and_partial_export_without_api_key(self):
        def truncate(numbers):
            if 17 in numbers:
                return SegmentResponse({}, "length", self.client.model, cost=0)
        self.client.handler = truncate
        result = self.run_job()
        self.assertEqual(result.status, "paused")
        self.assertEqual(result.reason, "page_17_length")
        self.source.unlink()
        exported = Pipeline(None, store=self.store).run("", str(self.root), task_id=result.task_id, action="export")
        self.assertIn("partial", exported.output_path)
        self.assertIn("missing source pages: 17", Path(exported.output_path).read_text())

    def test_paid_timeout_does_not_retry_or_release_reservation(self):
        task = self.run_job(cancel_callback=lambda: len(self.client.calls) >= 1)
        self.store.update(task.task_id, paid_eligible=1)
        backup = StubCloud(paid=True)
        backup.handler = lambda pages: (_ for _ in ()).throw(CloudError("timeout", "network", uncertain=True))
        paused = self.run_job(task_id=task.task_id, backup=backup, allow_paid=True)
        self.assertEqual(paused.reason, "uncertain_cost")
        self.assertEqual(len(backup.calls), 1)
        self.assertAlmostEqual(paused.cost_usd, .04)
        self.run_job(task_id=task.task_id, backup=backup, allow_paid=True)
        self.assertEqual(len(backup.calls), 1)
        row = self.store.uncertain(task.task_id)[0]
        self.store.reconcile(row["id"], .001)
        backup.handler = None
        result = self.run_job(task_id=task.task_id, backup=backup, allow_paid=True)
        self.assertEqual(result.status, "completed")
        self.assertAlmostEqual(result.cost_usd, .005)

    def test_resume_rejects_changed_prompt_and_new_job_invalidates_cache(self):
        result = self.run_job()
        self.client.prompt_hash = lambda: "prompt-v2"
        with self.assertRaises(ValueError):
            self.run_job(task_id=result.task_id)
        self.run_job()
        self.assertEqual(len(self.client.calls), 6)

    def test_recovery_and_single_running_task(self):
        task = self.store.create("a", "a", {})
        other = self.store.create("b", "b", {})
        self.store.acquire(task)
        with self.assertRaises(TaskBusy):
            self.store.acquire(other)
        request = self.store.reserve(task, "model", True, .03)
        self.store.recover_interrupted()
        self.assertEqual(self.store.get(task)["status"], "paused")
        self.assertEqual(self.store.uncertain(task)[0]["id"], request)

    def test_notion_retry_is_independent_and_success_is_not_duplicated(self):
        class Notion:
            calls = 0
            fail = True
            def create_child_page(self, parent, title, markdown):
                self.calls += 1
                self.markdown = markdown
                if self.fail:
                    raise RuntimeError("offline")
                return NotionPublishResult("id", "https://notion.so/id", "create_child")
        notion = Notion()
        parent = "01234567-89ab-cdef-0123-456789abcdef"
        result = self.run_job(notion_client=notion, notion_page_id=parent, notion_mode="create_child", include_source_pages=True)
        self.assertEqual(result.reason, "notion_failed")
        calls = len(self.client.calls)
        notion.fail = False
        pipeline = Pipeline(None, store=self.store)
        retried = pipeline.run("", "", task_id=result.task_id, action="publish", notion_client=notion,
                               notion_page_id=parent, notion_mode="create_child")
        self.assertEqual(retried.notion.page_id, "id")
        self.assertEqual(notion.markdown, Path(result.output_path).read_text())
        for target in [parent, parent.replace("-", ""), "https://www.notion.so/Lecture-"+parent.replace("-", "")+"?source=copy_link"]:
            pipeline.run("", "", task_id=result.task_id, action="publish", notion_client=notion,
                         notion_page_id=target, notion_mode="create_child")
        self.assertEqual(notion.calls, 2)
        self.assertEqual(len(self.client.calls), calls)
        # Existing installations may have signed the original page URL rather than its ID.
        task = self.store.get(result.task_id)
        old_url = "https://www.notion.so/Lecture-" + parent.replace("-", "") + "?source=copy_link"
        signature = hashlib.sha256(f"create_child:{old_url}:{task['markdown']}".encode()).hexdigest()
        self.store.update(result.task_id, options={**task["options"], "notion_page_id": old_url,
                                                   "published_signature": signature})
        pipeline.run("", "", task_id=result.task_id, action="publish", notion_client=notion,
                     notion_page_id=parent, notion_mode="create_child")
        self.assertEqual(notion.calls, 2)

    def test_region_error_does_not_trigger_quota_wait_or_paid(self):
        self.client.handler = lambda p: (_ for _ in ()).throw(CloudError("region", "region"))
        backup = StubCloud(paid=True)
        result = self.run_job(backup=backup)
        self.assertEqual(result.reason, "region")
        self.assertEqual(result.status, "paused")
        self.assertFalse(result.retry_at)
        self.assertFalse(backup.calls)

    def test_monthly_cap_and_utc_reset(self):
        task = self.store.create("a", "a", {})
        self.store.reserve(task, "model", True, .04, .10, .05)
        other = self.store.create("b", "b", {})
        with self.assertRaises(BudgetExceeded):
            self.store.reserve(other, "model", True, .02, .10, .05)
        reset = datetime.fromtimestamp(next_free_reset(self.now[0]), timezone.utc)
        self.assertEqual((reset.day, reset.hour, reset.minute), (31, 0, 5))

    def test_paid_primary_cannot_bypass_manual_approval(self):
        self.client = StubCloud(paid=True)
        with self.assertRaisesRegex(ValueError, "primary profile must be free"):
            self.run_job()
        self.assertFalse(self.client.calls)

    def test_interrupted_notion_write_is_flagged_for_manual_review(self):
        result = self.run_job()
        task = self.store.get(result.task_id)
        self.store.update(result.task_id, options={**task["options"], "publication_state": "sending"})
        self.store.recover_interrupted()
        self.assertEqual(self.store.get(result.task_id)["reason"], "notion_failed")

    def test_checkpoint_failure_rolls_back_charge_settlement(self):
        task = self.store.create("atomic", "atomic", {})
        request = self.store.reserve(task, "model", True, .03)
        with self.store._db() as db:
            db.execute("CREATE TRIGGER reject_segment BEFORE INSERT ON segments BEGIN SELECT RAISE(ABORT, 'storage failure'); END")
        response = SegmentResponse({1: "notes"}, "stop", "model", cost=.001)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.commit_response(request, "atomic", response, True)
        self.assertEqual(self.store.segments("atomic"), [])
        self.assertAlmostEqual(self.store.cost(task), .03)
        self.assertIsNone(self.store.uncertain(task)[0]["actual"])
