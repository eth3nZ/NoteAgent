import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


class UISmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_constructs_with_empty_config(self):
        with tempfile.TemporaryDirectory() as directory:
            old_path = MainWindow.CONFIG_FILE
            MainWindow.CONFIG_FILE = str(Path(directory) / "config.json")
            try:
                window = MainWindow()
                self.assertEqual(window.windowTitle(), "NoteAgent")
                self.assertEqual(window.format_combo.count(), 2)
                self.assertFalse(window.publish_notion.isChecked())
                window.close()
            finally:
                MainWindow.CONFIG_FILE = old_path


if __name__ == "__main__":
    unittest.main()
