"""Responsive desktop shell. Document work and HTTP requests run in TaskWorker."""

import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl, QLockFile, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QApplication, QInputDialog, QLabel, QLayout, QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSystemTrayIcon, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from app.core.configuration import cloud_defaults, make_client, make_model_config, migrate_config
from app.core.generator.markdown import render_source_pages
from app.core.maintenance import clean_storage, format_bytes
from app.core.notion import NotionClient, NotionPublishResult
from app.core.pipeline import Pipeline, PipelineResult
from app.core.preflight import PreflightService
from app.core.task_store import TaskStore
from app.ui.branding import application_icon, menu_bar_icon
from app.ui.components import ActivityPanel, BusyIndicator, MetricCard
from app.ui.settings import SettingsDialog
from app.ui.review import MarkdownBrowser, RevisionDialog, SourceDialog
from app.ui.workers import TaskWorker
from app.utils.helpers import load_config, save_config, validate_file_type


APP_STYLE = """
QMainWindow, QWidget#root { background: #F4F2ED; color: #20231F; }
QFrame#card {
    background: #FFFEFB;
    border: 1px solid #DEDCD5;
    border-radius: 12px;
}
QLabel#eyebrow { color: #49685A; font-size: 11px; font-weight: 700; }
QLabel#muted { color: #73756F; }
QLabel#sectionTitle { font-size: 12px; font-weight: 650; color: #4F524C; }
QFrame#metricCard { background: #F6F5F1; border: none; border-radius: 9px; }
QLabel#metricTitle { color: #777A72; font-size: 11px; }
QLabel#metricValue { color: #252922; font-size: 17px; font-weight: 650; }
QLabel#metricDetail { color: #676C62; font-size: 11px; }
QLabel#activityCount { color: #777A72; font-size: 11px; }
QPushButton#activityToggle { border: none; background: transparent; padding: 0 2px; min-height: 32px; }
QPlainTextEdit#activityLog { background: #F7F6F2; border: none; border-radius: 7px;
    padding: 8px; color: #565C51; font-family: Menlo, monospace; font-size: 11px; }
QLineEdit, QComboBox, QTextEdit {
    background: #FFFEFB;
    color: #20231F;
    border: 1px solid #CFCDC5;
    border-radius: 8px;
    padding: 8px 10px;
    selection-background-color: #49685A;
}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus { border-color: #49685A; }
QPushButton {
    min-height: 34px;
    padding: 0 15px;
    border-radius: 8px;
    border: 1px solid #CFCDC5;
    background: #FFFEFB;
    color: #343731;
    font-weight: 550;
}
QPushButton:hover { background: #F7F5EF; border-color: #A6A49D; }
QPushButton#primary {
    min-height: 42px;
    color: #FFFFFF;
    border: none;
    background: #2F5545;
    font-size: 14px;
    font-weight: 650;
}
QPushButton#primary:hover { background: #244638; }
QPushButton#primary:disabled { background: #A9B2AC; }
QPushButton#danger { color: #9B3F36; border-color: #DDB8B2; }
QCheckBox { spacing: 8px; }
QProgressBar { border: none; background: #DDDCD6; border-radius: 2px; }
QProgressBar::chunk { background: #C27B50; border-radius: 2px; }
QTabWidget::pane { border: 1px solid #DEDCD5; border-radius: 8px; background: #FFFEFB; }
QTabBar::tab { padding: 9px 16px; color: #73756F; }
QTabBar::tab:selected { color: #2F5545; font-weight: 650; }
QScrollArea { background: transparent; }
"""


class DropZone(QFrame):
    file_requested = pyqtSignal()
    file_selected = pyqtSignal(str)
    file_cleared = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setObjectName("dropZone")
        self.setMinimumHeight(125)
        self.setStyleSheet(self._style(False))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(7)
        self.icon = QLabel("＋")
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setStyleSheet("font-size: 25px; color: #49685A; border: none;")
        self.label = QLabel("Drop a PDF or PowerPoint here")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("font-size: 14px; font-weight: 600; border: none;")
        self.detail = QLabel("or click to choose a file · PDF and PPTX supported")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail.setStyleSheet("color: #667085; border: none;")
        layout.addWidget(self.icon)
        layout.addWidget(self.label)
        layout.addWidget(self.detail)

    def _style(self, active: bool) -> str:
        background = "#F1F5F1" if active else "#FFFEFB"
        border = "#49685A" if active else "#B9BDB5"
        return (
            f"QFrame#dropZone {{ background: {background}; border: 2px dashed {border}; "
            "border-radius: 14px; }"
        )

    def set_file(self, path: str) -> None:
        self.icon.setText("✓")
        self.icon.hide()
        self.setMinimumHeight(76)
        self.layout().setContentsMargins(16, 12, 16, 12)
        self.label.setText(Path(path).name)
        size_mb = os.path.getsize(path) / (1024 * 1024)
        file_type = "PDF document" if Path(path).suffix.lower() == ".pdf" else "PowerPoint deck"
        self.detail.setText(f"{file_type} detected · {size_mb:.1f} MB · click to replace")
        self.setStyleSheet(self._style(True))

    def clear_file(self) -> None:
        self.icon.setText("＋")
        self.icon.show()
        self.setMinimumHeight(125)
        self.layout().setContentsMargins(20, 20, 20, 20)
        self.label.setText("Drop a PDF or PowerPoint here")
        self.detail.setText("or click to choose a file · PDF and PPTX supported")
        self.setStyleSheet(self._style(False))
        self.file_cleared.emit()

    def mousePressEvent(self, event) -> None:
        self.file_requested.emit()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if urls and validate_file_type(urls[0].toLocalFile()):
            event.acceptProposedAction()
            self.setStyleSheet(self._style(True))

    def dragLeaveEvent(self, event) -> None:
        self.setStyleSheet(self._style(bool(self.label.text() and self.icon.text() == "✓")))
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if validate_file_type(path):
                self.file_selected.emit(path)
                event.acceptProposedAction()


class MainWindow(QMainWindow):
    CONFIG_FILE = str(Path.home() / ".noteagent_config.json")

    def __init__(self):
        super().__init__()
        self.input_file = None
        self.worker = None
        self.last_result = None
        self.task_id = None
        self._task_context = None
        self._close_after_worker = False
        self._closed = False
        self._preview_buffer = []
        self._preview_markdown = ""
        self._rendered_markdown = None
        self._after_worker = None
        self.tray_icon = None
        self._estimate_worker = None
        self._estimate_revision = 0
        self._estimate_pending = self._estimate_refresh = self._close_after_estimate = False
        self._estimate = None
        self._estimate_context = self._active_context = None
        self._preflight = PreflightService()
        self._config = self._default_config()
        saved = load_config(self.CONFIG_FILE)
        self._has_language_preference = "language" in saved
        self._config.update(migrate_config(saved))
        self.store_path = Path(self.CONFIG_FILE).parent / ".noteagent" / "tasks.sqlite3"
        self._lock = QLockFile(self.CONFIG_FILE + ".lock")
        self._owns_lock = self._lock.tryLock(0)
        self._build_ui()
        self._restore_ui_config()
        self.setStyleSheet(APP_STYLE)
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(100)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._flush_preview)
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(30000)
        self._retry_timer.timeout.connect(self._check_due)
        self._retry_timer.start()
        self._estimate_timer = QTimer(self)
        self._estimate_timer.setSingleShot(True)
        self._estimate_timer.timeout.connect(self._begin_estimate)
        self.language_combo.currentIndexChanged.connect(self._language_changed)
        self.include_source_pages.toggled.connect(self._source_pages_changed)
        self.publish_notion.toggled.connect(self._render_state)
        self._render_state()
        QTimer.singleShot(0, self._setup_menu_bar)
        if self._owns_lock:
            QTimer.singleShot(0, self._load_previous)
        else:
            self.status.setText("Another NoteAgent window owns this profile. Close it before processing here.")

    def _default_config(self):
        return {**cloud_defaults(), "notion_token": "", "notion_page_id": "",
                "output_dir": str(Path.home() / "Desktop" / "NoteAgent_Output"),
                "output_format": "md", "language": "zh", "notion_mode": "none", "include_source_pages": False}

    def _setup_menu_bar(self):
        if self._closed or self.tray_icon is not None:
            return
        if sys.platform != "darwin" or QApplication.platformName() != "cocoa":
            return
        self.tray_icon = QSystemTrayIcon(menu_bar_icon(), self)
        self.tray_icon.setToolTip("NoteAgent")
        self._tray_menu = menu = QMenu(self)
        menu.addAction("Open NoteAgent", self._activate_window)
        settings = menu.addAction("Settings…", self._open_settings_from_menu)
        menu.addSeparator()
        quit_action = menu.addAction("Quit NoteAgent", self.close)
        def update_menu():
            modal = QApplication.activeModalWidget() is not None
            settings.setEnabled(not self._busy() and not modal)
            quit_action.setEnabled(not modal)
        menu.aboutToShow.connect(update_menu)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._menu_bar_activated)
        self.tray_icon.show()

    def _activate_window(self):
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        modal = QApplication.activeModalWidget()
        if modal is not None:
            modal.raise_()
            modal.activateWindow()

    def _open_settings_from_menu(self):
        self._activate_window()
        self._show_settings()

    def _menu_bar_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._activate_window()

    def _build_ui(self):
        self.setWindowTitle("NoteAgent")
        self.setWindowIcon(application_icon())
        self.setMinimumSize(760, 640)
        self.resize(940, 820)
        root = QWidget()
        root.setObjectName("root")
        self.page_scroll = QScrollArea()
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page_scroll.setWidget(root)
        self.setCentralWidget(self.page_scroll)
        outer = QVBoxLayout(root)
        outer.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        outer.setContentsMargins(24, 20, 24, 20)
        heading = QHBoxLayout()
        heading.setSpacing(12)
        logo = QLabel()
        logo.setObjectName("appLogo")
        logo.setFixedSize(40, 40)
        logo.setPixmap(self.windowIcon().pixmap(40, 40))
        logo.setAccessibleName("NoteAgent logo")
        heading.addWidget(logo)
        title = QLabel("NoteAgent")
        title.setStyleSheet("font-size: 24px; font-weight: 650;")
        heading.addWidget(title)
        heading.addStretch()
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self._show_settings)
        heading.addWidget(self.settings_button)
        outer.addLayout(heading)
        self.drop_zone = DropZone()
        self.drop_zone.file_requested.connect(self._browse_input)
        self.drop_zone.file_selected.connect(self._set_input_file)
        self.drop_zone.file_cleared.connect(self._clear_input)
        outer.addWidget(self.drop_zone)
        options = QHBoxLayout()
        self.format_combo = QComboBox()
        self.format_combo.addItem("Markdown (.md)", "md")
        self.format_combo.addItem("Jupyter Notebook (.ipynb)", "ipynb")
        self.language_combo = QComboBox()
        self.language_combo.addItem("中文", "zh")
        self.language_combo.addItem("English", "en")
        options.addWidget(self.format_combo, 1)
        options.addWidget(self.language_combo)
        self.include_source_pages = QCheckBox("Include source pages")
        self.include_source_pages.setToolTip("Show source-page references in previews, local exports and Notion. No extra model requests.")
        options.addWidget(self.include_source_pages)
        outer.addLayout(options)
        destination = QHBoxLayout()
        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText("Output folder")
        self.browse_output = QPushButton("Browse")
        self.browse_output.clicked.connect(self._browse_output)
        destination.addWidget(self.output_path, 1)
        destination.addWidget(self.browse_output)
        outer.addLayout(destination)
        notion = QHBoxLayout()
        self.publish_notion = QCheckBox("Send to Notion")
        self.notion_mode = QComboBox()
        self.notion_mode.addItem("Create child page", "create_child")
        self.notion_mode.addItem("Append to page", "append")
        self.notion_status = QLabel()
        self.notion_status.setObjectName("muted")
        notion.addWidget(self.publish_notion)
        notion.addWidget(self.notion_mode)
        notion.addWidget(self.notion_status, 1)
        self.publish_notion.toggled.connect(self._update_notion_state)
        outer.addLayout(notion)
        self.estimate_card = QFrame()
        self.estimate_card.setObjectName("card")
        estimate_layout = QVBoxLayout(self.estimate_card)
        estimate_layout.setContentsMargins(12, 8, 12, 10)
        estimate_layout.setSpacing(8)
        estimate_row = QHBoxLayout()
        self.estimate_heading = QLabel("Document estimate")
        self.estimate_heading.setObjectName("sectionTitle")
        estimate_row.addWidget(self.estimate_heading)
        estimate_row.addStretch()
        self.estimate_spinner = BusyIndicator(self)
        estimate_row.addWidget(self.estimate_spinner)
        self.refresh_status = QLabel()
        self.refresh_status.setObjectName("muted")
        self.refresh_status.setStyleSheet("font-size: 11px;")
        estimate_row.addWidget(self.refresh_status)
        self.refresh_allowance = QPushButton("Refresh")
        self.refresh_allowance.setFixedWidth(122)
        self.refresh_allowance.setToolTip("Refresh allowance, selected-model prices and cached pages. No inference.")
        self.refresh_allowance.clicked.connect(lambda: self._schedule_estimate(refresh=True))
        estimate_row.addWidget(self.refresh_allowance)
        estimate_layout.addLayout(estimate_row)
        metric_row = QHBoxLayout()
        metric_row.setSpacing(8)
        self.document_metric = MetricCard("DOCUMENT")
        self.model_metric = MetricCard("GENERATION MODEL")
        self.allowance_metric = MetricCard("FREE TODAY")
        self.backup_metric = MetricCard("PAID BACKUP")
        for metric in (self.document_metric, self.model_metric, self.allowance_metric, self.backup_metric):
            metric_row.addWidget(metric, 1)
        estimate_layout.addLayout(metric_row)
        self.estimate_warning = QLabel()
        self.estimate_warning.setWordWrap(True)
        self.estimate_warning.setStyleSheet("color: #93632D; font-size: 11px;")
        self.estimate_warning.hide()
        estimate_layout.addWidget(self.estimate_warning)
        self.estimate_card.hide()
        outer.addWidget(self.estimate_card)
        self.status = QLabel("Ready · free requests first")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.status)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(5)
        self.progress_bar.hide()
        outer.addWidget(self.progress_bar)
        actions = QHBoxLayout()
        self.generate_button = QPushButton("Create notes")
        self.generate_button.setObjectName("primary")
        self.generate_button.clicked.connect(self._primary_action)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel_generation)
        self.paid_button = QPushButton("Use paid backup")
        self.paid_button.clicked.connect(self._continue_paid)
        self.more_button = QPushButton("More")
        self.more_button.setToolTip("Actions for the saved notes shown below")
        self.note_actions = QMenu(self.more_button)
        self.more_button.setMenu(self.note_actions)
        self.edit_action = self.note_actions.addAction('Edit notes', self._edit_notes)
        self.source_action = self.note_actions.addAction('Review sources', self._review_sources)
        self._export_separator = self.note_actions.addSeparator()
        self.export_action = self.note_actions.addAction('Export available pages', lambda:self._saved_action('export'))
        self.export_action.setToolTip('Save accepted notes in the selected format and folder. No model requests or Notion publishing.')
        self.open_file_action = self.note_actions.addAction('Open previous export', self._open_output)
        self._notion_separator = self.note_actions.addSeparator()
        self.publish_action = self.note_actions.addAction('Send to Notion', self._retry_notion)
        self.open_notion_action = self.note_actions.addAction('Open in Notion', self._open_notion)
        self._charge_separator = self.note_actions.addSeparator()
        self.reconcile_action = self.note_actions.addAction('Reconcile charge', self._reconcile)
        self.note_actions.aboutToShow.connect(self._render_state)
        actions.addWidget(self.generate_button, 1)
        for button in (self.paid_button, self.cancel_button, self.more_button):
            actions.addWidget(button)
        outer.addLayout(actions)
        self.revision_status = QLabel('Notes')
        self.revision_status.setObjectName('muted')
        self.revision_status.setContentsMargins(0, 0, 5, 0)
        self.notes_tabs = QTabWidget()
        self.rendered_preview = MarkdownBrowser()
        self.rendered_preview.setToolTip('Native Markdown preview. Formulas remain in LaTeX; Notebook and Notion render them on export.')
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(160)
        self.preview.setPlaceholderText("Completed page notes appear here. Progress is saved after each segment.")
        self.rendered_preview.setMinimumHeight(160)
        self.notes_tabs.addTab(self.rendered_preview, 'Preview')
        self.notes_tabs.addTab(self.preview, 'Markdown')
        self.notes_tabs.currentChanged.connect(self._render_preview)
        self.notes_tabs.setCornerWidget(self.revision_status, Qt.Corner.TopRightCorner)
        outer.addWidget(self.notes_tabs, 1)
        self.activity = ActivityPanel()
        self.details_button, self.log = self.activity.toggle_button, self.activity.log
        outer.addWidget(self.activity)

    def _restore_ui_config(self):
        self.output_path.setText(self._config.get("output_dir", ""))
        self.format_combo.setCurrentIndex(max(0, self.format_combo.findData(self._config.get("output_format", "md"))))
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(self._config.get("language", "zh"))))
        self.include_source_pages.setChecked(bool(self._config.get("include_source_pages", False)))
        mode = self._config.get("notion_mode", "none")
        self.publish_notion.setChecked(mode != "none")
        self.notion_mode.setCurrentIndex(max(0, self.notion_mode.findData(mode)))
        self._update_notion_state()

    def _busy(self):
        return self.worker is not None

    def _language_changed(self):
        self._has_language_preference = True
        self._render_state()
        self._schedule_estimate()

    def _source_pages_changed(self):
        self._flush_preview()
        scrollbar = self.preview.verticalScrollBar()
        position = scrollbar.value() / max(1, scrollbar.maximum())
        self.preview.setPlainText(render_source_pages(self._preview_markdown, self.include_source_pages.isChecked()))
        self._render_preview()
        scrollbar.setValue(round(position * scrollbar.maximum()))
        self._save_config()

    def _clear_preview(self):
        self._preview_timer.stop()
        self._preview_buffer.clear()
        self._preview_markdown = ""
        self._rendered_markdown = None
        self.preview.clear()
        self.rendered_preview.clear()

    def _set_preview_markdown(self, markdown):
        self._preview_markdown = markdown
        self.preview.setPlainText(render_source_pages(markdown, self.include_source_pages.isChecked()))
        self._render_preview()

    def _render_preview(self, follow=False):
        if self.notes_tabs.currentIndex() != 0:
            return
        markdown = self.preview.toPlainText()
        if markdown == self._rendered_markdown:
            return
        self._rendered_markdown = markdown
        scrollbar = self.rendered_preview.verticalScrollBar()
        position, maximum = scrollbar.value(), scrollbar.maximum()
        follow = follow and position >= maximum-4
        self.rendered_preview.setMarkdown(markdown)
        scrollbar.setValue(scrollbar.maximum() if follow else min(position, scrollbar.maximum()))

    def _document_context(self):
        return (self.input_file, self.language_combo.currentData(), self._config.get("provider"),
                self._config.get("base_url"), self._config.get("vision_model"),
                self._config.get("paid_model"), bool(self._config.get("paid_enabled")))

    def _generation_context(self):
        # Output format, keys, provider filters and budgets do not change accepted notes.
        return (self.input_file, self.language_combo.currentData(), make_model_config(self._config).fingerprint())

    def _matches_current_task(self):
        if not self.task_id or not self.last_result or not self._task_context:
            return False
        path, language, profile = self._generation_context()
        # Completed notes remain usable if the original document was moved or deleted.
        return (path or self._task_context[0], language, profile) == self._task_context

    def _primary_mode(self):
        if not self._matches_current_task():
            return "create"
        result = self.last_result
        if result.status == "completed":
            if result.revision_id != result.exported_revision_id:
                return 'export'
            if result.output_path and os.path.isfile(result.output_path):
                return "open"
            return "export" if result.completed_pages or result.revision_id else "create"
        if result.reason == "uncertain_cost":
            return "reconcile"
        return "resume"

    def _primary_action(self):
        if self._busy() or not self._owns_lock:
            return
        action = self._primary_mode()
        if action == "open":
            self._open_output()
        elif action == "export":
            self._saved_action("export")
        elif action == "reconcile":
            self._reconcile()
        elif action == "resume":
            self._start_generation(resume=True)
        else:
            self._start_generation()

    def _set_model_metrics(self):
        hosted = self._config.get("provider") == "openrouter"
        model = self._config.get("vision_model", "")
        self.model_metric.set_content("Free · $0" if hosted else "Custom API", model.split("/", 1)[-1], model)
        if hosted and self._config.get("paid_enabled"):
            model = self._config.get("paid_model", "")
            self.backup_metric.set_content("—", model.split("/", 1)[-1], f"Selected backup: {model}")
        else:
            self.backup_metric.set_content("Off", "Enable in Settings for a cost estimate" if hosted else "Service billing applies")

    def _sync_refresh_state(self):
        checking = self._estimate_worker is not None or (self._estimate_pending and not self._busy())
        self.estimate_spinner.set_state(checking, "#49685A")
        self.refresh_allowance.setEnabled(not self._busy() and not checking)
        self.refresh_allowance.setText("Refreshing…" if checking else "Refresh")
        if checking:
            self.refresh_status.setText("Checking…")
        elif self._estimate_pending and self._busy():
            self.refresh_status.setText("Refresh after task")

    def _schedule_estimate(self, refresh=False):
        if self._closed or self._close_after_estimate:
            return
        self._estimate_revision += 1
        self._estimate_refresh |= refresh
        self._estimate_pending = True
        if self._estimate_worker is not None:
            self._estimate_worker.requestInterruption()
        if not self.input_file:
            self._estimate = None
            self.estimate_card.hide()
            self._estimate_pending = False
            self.estimate_spinner.set_state(False)
            return
        # Retain the last snapshot during a refresh of the same settings, avoiding flicker.
        if self._estimate_context != self._document_context():
            self._estimate = None
            self.document_metric.set_content("—", "Reading document…")
            self.allowance_metric.set_content("—", "Checking account…")
            self._set_model_metrics()
            self.estimate_warning.setText("")
            self.estimate_warning.hide()
        self.estimate_card.show()
        self._sync_refresh_state()
        self._estimate_timer.start(200)

    def _begin_estimate(self):
        if self._closed or self._close_after_estimate or not self.input_file:
            return
        if self._estimate_worker is not None or self._busy():
            self._estimate_pending = True
            self._sync_refresh_state()
            return
        revision, path = self._estimate_revision, self.input_file
        config = {**self._config, "language": self.language_combo.currentData()}
        refresh, self._estimate_refresh = self._estimate_refresh, False
        self._estimate_pending = False
        self.estimate_card.show()
        self._estimate_worker = TaskWorker(lambda worker: self._preflight.estimate(
            path, config, self.store_path, worker.isInterruptionRequested, refresh), self)
        self._estimate_worker.result.connect(lambda result: self._receive_estimate(revision, result))
        self._estimate_worker.failed.connect(lambda message: self._estimate_failed(revision, message))
        self._estimate_worker.finished.connect(self._estimate_finished)
        self._sync_refresh_state()
        self._estimate_worker.start()

    def _receive_estimate(self, revision, result):
        if revision == self._estimate_revision and not self._closed:
            self._estimate = result
            self._estimate_context = self._document_context()
            self.refresh_status.setText("Checked " + datetime.now().strftime("%H:%M"))
            self._render_estimate()

    def _estimate_failed(self, revision, message):
        if revision == self._estimate_revision and not self._closed:
            self.refresh_status.setText("Refresh unavailable")
            if self._estimate is None:
                self.document_metric.set_content("—", "Estimate unavailable")
                self.allowance_metric.set_content("Unknown", "Retry refresh")
            self.estimate_warning.setText(message)
            self.estimate_warning.show()

    def _estimate_finished(self):
        if self.sender() is not self._estimate_worker:
            return
        worker, self._estimate_worker = self._estimate_worker, None
        worker.deleteLater()
        self._sync_refresh_state()
        if self._close_after_estimate:
            QTimer.singleShot(0, self.close)
        elif self._estimate_pending:
            self._estimate_timer.start(0)

    def _render_estimate(self):
        estimate = self._estimate
        if estimate is None:
            return
        total, pending, segment = estimate.document.page_count, estimate.pending_pages, estimate.segment_pages
        result = self.last_result
        # Only overlay live progress from this exact file/profile/language, never an old
        # completed task with the same page count or a different language selection.
        if (result and result.task_id == self.task_id and result.page_count == total and
                self._active_context == self._estimate_context == self._document_context()):
            pending = max(0, total - len(result.completed_pages))
            segment = result.segment_pages
        requests = (pending + segment - 1) // segment
        self.document_metric.set_content(f"{total} pages", f"{total-pending} cached · {pending} remaining\n≈ {requests} requests",
            f"Based on groups of up to {segment} pages. Retries and output limits can increase requests.")
        self._set_model_metrics()
        remaining = estimate.quota.remaining_requests if estimate.quota else None
        if remaining is not None:
            coverage = min(pending, remaining * segment)
            detail = 'No requests needed for this document' if not pending else f"≈ {coverage} of {pending} remaining pages\nExcludes retries"
            self.allowance_metric.set_content(f"{remaining} requests", detail,
                estimate.quota_note + f"\nAssumes {segment} pages per request; shared usage and retries affect coverage.")
        else:
            detail = ("Managed by your service" if self._config.get("provider") != "openrouter" else
                      "Allowance not reported")
            if not pending:
                detail = 'No requests needed for this document'
            self.allowance_metric.set_content("Unknown", detail, estimate.quota_note)
        if estimate.backup_enabled:
            name = estimate.cost_model.split("/", 1)[-1]
            if not pending:
                self.backup_metric.set_content('Not needed', name, f'Selected backup: {estimate.cost_model}')
            elif estimate.cost_low is not None:
                factor = pending / max(1, estimate.pending_pages)
                self.backup_metric.set_content(f"${estimate.cost_low*factor:.3f}–${estimate.cost_high*factor:.3f}",
                    name + "\nIf all remaining pages use backup", estimate.price_note + f"\nSelected backup: {estimate.cost_model}")
            else:
                self.backup_metric.set_content("Unknown", name + "\nPrice unavailable · refresh", estimate.price_note)
        warnings = []
        if pending and self._config.get("provider") != "openrouter":
            warnings.append("Custom service: allowance and cost are unverified; provider charges may apply.")
        elif pending and not estimate.backup_enabled:
            warnings.append("Paid backup is off · notes are saved and the task pauses if free quota runs out.")
        if remaining is not None and pending > remaining * segment:
            warnings.append("Today's free allowance may not cover the remaining pages.")
        if pending and estimate.backup_enabled and estimate.backup_warning:
            warnings.append(estimate.backup_warning)
        self.estimate_warning.setText("\n".join(warnings))
        self.estimate_warning.setVisible(bool(warnings))


    def _render_state(self):
        busy = self._busy()
        result = self.last_result
        action = self._primary_mode()
        labels = {"create": "Create notes", "open": "Open notes", "export": "Export notes",
                  "resume": "Resume", "reconcile": "Reconcile charge"}
        tips = {"create": "Generate notes using the selected language and model; reuse compatible cached pages.",
                "open": "Open the saved file. No generation, export or Notion publishing.",
                "export": "The saved file is missing. Export the accepted notes without model requests.",
                "resume": "Continue this task with the free profile; reuse completed pages.",
                "reconcile": "Check the uncertain charge before resuming this task."}
        waiting = bool(action == "resume" and result.status == "waiting_quota" and
                       result.retry_at and time.time() < result.retry_at)
        if busy:
            label = "Stopping…" if self.worker.isInterruptionRequested() else "Working…"
        else:
            label = "Waiting for free quota" if waiting else labels[action]
        self.generate_button.setText(label)
        if not busy and action == 'export' and result and result.revision_id != result.exported_revision_id:
            self.generate_button.setText('Export revision')
            tips[action] = 'Save the current revision to a new local file. No generation or Notion publishing.'
        self.generate_button.setToolTip("See the status above for the free retry time." if waiting else tips[action])
        self.generate_button.setEnabled(not busy and not waiting and self._owns_lock)
        self.paid_button.setVisible(bool(result and result.paid_eligible and result.status != "completed"))
        self.paid_button.setEnabled(not busy and self._matches_current_task() and bool(self._config.get("paid_enabled")))
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(busy and not self.worker.isInterruptionRequested())
        has_notes = bool(result and (result.completed_pages or result.revision_id))
        self.export_action.setEnabled(not busy and has_notes)
        self.export_action.setVisible(has_notes and action != "export")
        self.export_action.setText("Export again" if result and result.status == "completed" else "Export available pages")
        self.publish_action.setVisible(bool(result and result.status == "completed" and
                                                self.publish_notion.isChecked() and
                                                (not result.notion or result.revision_id != result.published_revision_id or
                                                 result.reason in {"notion_failed", "notion_setup"})))
        self.publish_action.setText("Retry Notion" if result and result.reason == "notion_failed" else
                                        'Send revision' if result and result.notion and result.revision_id != result.published_revision_id else "Send to Notion")
        self.publish_action.setEnabled(not busy)
        self.reconcile_action.setVisible(bool(result and result.reason in {"uncertain_cost", "interrupted"} and action != "reconcile"))
        self.reconcile_action.setEnabled(not busy)
        self.open_file_action.setVisible(bool(result and result.output_path and os.path.isfile(result.output_path) and action != "open"))
        self.open_file_action.setEnabled(not busy)
        self.open_notion_action.setVisible(bool(result and result.notion))
        self.open_notion_action.setEnabled(not busy)
        self.source_action.setVisible(bool(result and result.page_count))
        self.source_action.setEnabled(not busy)
        self.edit_action.setVisible(bool(result and result.status == 'completed' and self._preview_markdown))
        self.edit_action.setEnabled(not busy and self._owns_lock)
        self._export_separator.setVisible(self.export_action.isVisible() or self.open_file_action.isVisible())
        self._notion_separator.setVisible(self.publish_action.isVisible() or self.open_notion_action.isVisible())
        self._charge_separator.setVisible(self.reconcile_action.isVisible())
        self.more_button.setVisible(any(action.isVisible() and not action.isSeparator() for action in self.note_actions.actions()))
        self.more_button.setEnabled(not busy and self._owns_lock)
        if busy:
            self.note_actions.hide()
        revised = bool(result and result.revision_id)
        needs_export = bool(result and result.revision_id != result.exported_revision_id)
        self.revision_status.setText('Revision · not exported' if needs_export else 'Saved revision' if revised else 'Generated notes' if result else '')
        self.revision_status.setToolTip('Export the saved revision to update the local file.' if needs_export else 'Revisions keep the generated source-page notes intact.' if revised else '')
        for widget in (self.drop_zone, self.output_path, self.browse_output, self.format_combo,
                       self.language_combo, self.include_source_pages, self.publish_notion, self.settings_button):
            widget.setEnabled(not busy)
        self._update_notion_state()
        self._sync_refresh_state()
        self.activity.set_running(busy, busy and self.worker.isInterruptionRequested())
        self.progress_bar.setVisible(busy or bool(result and result.page_count))
        if result and result.page_count:
            self.progress_bar.setRange(0, result.page_count)
            self.progress_bar.setValue(len(result.completed_pages))
        else:
            self.progress_bar.setRange(0, 0)

    def _launch(self, operation, handler):
        if self._closed or self._busy() or not self._owns_lock:
            return False
        self.worker = TaskWorker(operation, self)
        self.worker.progress.connect(self._on_progress)
        self.worker.stream.connect(self._on_stream)
        self.worker.state.connect(self._on_state)
        self.worker.result.connect(handler)
        self.worker.failed.connect(self._on_failure)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()
        self._render_state()
        return True

    def _load_previous(self):
        if self._closed:
            return
        def load(worker):
            store = TaskStore(self.store_path)
            store.recover_interrupted()
            try:
                cleaned = clean_storage(self.store_path, cancel_callback=worker.isInterruptionRequested)
                if cleaned['removed_bytes'] or cleaned['removed_workspaces']:
                    worker.progress.emit(f"Cache maintenance · {format_bytes(cleaned['removed_bytes'])} cleared · {cleaned['removed_workspaces']} stale workspaces removed")
            except (OSError, sqlite3.OperationalError):
                worker.progress.emit("Cache cleanup deferred; saved notes are available.")
            task = store.latest()
            if task:
                task["notes"] = {p: text for seg in store.segments(task["cache_key"]) for p, text in seg["notes"].items()}
                task["cost"] = store.cost(task["id"])
                task['revision'] = store.latest_revision(task['id'])
                if store.uncertain(task["id"]):
                    task["reason"] = "uncertain_cost"
            return task
        self._launch(load, self._restore_previous)

    def _restore_previous(self, task):
        if not task:
            return
        options = task["options"]
        self.output_path.setText(options["output_dir"])
        self.format_combo.setCurrentIndex(max(0, self.format_combo.findData(options["output_format"])))
        if not self._has_language_preference or task["status"] != "completed":
            self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(options["language"])))
        self.task_id = task["id"]
        path = options["input_path"]
        self._task_context = (path, options["language"],
                              options.get("primary_profile", make_model_config(self._config).fingerprint()))
        if os.path.isfile(path):
            self.input_file = path
            self.drop_zone.set_file(path)
        notion = options.get("notion_result")
        revision = task.get('revision')
        self.last_result = PipelineResult(task["output_path"], options["title"], task["page_count"], 0, 1,
                                         notion=NotionPublishResult(**notion) if notion else None,
                                         task_id=self.task_id, status=task["status"], completed_pages=sorted(task["notes"]),
                                         cost_usd=task["cost"], reason=task["reason"], retry_at=task["retry_at"],
                                         paid_eligible=bool(task["paid_eligible"]),
                                         revision_id=revision['id'] if revision else 0,
                                         exported_revision_id=options.get('exported_revision_id',0),
                                         published_revision_id=options.get('published_revision_id',0))
        if revision:
            self._set_preview_markdown(revision['markdown'])
        elif task["markdown"]:
            self._set_preview_markdown(task["markdown"])
        else:
            self._set_preview_markdown("\n\n".join(f"{text}\n\n> Source: page {page}" for page, text in sorted(task["notes"].items())))
        self._on_state(self.last_result)
        self._schedule_estimate()

    def _job_operation(self, config, arguments):
        def operation(worker):
            store = TaskStore(self.store_path)
            primary = make_client(config) if arguments.get("action", "generate") == "generate" else None
            backup = make_client(config, paid=True) if primary and arguments.get("allow_paid") and config.get("paid_enabled") else None
            pipeline = Pipeline(primary, store=store, paid_client=backup,
                                document_limit=float(config.get("document_limit", .10)),
                                monthly_limit=float(config.get("monthly_limit", 1)))
            token = config.get("notion_token") or os.environ.get("NOTION_TOKEN", "")
            notion = NotionClient(token) if token else None
            result = pipeline.run(**arguments, notion_client=notion,
                                  progress_callback=worker.progress.emit, stream_callback=worker.stream.emit,
                                  state_callback=worker.state.emit, cancel_callback=worker.isInterruptionRequested)
            revision = store.latest_revision(result.task_id)
            return result, revision['markdown'] if revision else store.get(result.task_id)["markdown"]
        return operation

    def _arguments(self):
        return {"input_path": self.input_file or "", "output_dir": self.output_path.text().strip(),
                "output_format": self.format_combo.currentData(), "language": self.language_combo.currentData(),
                "include_source_pages": self.include_source_pages.isChecked(),
                "notion_mode": self.notion_mode.currentData() if self.publish_notion.isChecked() else "none",
                "notion_page_id": self._config.get("notion_page_id", "")}

    def _start_generation(self, checked=False, resume=False, paid=False):
        if self._busy():
            return
        if not self.input_file or not self.output_path.text().strip():
            self.status.setText("Choose a PDF/PPTX and an output folder first.")
            return
        arguments = self._arguments()
        if resume:
            arguments.update(task_id=self.task_id, allow_paid=paid)
        self._save_config()
        self._active_context = self._document_context()
        self._task_context = self._generation_context()
        if not resume:
            self.task_id = self.last_result = None
        self._estimate_revision += 1
        if self._estimate_worker is not None:
            self._estimate_worker.requestInterruption()
        self._estimate_pending = True
        self._clear_preview()
        self.activity.clear()
        self.status.setText("Preparing document…")
        self.activity.add_event("Preparing document…")
        self._launch(self._job_operation(dict(self._config), arguments), self._on_result)

    def _saved_action(self, action):
        if self._busy() or not self.task_id:
            return
        arguments = {**self._arguments(), "task_id": self.task_id, "action": action}
        if self._launch(self._job_operation(dict(self._config), arguments), self._on_result):
            message = 'Exporting notes…' if action == 'export' else 'Sending notes to Notion…'
            self.status.setText(message)
            self.activity.add_event(message)

    def _edit_notes(self):
        if self._busy() or not self.last_result or self.last_result.status != 'completed':
            return
        self._flush_preview()
        dialog = RevisionDialog(self.store_path, self.task_id,
                                render_source_pages(self._preview_markdown, self.include_source_pages.isChecked()), self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved:
            revision_id, markdown = dialog.saved
            self.last_result.revision_id = revision_id
            self._set_preview_markdown(markdown)
            self._on_state(self.last_result)
            self.activity.add_event('Saved a local revision. Export or publish it when ready.')
        dialog.deleteLater()

    def _review_sources(self):
        if self._busy() or not self.last_result or not self.task_id:
            return
        dialog = SourceDialog(self.store_path, self.task_id, self.last_result.page_count, self)
        dialog.exec()
        dialog.deleteLater()

    def _continue_paid(self):
        if self._busy() or not self.last_result or not self.last_result.paid_eligible:
            return
        choice = QMessageBox.question(self, "Use paid backup for this task?",
            f"Continue with {self._config['paid_model']}?\nDocument cap: ${self._config['document_limit']:.2f}; "
            f"monthly cap: ${self._config['monthly_limit']:.2f}.\nNo paid request is sent unless its maximum fits both caps.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if choice == QMessageBox.StandardButton.Yes:
            self._start_generation(resume=True, paid=True)

    def _retry_notion(self):
        if (self.last_result and self.last_result.notion and self.last_result.reason != 'notion_failed' and
                self.last_result.revision_id != self.last_result.published_revision_id):
            choice = QMessageBox.question(self, 'Publish revised copy?',
                'This publishes a new revised copy. Append adds it to the page; create-child makes a new page. Existing Notion content is kept.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if choice == QMessageBox.StandardButton.Yes:
                self._saved_action('publish')
            return
        if self.last_result and self.last_result.reason != "notion_failed":
            self._saved_action("publish")
            return
        choice = QMessageBox.question(self, "Retry Notion publishing?",
            "The previous write may have reached Notion. Check the target page before retrying to avoid duplicates.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if choice == QMessageBox.StandardButton.Yes:
            self._saved_action("publish")

    def _reconcile(self):
        if self._busy() or not self.task_id:
            return
        task_id = self.task_id
        def snapshot(store):
            return store.uncertain(task_id), store.get(task_id)["reason"], store.cost(task_id)
        def refreshed(payload):
            rows, reason, cost = payload
            if self.task_id != task_id:
                return
            self.last_result.reason = "uncertain_cost" if rows else reason
            self.last_result.cost_usd = cost
            self._on_state(self.last_result)
        def loaded(payload):
            rows, _, _ = payload
            if not rows:
                refreshed(payload)
                return
            row = rows[0]
            sent = datetime.fromtimestamp(row["created"]).astimezone().strftime("%b %d %H:%M:%S %Z")
            amount, accepted = QInputDialog.getDouble(self, "Reconcile OpenRouter charge",
                f"Check OpenRouter activity for {row['model']}, sent {sent}.\n{row['detail']}\n"
                f"Reserved: ${row['reserved']/1_000_000:.6f}. Enter the actual USD charge; use 0 only if not charged.",
                row["reserved"] / 1_000_000, 0, 100, 6)
            if accepted:
                def reconcile(worker):
                    store = TaskStore(self.store_path)
                    store.reconcile(row["id"], amount)
                    return snapshot(store)
                self._after_worker = lambda: self._launch(reconcile, refreshed)
        self._launch(lambda worker: snapshot(TaskStore(self.store_path)), loaded)

    def _check_due(self):
        result = self.last_result
        if QApplication.activeModalWidget() is not None or self.note_actions.isVisible():
            return
        if (not self._closed and not self._busy() and self._matches_current_task() and
                result.status == "waiting_quota" and result.retry_at and time.time() >= result.retry_at):
            self._start_generation(resume=True)

    def _cancel_generation(self):
        if self._busy():
            self.worker.requestInterruption()
            self.status.setText("Stopping… Completed pages are saved. The in-flight request is bounded by a timeout.")
            self._render_state()

    def _on_progress(self, message):
        self.activity.add_event(message)
        self.status.setText(message)

    def _on_stream(self, text):
        self._preview_markdown += text
        self._preview_buffer.append(render_source_pages(text, self.include_source_pages.isChecked()))
        if not self._preview_timer.isActive():
            self._preview_timer.start()

    def _flush_preview(self):
        self._preview_timer.stop()
        if not self._preview_buffer:
            return
        scrollbar = self.preview.verticalScrollBar()
        position = scrollbar.value()
        follow = position >= scrollbar.maximum() - 4
        cursor = QTextCursor(self.preview.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText("".join(self._preview_buffer))
        self._preview_buffer.clear()
        scrollbar.setValue(scrollbar.maximum() if follow else position)
        self._render_preview(follow=True)

    def _on_state(self, result):
        self.last_result, self.task_id = result, result.task_id
        if self._estimate and self._estimate.quota and result.free_requests_remaining is not None:
            self._estimate.quota.remaining_requests = result.free_requests_remaining
        self._render_estimate()
        descriptions = {"daily_quota": "Free daily quota exhausted", "budget": "Budget cannot cover the next request",
                        "privacy": "OpenRouter data-policy settings exclude this route; review its data-use requirements",
                        "capability": "No route supports the required image/schema/stream parameters; select another model",
                        "routing": "No endpoint matches the provider or price limits; check Settings",
                        "region": "Model/provider unavailable in this region; change the route in Settings",
                        "config": "Check the model, key and provider settings", "uncertain_cost": "Check and reconcile the uncertain paid charge",
                        "notion_failed": "Local notes saved; check Notion before retrying", "notion_setup": "Local notes saved; configure Notion",
                        "interrupted": "Previous session interrupted; resume saved progress", "cancelled": "Cancelled; completed pages saved"}
        detail = descriptions.get(result.reason, result.reason.replace("_", " "))
        text = f"{result.status.replace('_', ' ').title()} · {len(result.completed_pages)}/{result.page_count} pages · ${result.cost_usd:.6f} spent/reserved"
        if detail:
            text += f"\n{detail}"
        if result.retry_at:
            text += "\nFree retry after " + datetime.fromtimestamp(result.retry_at).astimezone().strftime("%b %d %H:%M %Z")
        if result.status == "waiting_paid":
            text += "\nThe next day's free retry failed. Configure a backup in Settings or retry free later."
        self.status.setText(text)
        self.activity.add_event(text)
        self._render_state()

    def _on_result(self, payload):
        result, markdown = payload
        self._flush_preview()
        if markdown:
            self._set_preview_markdown(markdown)
        self._on_state(result)

    def _on_failure(self, message):
        self.status.setText(message)
        self.activity.add_event(message)

    def _worker_finished(self):
        if self.sender() is not self.worker:
            return
        worker = self.worker
        self.worker = None
        worker.deleteLater()
        self._render_state()
        if self._close_after_worker:
            QTimer.singleShot(0, self.close)
        elif self._after_worker:
            operation, self._after_worker = self._after_worker, None
            QTimer.singleShot(0, operation)
        else:
            QTimer.singleShot(0, self._check_due)
            if self._estimate_pending:
                self._estimate_timer.start(0)

    def _toggle_details(self, visible):
        self.details_button.setChecked(visible)

    def _browse_input(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose course material", "", "Course material (*.pdf *.pptx)")
        if path:
            self._set_input_file(path)

    def _set_input_file(self, path):
        if self._busy():
            return
        if not os.path.isfile(path) or not validate_file_type(path):
            self.status.setText("Choose an existing PDF or PPTX file.")
            return
        self.input_file, self.task_id, self.last_result = path, None, None
        self._task_context = None
        self._clear_preview()
        self.drop_zone.set_file(path)
        self.status.setText("Ready · free requests first")
        self._render_state()
        self._schedule_estimate()

    def _clear_input(self):
        if not self._busy():
            self.input_file = self.task_id = self.last_result = None
            self._task_context = None
            self._clear_preview()
            self._schedule_estimate()
            self._render_state()

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "Choose output folder", self.output_path.text().strip())
        if path:
            self.output_path.setText(path)

    def _show_settings(self):
        if self._busy():
            return
        dialog = SettingsDialog(self._config, self)
        dialog.cache_changed.connect(self._schedule_estimate)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._config.update(dialog.values())
            self._save_config()
            self._update_notion_state()
            self.status.setText("Settings saved. Test the image route on each network you use.")
            self._render_state()
            self._schedule_estimate(refresh=True)

    def _update_notion_state(self):
        self.notion_mode.setEnabled(self.publish_notion.isChecked() and not self._busy())
        self.notion_status.setText("Configured" if self._config.get("notion_page_id") else "Configure in Settings")

    def _save_config(self):
        self._config.update({"output_dir": self.output_path.text().strip(), "output_format": self.format_combo.currentData(),
                             "language": self.language_combo.currentData(), "include_source_pages": self.include_source_pages.isChecked(),
                             "notion_mode": self.notion_mode.currentData() if self.publish_notion.isChecked() else "none"})
        try:
            save_config(self._config, self.CONFIG_FILE)
        except OSError as exc:
            self.status.setText(f"Could not save settings: {exc}")

    def _open_output(self):
        if self.last_result and self.last_result.output_path:
            if not os.path.isfile(self.last_result.output_path):
                self.status.setText("Saved file was moved or deleted. Export the accepted notes to save a new copy.")
                self._render_state()
            elif not QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_result.output_path)):
                self.status.setText("Could not open the saved file. Check its default application.")

    def _open_notion(self):
        if self.last_result and self.last_result.notion:
            QDesktopServices.openUrl(QUrl(self.last_result.notion.url))

    def closeEvent(self, event):
        if self._closed:
            event.accept()
            return
        if self._busy():
            if not self._close_after_worker:
                answer = QMessageBox.question(self, "Close NoteAgent?", "Stop processing and close after the current request exits? Completed pages stay saved.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                if answer == QMessageBox.StandardButton.Yes:
                    self._close_after_worker = True
                    self._cancel_generation()
            event.ignore()
            return
        if self._estimate_worker is not None:
            self._close_after_estimate = True
            self._estimate_timer.stop()
            self._estimate_worker.requestInterruption()
            self.status.setText("Closing after the allowance check stops…")
            event.ignore()
            return
        self._save_config()
        self._closed = True
        self._preview_timer.stop()
        self._retry_timer.stop()
        self._estimate_timer.stop()
        self.activity.stop_animation()
        self.estimate_spinner.set_state(False)
        if self.tray_icon is not None:
            self.tray_icon.hide()
        if self._owns_lock:
            self._lock.unlock()
            self._owns_lock = False
        event.accept()
