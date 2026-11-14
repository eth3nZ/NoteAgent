import hashlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pymupdf
from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from app.core.source_review import SourceRenderer
from app.core.cloud import SegmentResponse
from app.core.markdown_text import NON_CONTENT_PAGE
from app.core.task_store import TaskStore
from app.ui.review import MarkdownBrowser, RevisionDialog, SourceDialog


class ReviewDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/'source.pdf'
        with pymupdf.open() as document:
            for page in range(1,3):
                document.new_page().insert_text((40,60), f'Original source page {page}')
            document.save(self.source)
        self.store = TaskStore(self.root/'tasks.sqlite3')
        self.task = self.store.create('fixture', hashlib.sha256(self.source.read_bytes()).hexdigest(), {'input_path':str(self.source)})
        self.store.update(self.task, status='completed', page_count=2, markdown='# Notes\n\nOriginal.')
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            if dialog.isVisible():
                with patch('app.ui.review.QMessageBox.question', return_value=QMessageBox.StandardButton.Discard):
                    dialog.reject()
            self.spin(lambda:not dialog.isVisible() and dialog.worker is None)
            dialog.deleteLater()
            QTest.qWait(10)
        self.temp.cleanup()

    def spin(self, condition, timeout=3000):
        deadline = time.monotonic()+timeout/1000
        QTest.qWait(10)
        while not condition():
            if time.monotonic() > deadline:
                self.fail('Review operation did not finish')
            QTest.qWait(10)

    def revision_dialog(self):
        dialog = RevisionDialog(self.store.path, self.task, '# Notes\n\nOriginal.')
        self.dialogs.append(dialog)
        dialog.show()
        return dialog

    def test_revision_save_is_background_and_empty_failure_keeps_the_draft(self):
        dialog = self.revision_dialog()
        dialog.editor.setPlainText(' ')
        dialog.save()
        self.spin(lambda:dialog.worker is None)
        self.assertTrue(dialog.isVisible())
        self.assertIn('cannot be empty', dialog.status.text())
        self.assertEqual(dialog.editor.toPlainText(), ' ')
        revised = '# Notes\n\nA human correction.\n\n```python\nvalue = (\n42\n)\n```'
        dialog.editor.setPlainText(revised)
        original = TaskStore.save_revision
        def delayed(store, task, markdown):
            time.sleep(.15)
            return original(store, task, markdown)
        ticks = []
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda:ticks.append(1))
        timer.start()
        with patch.object(TaskStore, 'save_revision', delayed):
            dialog.save()
            dialog.save()
            self.spin(lambda:dialog.worker is None)
        timer.stop()
        self.assertGreater(len(ticks), 5)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(self.store.latest_revision(self.task)['markdown'], revised)
        with self.store._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0], 1)

    def test_dirty_revision_close_can_be_cancelled_or_discarded(self):
        dialog = self.revision_dialog()
        dialog.editor.setPlainText('An unsaved correction')
        with patch('app.ui.review.QMessageBox.question', return_value=QMessageBox.StandardButton.Cancel):
            dialog.reject()
        self.assertTrue(dialog.isVisible())
        with patch('app.ui.review.QMessageBox.question', return_value=QMessageBox.StandardButton.Discard):
            dialog.reject()
        self.assertFalse(dialog.isVisible())
        self.assertIsNone(self.store.latest_revision(self.task))

    def test_native_preview_never_implicitly_loads_linked_files_or_images(self):
        browser = MarkdownBrowser()
        browser.setMarkdown('# A heading\n\n**Bold**\n\n![image](https://example.com/tracker.png)')
        self.assertIn('A heading', browser.toPlainText())
        self.assertNotIn('# A heading', browser.toPlainText())
        self.assertIsNone(browser.loadResource(2, QUrl('file:///private/secret.png')))
        with patch('app.ui.review.QDesktopServices.openUrl') as opened:
            browser._open_link(QUrl('file:///private/secret.txt'))
            opened.assert_not_called()
        browser.deleteLater()

    def test_non_content_receipt_is_explained_without_exposing_the_internal_marker(self):
        request = self.store.reserve(self.task,'fixture',False,0)
        self.store.commit_response(request,'fixture',SegmentResponse(
            {1:NON_CONTENT_PAGE,2:'## Topic\nA useful fact.'},'stop','fixture'),True)
        dialog = SourceDialog(self.store.path,self.task,2)
        self.dialogs.append(dialog)
        dialog.show()
        self.spin(lambda:dialog.worker is None and dialog._loaded_page == 1)
        self.assertNotIn(NON_CONTENT_PAGE,dialog.excerpt.toPlainText())
        self.assertIn('was reviewed',dialog.excerpt.toPlainText())
        self.assertIn('no study content',dialog.status.text())
        dialog.page.setValue(2)
        self.spin(lambda:dialog.worker is None and dialog._loaded_page == 2)
        self.assertIn('A useful fact.',dialog.excerpt.toPlainText())

    def test_source_pages_load_in_background_and_close_cooperatively(self):
        dialog = SourceDialog(self.store.path, self.task, 2)
        self.dialogs.append(dialog)
        dialog.show()
        self.spin(lambda:dialog.worker is None and dialog._loaded_page == 1)
        first = dialog._pixmap.toImage()
        dialog.page.setValue(2)
        self.spin(lambda:dialog.worker is None and dialog._loaded_page == 2)
        self.assertNotEqual(dialog._pixmap.toImage(), first)
        real_render = SourceRenderer.render
        def delayed(renderer, page, cancel=None):
            time.sleep(.15)
            return real_render(renderer, page, cancel)
        with patch.object(SourceRenderer, 'render', delayed):
            dialog.page.setValue(1)
            dialog.timer.stop()
            dialog._load()
            dialog.reject()
            self.assertTrue(dialog.worker.isInterruptionRequested())
            self.spin(lambda:dialog.worker is None and not dialog.isVisible())

    def test_late_source_page_results_do_not_replace_the_page_being_viewed(self):
        dialog = SourceDialog(self.store.path,self.task,2)
        self.dialogs.append(dialog)
        dialog.show()
        self.spin(lambda:dialog.worker is None and dialog._loaded_page == 1)
        displayed = dialog._pixmap.toImage()
        key = dialog.image.pixmap().cacheKey()
        dialog._fit_image()
        self.assertEqual(dialog.image.pixmap().cacheKey(),key)
        dialog._shown((2,dialog.renderer.render(2),'A stale result'))
        self.assertEqual(dialog._loaded_page,1)
        self.assertEqual(dialog._pixmap.toImage(),displayed)
        self.assertTrue(dialog.locate_button.isEnabled())
