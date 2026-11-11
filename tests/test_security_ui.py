import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from app.core.configuration import cloud_defaults
from app.core.task_store import TaskStore
from app.core.settings_store import SettingsStore
from app.ui.main_window import MainWindow
from app.ui.settings import SettingsDialog
from app.utils.helpers import load_config, save_config
from test_credentials import MemoryCredentials


class SlowCredentials(MemoryCredentials):
    def __init__(self):
        super().__init__()
        self.delay = 0
        self.threads = []

    def get(self, account):
        self.threads.append(threading.get_ident())
        time.sleep(self.delay)
        return super().get(account)


class SecurityUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = MainWindow.CONFIG_FILE
        self.path = Path(self.temp.name) / "config.json"
        MainWindow.CONFIG_FILE = str(self.path)
        self.backend = SlowCredentials()
        self.store = SettingsStore(self.path, self.backend)
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            self.spin(lambda: widget.worker is None)
            widget.close()
            widget.deleteLater()
        QTest.qWait(10)
        MainWindow.CONFIG_FILE = self.previous
        self.temp.cleanup()

    def spin(self, condition):
        QTest.qWait(10)
        deadline = time.monotonic() + 4
        while not condition():
            if time.monotonic() > deadline:
                self.fail("Background settings operation did not finish")
            QTest.qWait(10)

    def dialog(self, config):
        dialog = SettingsDialog(config, auto_refresh=False, settings_store=self.store)
        self.widgets.append(dialog)
        dialog.show()
        return dialog

    def heartbeat(self):
        beats = []
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: beats.append(1))
        timer.start()
        self.addCleanup(timer.stop)
        return timer, beats

    def test_slow_startup_migration_is_in_worker_and_keeps_ui_responsive(self):
        save_config({**cloud_defaults(), "api_key": "legacy-dummy"}, str(self.path))
        self.backend.delay = .2
        timer, beats = self.heartbeat()
        window = MainWindow(self.backend)
        self.widgets.append(window)
        window.show()
        self.assertFalse(window.generate_button.isEnabled())
        self.spin(lambda: window.worker is None and window._credentials_ready)
        self.assertGreater(len(beats), 5)
        self.assertNotIn(threading.get_ident(), self.backend.threads)
        self.assertNotIn("api_key", load_config(str(self.path)))
        self.assertEqual(window._config["api_key"], "legacy-dummy")

    def test_failed_task_restore_does_not_disable_settings_permanently(self):
        database = self.path.parent / ".noteagent" / "tasks.sqlite3"
        database.parent.mkdir()
        database.write_bytes(b"invalid database fixture")
        window = MainWindow(self.backend)
        self.widgets.append(window)
        window.show()
        self.spin(lambda: window.worker is None and window._credentials_ready)
        self.assertTrue(window.settings_button.isEnabled())
        self.assertIn("database", window.status.text())

    def test_second_window_never_migrates_or_writes_the_profile(self):
        first = MainWindow(self.backend)
        self.widgets.append(first)
        self.spin(lambda: first.worker is None and first._credentials_ready)
        save_config({**cloud_defaults(), "api_key": "legacy-dummy"}, str(self.path))
        before = self.path.read_bytes()
        self.backend.calls.clear()
        second = MainWindow(self.backend)
        self.widgets.append(second)
        self.assertFalse(second._owns_lock)
        second.close()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.backend.calls, [])

    def test_close_during_startup_waits_without_blocking_the_event_loop(self):
        save_config({**cloud_defaults(), "api_key": "legacy-dummy"}, str(self.path))
        self.backend.delay = .2
        window = MainWindow(self.backend)
        self.widgets.append(window)
        window.show()
        self.spin(lambda: bool(self.backend.threads))
        timer, beats = self.heartbeat()
        with patch("app.ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            window.close()
        self.assertFalse(window._closed)
        self.spin(lambda: window._closed)
        self.assertGreater(len(beats), 3)
        self.assertNotIn("api_key", load_config(str(self.path)))

    def test_save_is_async_and_repeated_clicks_do_not_repeat_keychain_writes(self):
        dialog = self.dialog(cloud_defaults())
        dialog.fields["api_key"].setText("new-dummy")
        self.backend.delay = .2
        timer, beats = self.heartbeat()
        dialog.accept()
        dialog.accept()
        self.assertIsNotNone(dialog.worker)
        self.assertFalse(dialog.tabs.isEnabled())
        self.spin(lambda: dialog.worker is None)
        self.assertGreater(len(beats), 5)
        self.assertEqual(sum(call[0] == "set" for call in self.backend.calls), 1)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertNotIn("api_key", load_config(str(self.path)))

    def test_failed_save_keeps_dialog_and_input_without_reporting_saved(self):
        dialog = self.dialog(cloud_defaults())
        dialog.fields["api_key"].setText("new-dummy")
        self.backend.denied = True
        dialog.accept()
        self.spin(lambda: dialog.worker is None)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.fields["api_key"].text(), "new-dummy")
        self.assertIsNone(dialog.saved_result)
        self.assertIn("Original settings were retained", dialog.test_status.text())
        self.assertFalse(self.path.exists())

    def test_connection_test_retains_unavailable_state_until_key_is_entered(self):
        config = {**cloud_defaults(), "_unavailable_credentials": ("api_key",)}
        dialog = self.dialog(config)
        self.assertEqual(dialog.values()["_unavailable_credentials"], ("api_key",))
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "other-account-dummy"}):
            dialog._test_model_connection()
            self.spin(lambda: dialog.worker is None)
        self.assertIn("saved API key", dialog.test_status.text())
        QTest.keyClicks(dialog.fields["api_key"], "new-dummy")
        self.assertEqual(dialog.values()["_unavailable_credentials"], ())

    def test_unavailable_notion_token_does_not_switch_to_environment_token(self):
        window = MainWindow(self.backend)
        self.widgets.append(window)
        self.spin(lambda: window.worker is None and window._credentials_ready)
        store = TaskStore(window.store_path)
        task = store.create("security-fixture", "fixture", {"title": "Fixture", "input_path": "missing.pdf"})
        store.update(task, status="completed", page_count=1, markdown="# Saved notes")
        config = {**cloud_defaults(), "_unavailable_credentials": ("notion_token",)}
        operation = window._job_operation(config, {"input_path": "missing.pdf", "output_dir": self.temp.name,
            "task_id": task, "action": "publish", "notion_page_id": "0123456789abcdef0123456789abcdef",
            "notion_mode": "append"})
        with patch.dict(os.environ, {"NOTION_TOKEN": "other-account-dummy"}), \
             patch("app.ui.main_window.NotionClient") as notion, \
             patch("app.ui.main_window.make_client") as model:
            window._launch(operation, window._on_result)
            self.spin(lambda: window.worker is None)
        notion.assert_not_called()
        model.assert_not_called()
        self.assertEqual(store.get(task)["reason"], "notion_setup")
        self.assertEqual(store.get(task)["markdown"], "# Saved notes")

    def test_close_during_save_waits_for_atomic_commit_without_losing_result(self):
        dialog = self.dialog(cloud_defaults())
        dialog.fields["api_key"].setText("new-dummy")
        self.backend.delay = .2
        dialog.accept()
        dialog.close()
        self.assertTrue(dialog.isVisible())
        self.spin(lambda: dialog.worker is None)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.saved_result.config["api_key"], "new-dummy")

    def test_changing_custom_host_clears_key_but_changing_path_does_not(self):
        config = {**cloud_defaults(), "provider": "custom", "base_url": "https://first.example/v1", "api_key": "old-dummy"}
        dialog = self.dialog(config)
        dialog.fields["base_url"].setText("https://first.example/v2")
        dialog.fields["base_url"].editingFinished.emit()
        self.assertEqual(dialog.fields["api_key"].text(), "old-dummy")
        dialog.fields["base_url"].setText("https://second.example/v1")
        dialog.fields["base_url"].editingFinished.emit()
        self.assertEqual(dialog.fields["api_key"].text(), "")
        self.assertIn("address changed", dialog.test_status.text())

    def test_explicitly_reentered_same_key_can_be_saved_for_new_host(self):
        config = {**cloud_defaults(), "provider": "custom", "base_url": "https://first.example/v1", "api_key": "old-dummy"}
        config = self.store.save(config).config
        dialog = self.dialog(config)
        dialog.fields["base_url"].setText("https://second.example/v1")
        dialog.fields["base_url"].editingFinished.emit()
        QTest.keyClicks(dialog.fields["api_key"], "old-dummy")
        dialog.accept()
        self.spin(lambda: dialog.worker is None)
        self.assertEqual(dialog.saved_result.config["api_key"], "old-dummy")
        self.assertEqual(self.store.load().config["api_key"], "old-dummy")
