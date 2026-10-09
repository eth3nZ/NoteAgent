"""Responsive desktop UI for NoteAgent."""

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.notion import NotionClient
from app.core.pipeline import Pipeline, PipelineCancelled, PipelineResult
from app.core.vision.vision_client import VisionClient
from app.utils.helpers import load_config, save_config, validate_file_type


APP_STYLE = """
QMainWindow, QWidget#root { background: #F5F6F8; color: #1F2328; }
QFrame#card {
    background: white;
    border: 1px solid #E4E7EC;
    border-radius: 14px;
}
QLabel#muted { color: #667085; }
QLabel#sectionTitle { font-size: 13px; font-weight: 650; color: #344054; }
QLineEdit, QComboBox, QTextEdit {
    background: white;
    color: #1F2328;
    border: 1px solid #D0D5DD;
    border-radius: 8px;
    padding: 7px 10px;
    selection-background-color: #1570EF;
}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus { border-color: #1570EF; }
QPushButton {
    min-height: 34px;
    padding: 0 15px;
    border-radius: 8px;
    border: 1px solid #D0D5DD;
    background: white;
    color: #344054;
    font-weight: 550;
}
QPushButton:hover { background: #F9FAFB; border-color: #98A2B3; }
QPushButton#primary {
    min-height: 42px;
    color: white;
    border: none;
    background: #1570EF;
    font-size: 14px;
    font-weight: 650;
}
QPushButton#primary:hover { background: #175CD3; }
QPushButton#primary:disabled { background: #98A2B3; }
QPushButton#danger { color: #B42318; border-color: #FDA29B; }
QProgressBar { border: none; background: #EAECF0; border-radius: 2px; }
QProgressBar::chunk { background: #1570EF; border-radius: 2px; }
QTabWidget::pane { border: 1px solid #E4E7EC; border-radius: 8px; background: white; }
QTabBar::tab { padding: 8px 15px; color: #667085; }
QTabBar::tab:selected { color: #1570EF; font-weight: 650; }
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
        self.icon = QLabel("⇩")
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setStyleSheet("font-size: 27px; color: #1570EF; border: none;")
        self.label = QLabel("Drop a PDF or PPTX here, or click to browse")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("font-size: 14px; font-weight: 600; border: none;")
        self.detail = QLabel("Slides are rendered and analyzed page by page")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail.setStyleSheet("color: #667085; border: none;")
        layout.addWidget(self.icon)
        layout.addWidget(self.label)
        layout.addWidget(self.detail)

    def _style(self, active: bool) -> str:
        background = "#EFF8FF" if active else "#FFFFFF"
        border = "#1570EF" if active else "#B2CCFF"
        return (
            f"QFrame#dropZone {{ background: {background}; border: 2px dashed {border}; "
            "border-radius: 14px; }"
        )

    def set_file(self, path: str) -> None:
        self.icon.setText("✓")
        self.label.setText(Path(path).name)
        size_mb = os.path.getsize(path) / (1024 * 1024)
        self.detail.setText(f"{size_mb:.1f} MB · click to choose another file")
        self.setStyleSheet(self._style(True))

    def clear_file(self) -> None:
        self.icon.setText("⇩")
        self.label.setText("Drop a PDF or PPTX here, or click to browse")
        self.detail.setText("Slides are rendered and analyzed page by page")
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


class SettingsDialog(QDialog):
    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connections")
        self.setMinimumSize(600, 500)
        self._config = dict(config)
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        intro = QLabel("Choose a free hosted model or run Qwen locally with Ollama.")
        intro.setObjectName("muted")
        root.addWidget(intro)

        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        model_tab = QWidget()
        model_form = QFormLayout(model_tab)
        model_form.setContentsMargins(18, 18, 18, 18)
        model_form.setSpacing(12)
        self.provider = QComboBox()
        self.provider.addItem("OpenRouter · free hosted", "openrouter")
        self.provider.addItem("Ollama · free local", "ollama")
        self.provider.addItem("Custom OpenAI-compatible", "custom")
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("sk-or-v1-… (not needed for local Ollama)")
        self.base_url = QLineEdit()
        self.vision_model = QLineEdit()
        self.text_model = QLineEdit()
        model_form.addRow("Provider", self.provider)
        model_form.addRow("API key", self.api_key)
        model_form.addRow("Base URL", self.base_url)
        model_form.addRow("Vision model", self.vision_model)
        model_form.addRow("Rewrite model", self.text_model)
        model_help = QLabel(
            '<a href="https://openrouter.ai/settings/keys">Create an OpenRouter key</a> · '
            '<a href="https://ollama.com/library/qwen3-vl">Ollama Qwen3-VL setup</a>'
        )
        model_help.setOpenExternalLinks(True)
        model_help.setObjectName("muted")
        model_form.addRow("", model_help)
        tabs.addTab(model_tab, "Model")

        notion_tab = QWidget()
        notion_form = QFormLayout(notion_tab)
        notion_form.setContentsMargins(18, 18, 18, 18)
        notion_form.setSpacing(12)
        self.notion_token = QLineEdit()
        self.notion_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.notion_token.setPlaceholderText("ntn_… or secret_…")
        self.notion_page = QLineEdit()
        self.notion_page.setPlaceholderText("Paste a Notion page URL or page ID")
        notion_form.addRow("Access token", self.notion_token)
        notion_form.addRow("Page / parent", self.notion_page)
        notion_help = QLabel(
            "Create a Notion connection with Insert and Update Content access, then add "
            "that connection to the target page."
        )
        notion_help.setWordWrap(True)
        notion_help.setObjectName("muted")
        notion_form.addRow("", notion_help)
        notion_link = QLabel(
            '<a href="https://www.notion.com/help/create-integrations-with-the-notion-api">Notion connection instructions</a>'
        )
        notion_link.setOpenExternalLinks(True)
        notion_form.addRow("", notion_link)
        tabs.addTab(notion_tab, "Notion")

        note = QLabel("Credentials are stored locally in a user-only configuration file.")
        note.setObjectName("muted")
        root.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.provider.currentIndexChanged.connect(self._provider_changed)

    def _load(self) -> None:
        self.provider.blockSignals(True)
        index = self.provider.findData(self._config.get("provider", "openrouter"))
        self.provider.setCurrentIndex(max(0, index))
        self.provider.blockSignals(False)
        self.api_key.setText(self._config.get("api_key", ""))
        self.base_url.setText(
            self._config.get("base_url", VisionClient.DEFAULT_BASE_URL)
        )
        self.vision_model.setText(
            self._config.get("vision_model", VisionClient.DEFAULT_MODEL)
        )
        self.text_model.setText(
            self._config.get("llm_model", VisionClient.DEFAULT_MODEL)
        )
        self.notion_token.setText(self._config.get("notion_token", ""))
        self.notion_page.setText(self._config.get("notion_page_id", ""))
        self._update_key_state()

    def _provider_changed(self) -> None:
        provider = self.provider.currentData()
        if provider == "openrouter":
            self.base_url.setText(VisionClient.DEFAULT_BASE_URL)
            self.vision_model.setText(VisionClient.DEFAULT_MODEL)
            self.text_model.setText(VisionClient.DEFAULT_MODEL)
        elif provider == "ollama":
            self.base_url.setText(VisionClient.LOCAL_DEFAULT_BASE_URL)
            self.vision_model.setText(VisionClient.LOCAL_DEFAULT_MODEL)
            self.text_model.setText(VisionClient.LOCAL_DEFAULT_MODEL)
        self._update_key_state()

    def _update_key_state(self) -> None:
        local = self.provider.currentData() == "ollama"
        self.api_key.setEnabled(not local)
        if local:
            self.api_key.setPlaceholderText("Not required for local Ollama")
        else:
            self.api_key.setPlaceholderText("sk-or-v1-…")

    def values(self) -> dict:
        return {
            "provider": self.provider.currentData(),
            "api_key": self.api_key.text().strip(),
            "base_url": self.base_url.text().strip(),
            "vision_model": self.vision_model.text().strip(),
            "llm_model": self.text_model.text().strip(),
            "notion_token": self.notion_token.text().strip(),
            "notion_page_id": self.notion_page.text().strip(),
        }


class WorkerThread(QThread):
    progress = pyqtSignal(str)
    stream = pyqtSignal(str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(
        self,
        pipeline: Pipeline,
        input_path: str,
        output_dir: str,
        output_format: str,
        language: str,
        notion_mode: str,
        notion_token: str,
        notion_page_id: str,
    ):
        super().__init__()
        self.pipeline = pipeline
        self.input_path = input_path
        self.output_dir = output_dir
        self.output_format = output_format
        self.language = language
        self.notion_mode = notion_mode
        self.notion_token = notion_token
        self.notion_page_id = notion_page_id
        self._stream_buffer = ""

    def run(self) -> None:
        try:
            notion_client = (
                NotionClient(self.notion_token) if self.notion_mode != "none" else None
            )
            result = self.pipeline.run(
                input_path=self.input_path,
                output_dir=self.output_dir,
                output_format=self.output_format,
                language=self.language,
                progress_callback=self.progress.emit,
                stream_callback=self._buffer_stream,
                cancel_callback=self.isInterruptionRequested,
                notion_client=notion_client,
                notion_page_id=self.notion_page_id,
                notion_mode=self.notion_mode,
            )
            self._flush_stream()
            self.succeeded.emit(result)
        except PipelineCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))

    def _buffer_stream(self, text: str) -> None:
        self._stream_buffer += text
        if len(self._stream_buffer) >= 100 or "\n" in self._stream_buffer:
            self._flush_stream()

    def _flush_stream(self) -> None:
        if self._stream_buffer:
            self.stream.emit(self._stream_buffer)
            self._stream_buffer = ""


class MainWindow(QMainWindow):
    CONFIG_FILE = str(Path.home() / ".noteagent_config.json")

    def __init__(self):
        super().__init__()
        self.input_file: Optional[str] = None
        self.worker: Optional[WorkerThread] = None
        self.last_result: Optional[PipelineResult] = None
        self._close_after_worker = False
        self._config = self._default_config()
        self._config.update(load_config(self.CONFIG_FILE))
        self._build_ui()
        self._restore_ui_config()
        self.setStyleSheet(APP_STYLE)

    def _default_config(self) -> dict:
        return {
            "provider": "openrouter",
            "api_key": "",
            "base_url": VisionClient.DEFAULT_BASE_URL,
            "vision_model": VisionClient.DEFAULT_MODEL,
            "llm_model": VisionClient.DEFAULT_MODEL,
            "notion_token": "",
            "notion_page_id": "",
            "output_dir": str(Path.home() / "Desktop" / "NoteAgent_Output"),
            "output_format": "md",
            "language": "zh",
            "notion_mode": "none",
        }

    def _build_ui(self) -> None:
        self.setWindowTitle("NoteAgent")
        self.setMinimumSize(820, 680)
        self.resize(940, 780)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 16, 24, 12)
        brand = QVBoxLayout()
        title = QLabel("NoteAgent")
        title.setStyleSheet("font-size: 22px; font-weight: 750;")
        subtitle = QLabel("Turn PDF and PowerPoint material into useful notes")
        subtitle.setObjectName("muted")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        header_layout.addLayout(brand)
        header_layout.addStretch()
        settings = QPushButton("Connections")
        settings.clicked.connect(self._show_settings)
        header_layout.addWidget(settings)
        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 8, 24, 24)
        content_layout.setSpacing(14)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self.drop_zone = DropZone()
        self.drop_zone.file_requested.connect(self._browse_input)
        self.drop_zone.file_selected.connect(self._set_input_file)
        self.drop_zone.file_cleared.connect(self._clear_input)
        content_layout.addWidget(self.drop_zone)

        options = self._card()
        options_layout = QHBoxLayout(options)
        options_layout.setContentsMargins(16, 14, 16, 14)
        options_layout.setSpacing(14)
        self.format_combo = QComboBox()
        self.format_combo.addItem("Markdown (.md)", "md")
        self.format_combo.addItem("Jupyter Notebook (.ipynb)", "ipynb")
        self.language_combo = QComboBox()
        self.language_combo.addItem("中文", "zh")
        self.language_combo.addItem("English", "en")
        options_layout.addLayout(self._labeled("Local format", self.format_combo), 1)
        options_layout.addLayout(self._labeled("Output language", self.language_combo), 1)
        content_layout.addWidget(options)

        output_card = self._card()
        output_layout = QVBoxLayout(output_card)
        output_layout.setContentsMargins(16, 14, 16, 14)
        output_layout.setSpacing(8)
        output_layout.addWidget(self._section_label("Save local copy"))
        path_row = QHBoxLayout()
        self.output_path = QLineEdit()
        browse_output = QPushButton("Browse")
        browse_output.clicked.connect(self._browse_output)
        path_row.addWidget(self.output_path, 1)
        path_row.addWidget(browse_output)
        output_layout.addLayout(path_row)
        content_layout.addWidget(output_card)

        notion_card = self._card()
        notion_layout = QHBoxLayout(notion_card)
        notion_layout.setContentsMargins(16, 14, 16, 14)
        self.publish_notion = QCheckBox("Also publish to Notion")
        self.notion_mode = QComboBox()
        self.notion_mode.addItem("Create a new note inside the configured page", "create_child")
        self.notion_mode.addItem("Append to the configured page", "append")
        self.notion_status = QLabel()
        self.notion_status.setObjectName("muted")
        notion_layout.addWidget(self.publish_notion)
        notion_layout.addWidget(self.notion_mode, 1)
        notion_layout.addWidget(self.notion_status)
        self.publish_notion.toggled.connect(self._update_notion_state)
        content_layout.addWidget(notion_card)

        action_row = QHBoxLayout()
        self.generate_button = QPushButton("Generate notes")
        self.generate_button.setObjectName("primary")
        self.generate_button.clicked.connect(self._start_generation)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("danger")
        self.cancel_button.clicked.connect(self._cancel_generation)
        self.cancel_button.hide()
        action_row.addWidget(self.generate_button, 1)
        action_row.addWidget(self.cancel_button)
        content_layout.addLayout(action_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        content_layout.addWidget(self.progress_bar)

        self.output_tabs = QTabWidget()
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Generated note text will stream here.")
        self.output_tabs.addTab(self.log, "Progress")
        self.output_tabs.addTab(self.preview, "Live preview")
        self.output_tabs.setMinimumHeight(180)
        content_layout.addWidget(self.output_tabs)

        result_row = QHBoxLayout()
        self.open_file_button = QPushButton("Open output")
        self.open_file_button.clicked.connect(self._open_output)
        self.open_file_button.hide()
        self.open_notion_button = QPushButton("Open in Notion")
        self.open_notion_button.clicked.connect(self._open_notion)
        self.open_notion_button.hide()
        result_row.addStretch()
        result_row.addWidget(self.open_file_button)
        result_row.addWidget(self.open_notion_button)
        content_layout.addLayout(result_row)

    def _card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        return card

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _labeled(self, label: str, widget: QWidget) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(6)
        layout.addWidget(self._section_label(label))
        layout.addWidget(widget)
        return layout

    def _restore_ui_config(self) -> None:
        self.output_path.setText(self._config.get("output_dir", ""))
        format_index = self.format_combo.findData(self._config.get("output_format", "md"))
        self.format_combo.setCurrentIndex(max(0, format_index))
        language_index = self.language_combo.findData(self._config.get("language", "zh"))
        self.language_combo.setCurrentIndex(max(0, language_index))
        saved_mode = self._config.get("notion_mode", "none")
        self.publish_notion.setChecked(saved_mode != "none")
        mode_index = self.notion_mode.findData(
            saved_mode if saved_mode != "none" else "create_child"
        )
        self.notion_mode.setCurrentIndex(max(0, mode_index))
        self._update_notion_state()

    def _browse_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose course material",
            "",
            "Course material (*.pdf *.pptx);;PDF (*.pdf);;PowerPoint (*.pptx)",
        )
        if path:
            self._set_input_file(path)

    def _set_input_file(self, path: str) -> None:
        if not os.path.isfile(path) or not validate_file_type(path):
            QMessageBox.warning(self, "Unsupported file", "Choose a PDF or PPTX file.")
            return
        self.input_file = path
        self.drop_zone.set_file(path)

    def _clear_input(self) -> None:
        self.input_file = None

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Choose output folder", self.output_path.text().strip()
        )
        if path:
            self.output_path.setText(path)

    def _show_settings(self) -> None:
        dialog = SettingsDialog(self._config, self)
        dialog.setStyleSheet(APP_STYLE)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._config.update(dialog.values())
            self._save_config()
            self._update_notion_state()
            self.log.append("Connections saved.")

    def _update_notion_state(self) -> None:
        enabled = self.publish_notion.isChecked()
        self.notion_mode.setEnabled(enabled)
        page_configured = bool(self._config.get("notion_page_id", "").strip())
        self.notion_status.setText("Configured" if page_configured else "Set up in Connections")

    def _start_generation(self) -> None:
        if not self.input_file:
            QMessageBox.warning(self, "Choose a file", "Select a PDF or PPTX first.")
            return
        output_dir = self.output_path.text().strip()
        if not output_dir:
            QMessageBox.warning(self, "Choose a folder", "Select a local output folder.")
            return

        api_key = self._config.get("api_key", "") or os.environ.get("OPENROUTER_API_KEY", "")
        try:
            model_client = VisionClient(
                api_key=api_key,
                base_url=self._config.get("base_url", VisionClient.DEFAULT_BASE_URL),
                model=self._config.get("vision_model", VisionClient.DEFAULT_MODEL),
                llm_model=self._config.get("llm_model", VisionClient.DEFAULT_MODEL),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Model setup needed", str(exc))
            self._show_settings()
            return

        notion_mode = self.notion_mode.currentData() if self.publish_notion.isChecked() else "none"
        notion_token = self._config.get("notion_token", "") or os.environ.get("NOTION_TOKEN", "")
        notion_page_id = self._config.get("notion_page_id", "")
        if notion_mode != "none" and (notion_token == "" or notion_page_id.strip() == ""):
            QMessageBox.warning(
                self,
                "Notion setup needed",
                "Add a Notion token and page URL in Connections, or turn off Notion publishing.",
            )
            return

        self._config.update(
            {
                "output_dir": output_dir,
                "output_format": self.format_combo.currentData(),
                "language": self.language_combo.currentData(),
                "notion_mode": notion_mode,
            }
        )
        self._save_config()

        self.log.clear()
        self.preview.clear()
        self.output_tabs.setCurrentWidget(self.log)
        self.open_file_button.hide()
        self.open_notion_button.hide()
        self.last_result = None
        self._set_running(True)

        self.worker = WorkerThread(
            pipeline=Pipeline(model_client),
            input_path=self.input_file,
            output_dir=output_dir,
            output_format=self.format_combo.currentData(),
            language=self.language_combo.currentData(),
            notion_mode=notion_mode,
            notion_token=notion_token,
            notion_page_id=notion_page_id,
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.stream.connect(self._on_stream)
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_failure)
        self.worker.cancelled.connect(self._on_cancelled)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _set_running(self, running: bool) -> None:
        self.generate_button.setEnabled(not running)
        self.generate_button.setText("Generating…" if running else "Generate notes")
        self.cancel_button.setVisible(running)
        self.cancel_button.setEnabled(running)
        self.cancel_button.setText("Cancel")
        self.progress_bar.setVisible(running)

    def _cancel_generation(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.cancel_button.setEnabled(False)
            self.cancel_button.setText("Stopping…")
            self.log.append("Cancellation requested; the current model request may finish first.")

    def _on_progress(self, message: str) -> None:
        self.log.append(message)
        scrollbar = self.log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_stream(self, text: str) -> None:
        cursor = self.preview.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.preview.setTextCursor(cursor)
        self.preview.ensureCursorVisible()

    def _on_success(self, result: PipelineResult) -> None:
        self.last_result = result
        self.log.append(
            f"\nDone: {result.page_count} pages · quality check {result.score}/100 · "
            f"{result.iterations} generation pass(es)"
        )
        self.log.append(result.output_path)
        for warning in result.warnings:
            self.log.append(f"Warning: {warning}")
        if result.notion:
            self.log.append(result.notion.url)
            self.open_notion_button.show()
        self.open_file_button.show()

        summary = f"Notes saved to:\n{result.output_path}"
        if result.notion:
            summary += "\n\nThe Notion page was updated successfully."
        if result.warnings:
            summary += f"\n\nCompleted with {len(result.warnings)} warning(s); see Progress."
        QMessageBox.information(self, "Notes ready", summary)

    def _on_failure(self, message: str) -> None:
        self.log.append(f"\nError: {message}")
        QMessageBox.critical(self, "Generation failed", message)

    def _on_cancelled(self) -> None:
        self.log.append("\nCancelled. No partial output was overwritten.")

    def _worker_finished(self) -> None:
        self._set_running(False)
        if self._close_after_worker:
            self._close_after_worker = False
            QTimer.singleShot(0, self.close)

    def _open_output(self) -> None:
        if self.last_result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_result.output_path))

    def _open_notion(self) -> None:
        if self.last_result and self.last_result.notion:
            QDesktopServices.openUrl(QUrl(self.last_result.notion.url))

    def _save_config(self) -> None:
        if hasattr(self, "output_path"):
            self._config.update(
                {
                    "output_dir": self.output_path.text().strip(),
                    "output_format": self.format_combo.currentData(),
                    "language": self.language_combo.currentData(),
                    "notion_mode": (
                        self.notion_mode.currentData()
                        if self.publish_notion.isChecked()
                        else "none"
                    ),
                }
            )
        try:
            save_config(self._config, self.CONFIG_FILE)
        except OSError as exc:
            self.log.append(f"Could not save settings: {exc}")

    def closeEvent(self, event) -> None:
        self._save_config()
        if self.worker and self.worker.isRunning():
            answer = QMessageBox.question(
                self,
                "Generation is still running",
                "Cancel the current job and close when it stops?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._close_after_worker = True
                self._cancel_generation()
            event.ignore()
            return
        event.accept()
