import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWidgets import QMessageBox

from app.core.configuration import cloud_defaults, make_model_config, migrate_config
from app.core.pipeline import PipelineResult
from app.core.preflight import DocumentSummary, build_estimate
from app.core.cloud import AccountQuota, CatalogModel
from app.core.task_store import TaskStore
from app.core.notion import NotionPublishResult
from app.ui.main_window import MainWindow
from app.ui.branding import application_icon, menu_bar_icon
from app.ui.settings import SettingsDialog


class ResponsiveUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = MainWindow.CONFIG_FILE
        MainWindow.CONFIG_FILE = str(Path(self.temp.name) / "config.json")
        self.window = MainWindow()
        self.window.show()
        self.spin(lambda: self.window.worker is None)

    def tearDown(self):
        if self.window.worker:
            self.window.worker.requestInterruption()
            self.spin(lambda: self.window.worker is None)
        self.window.close()
        self.spin(lambda: self.window._closed)
        self.window.deleteLater()
        QTest.qWait(10)
        MainWindow.CONFIG_FILE = self.previous
        self.temp.cleanup()

    def spin(self, condition, timeout=3000):
        # Process the singleShot startup event before evaluating the predicate.
        QTest.qWait(10)
        deadline = time.monotonic() + timeout / 1000
        while not condition():
            if time.monotonic() > deadline:
                self.fail("UI operation failed to finish in time")
            QTest.qWait(10)

    def show_saved_task(self, status="completed", reason="", retry_at=None):
        source = Path(self.temp.name) / "Lecture.pdf"
        source.write_bytes(b"placeholder")
        output = Path(self.temp.name) / "Lecture_notes.md"
        output.write_text("# Lecture\n\nSaved notes", encoding="utf-8")
        task = {"id": "saved-task", "status": status, "reason": reason, "page_count": 2,
                "notes": {1: "Saved notes", 2: "More notes"}, "output_path": str(output),
                "markdown": "# Lecture\n\nSaved notes", "cost": 0, "retry_at": retry_at,
                "paid_eligible": False,
                "options": {"input_path": str(source), "output_dir": self.temp.name,
                            "output_format": "md", "language": self.window.language_combo.currentData(),
                            "title": "Lecture", "primary_profile": make_model_config(self.window._config).fingerprint()}}
        with patch.object(self.window, "_schedule_estimate"):
            self.window._restore_previous(task)
        return task

    def test_completed_primary_opens_existing_notes_without_generation_or_publishing(self):
        task = self.show_saved_task()
        self.window.publish_notion.setChecked(True)
        self.assertEqual(self.window.generate_button.text(), "Open notes")
        self.assertEqual(self.window.export_button.text(), "Export again")
        self.assertTrue(self.window.open_file_button.isHidden())
        with patch("app.ui.main_window.QDesktopServices.openUrl", return_value=True) as opened, \
             patch.object(self.window, "_job_operation") as operation, \
             patch.object(self.window, "_launch") as launch:
            self.window.generate_button.click()
            self.window.generate_button.click()
        self.assertEqual(opened.call_count, 2)
        self.assertEqual(opened.call_args.args[0].toLocalFile(), task["output_path"])
        self.assertEqual(self.window.task_id, task["id"])
        operation.assert_not_called()
        launch.assert_not_called()

    def test_export_and_notion_are_separate_actions_on_the_saved_task(self):
        task = self.show_saved_task()
        self.window.publish_notion.setChecked(True)
        self.window.format_combo.setCurrentIndex(self.window.format_combo.findData("ipynb"))
        self.assertEqual(self.window.generate_button.text(), "Open notes")
        self.assertFalse(self.window.retry_notion_button.isHidden())
        with patch.object(self.window, "_job_operation", return_value=object()) as operation, \
             patch.object(self.window, "_launch"), \
             patch("app.ui.main_window.QMessageBox.question") as confirmation:
            self.window.export_button.click()
            arguments = operation.call_args.args[1]
            self.assertEqual(arguments["task_id"], task["id"])
            self.assertEqual(arguments["action"], "export")
            self.assertEqual(arguments["output_format"], "ipynb")
            self.window.retry_notion_button.click()
            arguments = operation.call_args.args[1]
            self.assertEqual(arguments["task_id"], task["id"])
            self.assertEqual(arguments["action"], "publish")
            confirmation.assert_not_called()

    def test_missing_output_primary_exports_cache_without_restarting_generation(self):
        task = self.show_saved_task()
        Path(task["output_path"]).unlink()
        self.window._render_state()
        self.assertEqual(self.window.generate_button.text(), "Export notes")
        self.assertTrue(self.window.export_button.isHidden())
        self.assertTrue(self.window.open_file_button.isHidden())
        with patch.object(self.window, "_saved_action") as saved, \
             patch.object(self.window, "_start_generation") as generate:
            self.window.generate_button.click()
        saved.assert_called_once_with("export")
        generate.assert_not_called()

    def test_uncertain_notion_retry_still_requires_checking_the_previous_write(self):
        self.show_saved_task(reason="notion_failed")
        self.window.publish_notion.setChecked(True)
        self.assertEqual(self.window.retry_notion_button.text(), "Retry Notion")
        with patch("app.ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.No), \
             patch.object(self.window, "_saved_action") as saved:
            self.window.retry_notion_button.click()
        saved.assert_not_called()

    def test_completed_notes_open_after_the_original_document_is_deleted(self):
        task = self.show_saved_task()
        Path(task["options"]["input_path"]).unlink()
        self.window.input_file = None
        with patch.object(self.window, "_schedule_estimate"):
            self.window._restore_previous(task)
        self.assertEqual(self.window.generate_button.text(), "Open notes")
        with patch("app.ui.main_window.QDesktopServices.openUrl", return_value=True) as opened:
            self.window.generate_button.click()
        opened.assert_called_once()

    def test_generation_context_changes_create_new_notes_but_export_and_budget_options_do_not(self):
        self.show_saved_task()
        self.window._config.update(paid_enabled=True, paid_model="test/backup", api_key="rotated-key",
                                   document_limit=.2, free_providers="other-host")
        self.window.include_source_pages.setChecked(True)
        self.window._render_state()
        self.assertEqual(self.window.generate_button.text(), "Open notes")
        language = self.window.language_combo.currentIndex()
        with patch.object(self.window, "_schedule_estimate"):
            self.window.language_combo.setCurrentIndex(1 - language)
            self.assertEqual(self.window.generate_button.text(), "Create notes")
            with patch.object(self.window, "_start_generation") as generate:
                self.window.generate_button.click()
            generate.assert_called_once_with()
            self.window.language_combo.setCurrentIndex(language)
        self.assertEqual(self.window.generate_button.text(), "Open notes")
        self.window._config["vision_model"] = "test/different-model"
        self.window._render_state()
        self.assertEqual(self.window.generate_button.text(), "Create notes")

    def test_restored_model_profile_is_kept_for_primary_action(self):
        task = self.show_saved_task()
        task["options"]["primary_profile"]["model"] = "test/previous-model"
        with patch.object(self.window, "_schedule_estimate"):
            self.window._restore_previous(task)
        self.assertEqual(self.window.generate_button.text(), "Create notes")

    def test_resume_uses_same_task_and_double_click_does_not_launch_twice(self):
        task = self.show_saved_task(status="cancelled", reason="cancelled")
        self.assertEqual(self.window.generate_button.text(), "Resume")
        result = self.window.last_result
        def operation(config, arguments):
            def run(worker):
                time.sleep(.1)
                return result, task["markdown"]
            return run
        with patch.object(self.window, "_job_operation", side_effect=operation) as job:
            self.window.generate_button.click()
            self.window.generate_button.click()
            self.assertEqual(self.window.generate_button.text(), "Working…")
            self.assertFalse(self.window.generate_button.isEnabled())
            self.spin(lambda: self.window.worker is None)
        job.assert_called_once()
        self.assertEqual(job.call_args.args[1]["task_id"], task["id"])
        self.assertFalse(job.call_args.args[1]["allow_paid"])
        self.assertEqual(self.window.generate_button.text(), "Resume")

    def test_quota_wait_and_uncertain_charge_show_the_next_action(self):
        self.show_saved_task(status="waiting_quota", reason="daily_quota", retry_at=time.time() + 3600)
        self.assertEqual(self.window.generate_button.text(), "Waiting for free quota")
        self.assertFalse(self.window.generate_button.isEnabled())
        self.window.last_result.retry_at = time.time() - 10
        with patch.object(self.window, "_start_generation") as generate:
            self.window._check_due()
            generate.assert_called_once_with(resume=True)
        with patch.object(self.window, "_schedule_estimate"), \
             patch.object(self.window, "_start_generation") as generate:
            self.window.language_combo.setCurrentIndex(1 - self.window.language_combo.currentIndex())
            self.window._check_due()
            generate.assert_not_called()
        self.show_saved_task(status="paused", reason="uncertain_cost")
        self.assertEqual(self.window.generate_button.text(), "Reconcile charge")
        with patch.object(self.window, "_reconcile") as reconcile:
            self.window.generate_button.click()
        reconcile.assert_called_once_with()

    def test_delayed_worker_keeps_event_loop_alive_and_blocks_duplicate_launch(self):
        ticks = []
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start()
        completed = []
        def slow(worker):
            time.sleep(.25)
            return "done"
        self.assertTrue(self.window._launch(slow, completed.append))
        self.assertTrue(self.window.activity.indicator.active)
        self.assertFalse(self.window._launch(slow, completed.append))
        self.assertFalse(self.window.generate_button.isEnabled())
        self.spin(lambda: self.window.worker is None)
        timer.stop()
        self.assertGreater(len(ticks), 10)
        self.assertEqual(completed, ["done"])
        self.assertTrue(self.window.generate_button.isEnabled())
        self.assertFalse(self.window.activity.indicator.active)

    def test_reconciliation_refreshes_main_action_after_the_final_charge(self):
        task = self.show_saved_task(status='paused', reason='uncertain_cost')
        store = TaskStore(self.window.store_path)
        task_id = store.create('fixture', 'document', task['options'])
        request = store.reserve(task_id, 'test/paid', True, .04)
        store.fail_request(request, uncertain=True)
        store.update(task_id, status='paused', reason='uncertain_cost', page_count=2)
        task.update(id=task_id)
        with patch.object(self.window, '_schedule_estimate'):
            self.window._restore_previous(task)
        with patch('app.ui.main_window.QInputDialog.getDouble', return_value=(.001,True)):
            self.window.generate_button.click()
            self.spin(lambda:self.window.worker is None and not store.uncertain(task_id))
        self.assertEqual(self.window.generate_button.text(), 'Resume')
        self.assertEqual(self.window.last_result.reason, '')
        self.assertAlmostEqual(self.window.last_result.cost_usd, .001)

    def test_saved_revision_restores_and_exports_without_a_model_or_notion_write(self):
        task = self.show_saved_task()
        store = TaskStore(self.window.store_path)
        task_id = store.create('fixture', 'document', task['options'])
        store.update(task_id, status='completed', page_count=2, markdown=task['markdown'], output_path=task['output_path'])
        markdown = '# Revised notes\n\nA manually corrected explanation.'
        revision_id = store.save_revision(task_id, markdown)
        task.update(id=task_id, revision=store.latest_revision(task_id))
        with patch.object(self.window, '_schedule_estimate'):
            self.window._restore_previous(task)
        self.assertEqual(self.window.preview.toPlainText(), markdown)
        self.assertEqual(self.window.rendered_preview.toPlainText().splitlines()[0], 'Revised notes')
        self.assertEqual(self.window.generate_button.text(), 'Export revision')
        Path(task['options']['input_path']).unlink()
        self.window.publish_notion.setChecked(True)
        with patch('app.ui.main_window.make_client') as model, \
             patch('app.ui.main_window.NotionClient.append_to_page') as notion:
            self.window.generate_button.click()
            self.spin(lambda:self.window.worker is None)
            model.assert_not_called()
            notion.assert_not_called()
        self.assertEqual(Path(self.window.last_result.output_path).read_text(), markdown)
        self.assertEqual(self.window.last_result.exported_revision_id, revision_id)
        self.assertEqual(self.window.generate_button.text(), 'Open notes')
        self.window.input_file = self.window.last_result = None
        self.window._load_previous()
        self.spin(lambda:self.window.worker is None)
        self.assertEqual(self.window.preview.toPlainText(), markdown)
        self.assertEqual(self.window.last_result.revision_id, revision_id)
        self.assertEqual(self.window.generate_button.text(), 'Open notes')

    def test_publishing_a_revision_requires_an_explicit_new_copy_confirmation(self):
        self.show_saved_task()
        self.window.last_result.notion = NotionPublishResult('page', 'https://notion.so/page', 'append')
        self.window.last_result.revision_id = 7
        self.window.publish_notion.setChecked(True)
        self.assertEqual(self.window.retry_notion_button.text(), 'Send revision')
        with patch.object(self.window, '_saved_action') as saved:
            with patch('app.ui.main_window.QMessageBox.question', return_value=QMessageBox.StandardButton.No):
                self.window.retry_notion_button.click()
            saved.assert_not_called()
            with patch('app.ui.main_window.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
                self.window.retry_notion_button.click()
            saved.assert_called_once_with('publish')

    def test_rendered_preview_starts_at_top_and_does_not_pull_readers_to_the_end(self):
        self.window._set_preview_markdown('\n\n'.join(f'## Topic {number}\n\nA paragraph.' for number in range(100)))
        QTest.qWait(20)
        scrollbar = self.window.rendered_preview.verticalScrollBar()
        self.assertEqual(scrollbar.value(), 0)
        self.assertGreater(scrollbar.maximum(), 0)
        self.window._on_stream('\n\n## Next topic\n\nNew paragraph.')
        self.window._flush_preview()
        self.assertEqual(scrollbar.value(), 0)
        scrollbar.setValue(scrollbar.maximum())
        self.window._on_stream('\n\n## Last topic\n\nAnother paragraph.')
        self.window._flush_preview()
        self.assertEqual(scrollbar.value(), scrollbar.maximum())

    def test_activity_expands_without_blocking_and_preserves_plain_text(self):
        activity = self.window.activity
        activity.add_event("Reading <source> pages 1–8…")
        activity.add_event("Reading <source> pages 1–8…")
        self.assertEqual(activity.event_count,1)
        self.assertIn("<source>",activity.log.toPlainText())
        self.assertEqual(activity.body.maximumHeight(),0)
        activity.toggle_button.setChecked(True)
        QTest.qWait(70)
        self.assertGreater(activity.body.maximumHeight(),0)
        self.assertLess(activity.body.maximumHeight(),127)
        activity.toggle_button.setChecked(False)
        QTest.qWait(200)
        self.assertEqual(activity.body.maximumHeight(),0)
        self.window.close()
        self.assertFalse(activity.indicator.timer.isActive())

    def test_refresh_retains_snapshot_and_updates_the_selected_backup(self):
        source = Path(self.temp.name)/"estimate.pdf"
        source.write_bytes(b"placeholder")
        models = [CatalogModel("test/small", "Small", .1, .4, 10000, False),
                  CatalogModel("test/large", "Large", .5, 2, 10000, False)]
        self.window._config.update(paid_enabled=True,paid_model="test/small")
        calls = []
        def estimate(path,config,store,cancel,refresh):
            calls.append((config['paid_model'],refresh))
            time.sleep(.08)
            document = DocumentSummary(path,"source",(100,)*10,(1024,)*10)
            return build_estimate(document,config,models,AccountQuota(20,50,None,time.time()))
        with patch.object(self.window._preflight,"estimate",side_effect=estimate):
            self.window._set_input_file(str(source))
            self.window._estimate_timer.stop();self.window._begin_estimate()
            self.spin(lambda:self.window._estimate_worker is None)
            self.assertIn("small",self.window.backup_metric.detail.text())
            self.window._schedule_estimate(refresh=True)
            self.assertEqual(self.window.document_metric.value.text(),"10 pages")
            self.assertFalse(self.window.refresh_allowance.isEnabled())
            self.window._estimate_timer.stop();self.window._begin_estimate()
            self.assertTrue(self.window.estimate_spinner.active)
            self.spin(lambda:self.window._estimate_worker is None)
            self.window._config['paid_model']="test/large"
            self.window._schedule_estimate(refresh=True)
            self.assertIn("large",self.window.backup_metric.detail.text())
            self.window._estimate_timer.stop();self.window._begin_estimate()
            self.spin(lambda:self.window._estimate_worker is None)
        self.assertEqual(calls,[("test/small",False),("test/small",True),("test/large",True)])
        self.assertIn("large",self.window.backup_metric.detail.text())
        self.assertEqual(self.window.allowance_metric.value.text(),"20 requests")
        self.assertTrue(self.window.refresh_allowance.isEnabled())
        self.assertFalse(self.window.estimate_spinner.active)

    def test_language_change_does_not_reuse_old_task_page_counts(self):
        self.window.input_file="lecture.pdf"
        self.window._active_context=self.window._document_context()
        self.window.language_combo.setCurrentIndex(self.window.language_combo.findData("en"))
        self.window._estimate_timer.stop();self.window._estimate_pending=False
        document=DocumentSummary("lecture.pdf","source",(100,)*10,(1024,)*10)
        self.window._estimate=build_estimate(document,{**self.window._config,"language":"en"})
        self.window._estimate_context=self.window._document_context()
        self.window.task_id="old-chinese-task"
        self.window.last_result=PipelineResult("", "Lecture",10,0,1,task_id=self.window.task_id,completed_pages=list(range(1,11)))
        self.window._render_estimate()
        self.assertIn("10 remaining",self.window.document_metric.detail.text())
        self.assertEqual(self.window._arguments()['language'],"en")
        self.assertEqual(self.window.backup_metric.value.text(),"Off")
        self.assertNotIn("qwen",self.window.backup_metric.detail.text())

    def test_completed_task_does_not_override_current_english_preference(self):
        self.window.language_combo.setCurrentIndex(self.window.language_combo.findData("en"))
        task = {"id":"old","status":"completed","reason":"","page_count":1,"notes":{},
                "output_path":"","markdown":"","cost":0,"retry_at":None,"paid_eligible":False,
                "options":{"input_path":"missing.pdf","output_dir":self.temp.name,"output_format":"md",
                           "language":"zh","title":"Old Chinese notes"}}
        with patch.object(self.window,"_schedule_estimate"):
            self.window._restore_previous(task)
        self.assertEqual(self.window.language_combo.currentData(),"en")
        self.assertEqual(self.window._arguments()['language'],"en")

    def test_source_page_option_updates_preview_and_persists_without_work(self):
        self.assertFalse(self.window.include_source_pages.isChecked())
        content = "## Topic\n\n$x=2$\n\n> Source: page 1\n"
        self.window._on_stream(content)
        self.window._flush_preview()
        self.assertNotIn("> Source: page",self.window.preview.toPlainText())
        with patch.object(self.window,"_schedule_estimate") as estimate, \
             patch.object(self.window,"_launch") as launch:
            self.window.include_source_pages.setChecked(True)
            self.assertIn("> Source: page 1",self.window.preview.toPlainText())
            self.assertTrue(self.window._arguments()['include_source_pages'])
            saved = json.loads(Path(self.window.CONFIG_FILE).read_text())
            self.assertTrue(saved['include_source_pages'])
            self.window.include_source_pages.setChecked(False)
            self.assertNotIn("> Source: page",self.window.preview.toPlainText())
            self.assertIn("$x=2$",self.window.preview.toPlainText())
            estimate.assert_not_called()
            launch.assert_not_called()

    def test_dock_icon_has_clear_rounded_corners_and_menu_icon_is_a_template(self):
        icon = application_icon().pixmap(64, 64).toImage()
        self.assertEqual(icon.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(icon.pixelColor(5, 5).alpha(), 0)
        self.assertEqual(icon.pixelColor(32, 8).alpha(), 255)
        self.assertTrue(menu_bar_icon().isMask())
        self.assertFalse(menu_bar_icon().pixmap(34, 22).isNull())
        sizes = menu_bar_icon().availableSizes()
        self.assertEqual(len(sizes), 2)
        self.assertTrue(all(0 < size.width() <= 48 and 0 < size.height() <= 32 for size in sizes))

    def test_menu_bar_restores_window_and_uses_cooperative_quit(self):
        with patch("app.ui.main_window.sys.platform", "darwin"), \
             patch("app.ui.main_window.QApplication.platformName", return_value="cocoa"), \
             patch("app.ui.main_window.QSystemTrayIcon.show"):
            self.window._setup_menu_bar()
        self.assertTrue(self.window.tray_icon.icon().isMask())
        menu = self.window.tray_icon.contextMenu()
        open_action, settings_action, _, quit_action = menu.actions()
        self.window.showMinimized()
        open_action.trigger()
        self.assertFalse(self.window.isMinimized())
        with patch.object(self.window, "_show_settings") as show_settings:
            settings_action.trigger()
            show_settings.assert_called_once()

        def operation(worker):
            while not worker.isInterruptionRequested():
                time.sleep(.01)
        self.window._launch(operation, lambda _: None)
        menu.aboutToShow.emit()
        self.assertFalse(settings_action.isEnabled())
        with patch("app.ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            quit_action.trigger()
        self.assertFalse(self.window._closed)
        self.assertFalse(self.window.worker.isInterruptionRequested())
        with patch("app.ui.main_window.QApplication.activeModalWidget", return_value=object()):
            menu.aboutToShow.emit()
        self.assertFalse(quit_action.isEnabled())

    def test_menu_bar_survives_initial_unavailability_and_is_not_duplicated(self):
        with patch("app.ui.main_window.sys.platform", "darwin"), \
             patch("app.ui.main_window.QApplication.platformName", return_value="cocoa"), \
             patch("app.ui.main_window.QSystemTrayIcon.isSystemTrayAvailable", return_value=False), \
             patch("app.ui.main_window.QSystemTrayIcon.show") as show:
            self.window._setup_menu_bar()
            self.assertIsNotNone(self.window.tray_icon)
            first = self.window.tray_icon
            self.window._setup_menu_bar()
            self.assertIs(self.window.tray_icon, first)
            show.assert_called_once()

    def test_deferred_menu_bar_setup_does_not_run_after_close(self):
        self.window.close()
        with patch("app.ui.main_window.sys.platform", "darwin"), \
             patch("app.ui.main_window.QApplication.platformName", return_value="cocoa"), \
             patch("app.ui.main_window.QSystemTrayIcon.show") as show:
            self.window._setup_menu_bar()
            self.assertIsNone(self.window.tray_icon)
            show.assert_not_called()

    def test_connection_test_runs_in_background_and_closes_cleanly(self):
        dialog = SettingsDialog(cloud_defaults(), self.window, auto_refresh=False)
        def probe(cancel):
            for _ in range(20):
                if cancel():
                    return "Cancelled"
                time.sleep(.01)
            return "Image request passed"
        with patch("app.ui.settings.make_client", return_value=SimpleNamespace(test_connection=probe)):
            dialog.show()
            dialog._test_model_connection()
            self.assertTrue(dialog.worker.isRunning())
            self.assertFalse(dialog.test_connection.isEnabled())
            QTest.qWait(30)
            dialog.reject()
            self.assertTrue(dialog._closing)
            self.spin(lambda: dialog.worker is None)
            self.spin(lambda: not dialog.isVisible())

    def test_storage_check_and_cleanup_run_in_background_and_close_safely(self):
        dialog = SettingsDialog(cloud_defaults(), self.window, auto_refresh=False)
        info = {'cache_bytes':2048,'reclaimable_bytes':1024,'retention_days':30}
        cleaned = {**info,'cache_bytes':1024,'reclaimable_bytes':0,'removed_bytes':1024,'removed_workspaces':0}
        changed = []
        dialog.cache_changed.connect(lambda:changed.append(True))
        def delayed_info(*args):
            time.sleep(.1)
            return info
        def delayed_clean(path,task,cancel):
            for _ in range(10):
                if cancel():
                    break
                time.sleep(.01)
            return cleaned
        with patch('app.ui.settings.storage_info',side_effect=delayed_info), \
             patch('app.ui.settings.clean_storage',side_effect=delayed_clean):
            dialog.show()
            dialog.tabs.setCurrentWidget(dialog.storage_tab)
            self.assertIsNotNone(dialog.worker)
            self.assertFalse(dialog.clean_cache_button.isEnabled())
            before=time.monotonic();QTest.qWait(25)
            self.assertLess(time.monotonic()-before,.2)
            self.spin(lambda:dialog.worker is None)
            self.assertIn('2.0 KB',dialog.cache_summary.text())
            dialog.clean_cache_button.click()
            self.spin(lambda:dialog.worker is None)
            self.assertEqual(changed,[True])
            self.assertIn('1.0 KB',dialog.cache_summary.text())
            dialog.clean_cache_button.click()
            dialog.reject()
            self.assertTrue(dialog._closing)
            self.spin(lambda:dialog.worker is None)
            self.spin(lambda:not dialog.isVisible())

    def test_preview_batches_and_preserves_user_scroll(self):
        self.window.preview.setPlainText("\n".join(f"line {n}" for n in range(500)))
        QTest.qWait(20)
        scrollbar = self.window.preview.verticalScrollBar()
        scrollbar.setValue(0)
        old = self.window.preview.toPlainText()
        self.window._on_stream("\nnew note")
        self.assertEqual(self.window.preview.toPlainText(), old)
        self.window._flush_preview()
        self.assertTrue(self.window.preview.toPlainText().endswith("new note"))
        self.assertEqual(scrollbar.value(), 0)

    def test_cancellation_is_immediate_and_does_not_force_kill_thread(self):
        def cancellable(worker):
            while not worker.isInterruptionRequested():
                time.sleep(.01)
            return None
        self.window._launch(cancellable, lambda _: None)
        self.window._cancel_generation()
        self.assertIn("Stopping", self.window.status.text())
        self.assertFalse(self.window.cancel_button.isEnabled())
        self.spin(lambda: self.window.worker is None)

    def test_previous_config_is_preserved_without_redirecting_key(self):
        saved = {"provider": "groq", "base_url": "https://api.groq.com/openai/v1",
                 "api_key": "old-key", "vision_model": "old-model", "notion_token": "keep"}
        migrated = migrate_config(saved)
        self.assertEqual(migrated["provider"], "custom")
        for key in ("base_url", "api_key", "vision_model", "notion_token"):
            self.assertEqual(migrated[key], saved[key])
        self.assertFalse(migrated["paid_enabled"])

    def test_close_waits_asynchronously_for_worker(self):
        def operation(worker):
            while not worker.isInterruptionRequested():
                time.sleep(.01)
            return None
        self.window._launch(operation, lambda _: None)
        with patch("app.ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.window.close()
        self.assertTrue(self.window._close_after_worker)
        self.assertFalse(self.window._closed)
        self.spin(lambda: self.window._closed)
        self.assertIsNone(self.window.worker)

    def test_restart_restores_notion_link_and_original_output_options(self):
        store = TaskStore(self.window.store_path)
        source = Path(self.temp.name) / "Lecture.pdf"
        source.write_bytes(b"placeholder")
        options = {"input_path": str(source), "output_dir": self.temp.name,
                   "output_format": "ipynb", "language": "en", "title": "Lecture",
                   "notion_result": {"page_id": "id", "url": "https://notion.so/id", "mode": "create_child"}}
        task = store.create("restore", "source", options)
        store.update(task, status="completed", page_count=1, output_path=str(Path(self.temp.name) / "notes.ipynb"))
        self.window._load_previous()
        self.spin(lambda: self.window.worker is None)
        self.assertEqual(self.window.format_combo.currentData(), "ipynb")
        self.assertEqual(self.window.language_combo.currentData(), "en")
        self.assertEqual(self.window.last_result.notion.url, "https://notion.so/id")
        self.assertFalse(self.window.open_notion_button.isHidden())
