"""Native Markdown review and local source viewing; no embedded browser engine."""

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QDesktopServices, QPixmap, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
                            QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
                            QTabWidget, QTextBrowser, QVBoxLayout)

from app.core.source_review import SourceRenderer
from app.core.task_store import TaskStore
from app.ui.workers import TaskWorker


class MarkdownBrowser(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._open_link)

    def loadResource(self, resource_type, url):
        # Notes never implicitly read local files or fetch remote images.
        return None

    def _open_link(self, url):
        if url.scheme() in {'http', 'https'}:
            QDesktopServices.openUrl(url)
        elif not url.scheme() and url.fragment():
            self.scrollToAnchor(url.fragment())


class RevisionDialog(QDialog):
    def __init__(self, store_path, task_id, markdown, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Edit notes')
        self.resize(800, 650)
        self.store_path, self.task_id = store_path, task_id
        self.worker = None
        self.saved = None
        self.original = markdown
        layout = QVBoxLayout(self)
        caption = QLabel('Save a revision, then export or publish it from the main window. Generated page notes stay available for source review.')
        caption.setWordWrap(True)
        layout.addWidget(caption)
        self.tabs = QTabWidget()
        self.editor = QPlainTextEdit()
        self.editor.setPlainText(markdown)
        self.editor.setAccessibleName('Revision Markdown')
        self.preview = MarkdownBrowser()
        self.tabs.addTab(self.editor, 'Markdown')
        self.tabs.addTab(self.preview, 'Preview')
        self.tabs.currentChanged.connect(self._preview)
        layout.addWidget(self.tabs, 1)
        self.status = QLabel('Edits are saved locally; no model or Notion request is sent.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText('Save revision')
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Save), self)
        self.shortcut.activated.connect(self.save)

    def _preview(self):
        if self.tabs.currentIndex() == 1:
            self.preview.setMarkdown(self.editor.toPlainText())

    def save(self):
        if self.worker:
            return
        markdown = self.editor.toPlainText()
        self.worker = TaskWorker(lambda worker: TaskStore(self.store_path).save_revision(self.task_id, markdown), self)
        self.worker.result.connect(lambda revision: setattr(self, 'saved', (revision, markdown)))
        self.worker.failed.connect(self.status.setText)
        self.worker.finished.connect(self._finished)
        self.editor.setReadOnly(True)
        self.buttons.setEnabled(False)
        self.worker.start()

    def _finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self.buttons.setEnabled(True)
        self.editor.setReadOnly(False)
        if self.saved:
            super().accept()

    def reject(self):
        if self.worker:
            self.status.setText('Saving revision… Please wait for the local write to finish.')
            return
        if self.editor.toPlainText() != self.original:
            choice = QMessageBox.question(self, 'Unsaved revision', 'Save your changes before closing?',
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if choice == QMessageBox.StandardButton.Save:
                self.save()
                return
            if choice != QMessageBox.StandardButton.Discard:
                return
        super().reject()

    def closeEvent(self, event):
        self.reject()
        event.ignore()


class SourceDialog(QDialog):
    def __init__(self, store_path, task_id, page_count, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Review source pages')
        self.resize(850, 720)
        self.store_path, self.task_id = store_path, task_id
        self.worker = self.renderer = None
        self._closing = self._cleaning = False
        self._override = None
        self._loaded_page = None
        self._pixmap = QPixmap()
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel('Source page'))
        self.page = QSpinBox()
        self.page.setRange(1, max(1, page_count))
        row.addWidget(self.page)
        row.addStretch()
        locate = QPushButton('Locate source')
        locate.clicked.connect(self._locate)
        row.addWidget(locate)
        layout.addLayout(row)
        caption = QLabel('Page numbers refer to the original generated notes. Human revisions can reorganize those notes.')
        caption.setWordWrap(True)
        layout.addWidget(caption)
        self.status = QLabel('Loading source…')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.scroll.setWidget(self.image)
        layout.addWidget(self.scroll, 1)
        self.excerpt = QPlainTextEdit()
        self.excerpt.setReadOnly(True)
        self.excerpt.setMaximumHeight(110)
        self.excerpt.setPlaceholderText('Generated note for this source page')
        layout.addWidget(self.excerpt)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._load)
        self.page.valueChanged.connect(lambda: self.timer.start(150))
        self.timer.start(0)

    def _locate(self):
        if self.worker:
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Locate the original source', '', 'Course material (*.pdf *.pptx)')
        if path:
            self._override = path
            self._loaded_page = None
            self._load()

    def _load(self):
        if self.worker or self._closing:
            return
        page, override = self.page.value(), self._override
        self._attempt_page = page
        self.status.setText(f'Loading source page {page}…')
        def render(worker):
            store = TaskStore(self.store_path)
            task = store.get(self.task_id)
            path = override or task['options']['input_path']
            if not self.renderer or self.renderer.path != Path(path):
                if self.renderer:
                    self.renderer.close()
                self.renderer = SourceRenderer(path, task['doc_hash'])
            image = self.renderer.render(page, worker.isInterruptionRequested)
            notes = {p:text for segment in store.segments(task['cache_key']) for p,text in segment['notes'].items()}
            return page, image, notes.get(page, 'This page has no accepted generated note.')
        self._start(render, self._shown)

    def _start(self, operation, handler):
        self.worker = TaskWorker(operation, self)
        self.worker.result.connect(handler)
        self.worker.failed.connect(self.status.setText)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _shown(self, payload):
        if self._closing:
            return
        page, data, note = payload
        self._loaded_page = page
        self._pixmap.loadFromData(data)
        self._fit_image()
        self.excerpt.setPlainText(note)
        self.status.setText(f'Original page {page} · generated note below')

    def _fit_image(self):
        if not self._pixmap.isNull():
            self.image.setPixmap(self._pixmap.scaledToWidth(max(100, self.scroll.viewport().width()-20), Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'scroll'):
            self._fit_image()

    def _finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        if self._closing:
            if self._cleaning:
                super().reject()
            else:
                self._cleaning = True
                self._start(lambda worker: self.renderer.close() if self.renderer else None, lambda _: None)
        elif getattr(self, '_attempt_page', None) != self.page.value():
            self.timer.start(0)

    def reject(self):
        self._closing = True
        self.timer.stop()
        if self.worker:
            self.worker.requestInterruption()
        else:
            self._cleaning = True
            self._start(lambda worker: self.renderer.close() if self.renderer else None, lambda _: None)

    def closeEvent(self, event):
        self.reject()
        event.ignore()
