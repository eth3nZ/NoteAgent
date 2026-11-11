"""Small settings UI with asynchronous catalog loading and explicit paid opt-in."""

import math
from pathlib import Path
from urllib.parse import urlparse

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox,
                            QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                            QPushButton, QSizePolicy, QTabWidget, QVBoxLayout, QWidget,
                            QFrame, QScrollArea, QStackedWidget, QStyle, QToolButton)

from app.core.cloud import BASE_URL, FREE_MODEL, list_note_models
from app.core.configuration import make_client
from app.core.maintenance import clean_storage, format_bytes, storage_info
from app.ui.workers import TaskWorker


class ModelCombo(QComboBox):
    """Show names/prices while saving the stable API ID; manual IDs remain supported."""
    def __init__(self, model_id):
        super().__init__()
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(16)
        self.setMaxVisibleItems(12)
        self.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.set_model_id(model_id)

    def model_id(self):
        index = self.currentIndex()
        if index >= 0 and self.currentText() == self.itemText(index):
            return self.itemData(index) or self.currentText().strip()
        return self.currentText().strip()

    def set_model_id(self, model_id):
        index = self.findData(model_id)
        if index < 0:
            self.addItem(model_id, model_id)
            index = self.count() - 1
        self.setCurrentIndex(index)
        self.lineEdit().setCursorPosition(0)
        self.setToolTip(self.itemData(index, Qt.ItemDataRole.ToolTipRole) or model_id)

    def set_models(self, models):
        saved = self.model_id()
        self.blockSignals(True)
        self.clear()
        for model in models:
            price = "Free" if model.free else f"${model.input_price:g} / ${model.output_price:g}"
            self.addItem(f"{model.name} · {price}", model.id)
            self.setItemData(self.count() - 1, f"{model.id}\n{model.price_label}\nContext: {model.context_length:,} tokens", Qt.ItemDataRole.ToolTipRole)
        if saved and self.findData(saved) < 0:
            self.addItem(f"{saved} · saved, not listed", saved)
        if saved:
            self.set_model_id(saved)
        self.blockSignals(False)


SETTINGS_STYLE = """
QDialog#settingsDialog { background: #F4F2ED; color: #20231F; }
QWidget#settingsContent, QScrollArea#settingsScroll { background: transparent; border: none; }
QFrame#settingsCard { background: #FFFEFB; border: 1px solid #DEDCD5; border-radius: 10px; }
QLabel { color: #20231F; background: transparent; border: none; }
QLabel#settingsCaption { color: #73756F; font-size: 12px; }
QLabel#settingsHeading { font-size: 20px; font-weight: 650; }
QLineEdit, QComboBox, QDoubleSpinBox {
    color: #20231F; background: #FFFEFB; border: 1px solid #CFCDC5;
    border-radius: 7px; padding: 7px 9px; min-height: 22px;
    selection-background-color: #49685A;
}
QComboBox QLineEdit { border: none; padding: 0; min-height: 0; background: transparent; }
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus { border-color: #49685A; }
QPushButton { padding: 6px 16px; min-height: 24px; border: 1px solid #CFCDC5;
    border-radius: 7px; background: #FFFEFB; color: #343731; }
QPushButton:hover { background: #F7F5EF; }
QPushButton#saveSettings { background: #2F5545; border-color: #2F5545; color: white; font-weight: 600; }
QPushButton:disabled, QToolButton:disabled { color: #A0A29B; }
QToolButton#advancedToggle { border: none; background: transparent; color: #49685A; padding: 8px 0; }
QToolButton#refreshModels { border: 1px solid #CFCDC5; border-radius: 7px; padding: 8px; background: #FFFEFB; }
QCheckBox { spacing: 8px; padding: 2px 0; }
QTabWidget#settingsTabs::pane { border: none; background: transparent; }
QTabBar::tab { color: #73756F; padding: 9px 17px; background: transparent; border: none; }
QTabBar::tab:selected { color: #2F5545; border-bottom: 2px solid #2F5545; }
"""


class SettingsDialog(QDialog):
    cache_changed = pyqtSignal()

    def __init__(self, config, parent=None, auto_refresh=True):
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("Settings")
        self.setMinimumSize(580, 430)
        self.resize(660, 680 if config.get("paid_enabled") or config.get("provider") == "custom" else 590)
        self.setStyleSheet(SETTINGS_STYLE)
        self._config = dict(config)
        self.worker = None
        self._closing = self._disposed = False
        self._auto_refresh = auto_refresh
        self._pending_catalog = set()
        self._catalog_cache = None
        self._pending_storage = False
        self.store_path = Path(getattr(parent, 'store_path', Path.home()/'.noteagent'/'tasks.sqlite3'))
        self._protected_task_id = getattr(parent, 'task_id', None)
        self._models = {}
        self._paid_extra_prices = (float(config.get("paid_image_price", 0)), float(config.get("paid_request_price", 0)))
        self._service = "openrouter" if config.get("provider") == "openrouter" else "custom"
        self._service_drafts = {self._service: {key: config.get(key, "") for key in
                                               ("api_key", "base_url", "vision_model", "free_providers")}}
        self._last_models = {key: config.get(key, "") for key in ("vision_model", "paid_model")}
        self.fields = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 16)
        root.setSpacing(12)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("settingsTabs")
        self.tabs.tabBar().setDrawBase(False)
        root.addWidget(self.tabs, 1)

        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("settingsContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(2, 14, 2, 2)
        content_layout.setSpacing(12)
        heading = self._label("Your models")
        heading.setObjectName("settingsHeading")
        content_layout.addWidget(heading)
        self.service_help = self._label("")
        content_layout.addWidget(self.service_help)

        card = self._card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(12)
        form = QFormLayout()
        self.connection_form = form
        form.setSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.provider = QComboBox()
        self.provider.addItem("OpenRouter", "openrouter")
        self.provider.addItem("Custom endpoint", "custom")
        self.provider.setCurrentIndex(0 if self._service == "openrouter" else 1)
        form.addRow("Service", self.provider)
        self._text(form, "api_key", "OpenRouter key", secret=True)
        self.fields["api_key"].setPlaceholderText("Paste your API key")
        self._model_row(form, "vision_model", "Free model", paid=False)
        self.model_note = self._label("Browse models first; add a key when you're ready to generate.")
        form.addRow(self.model_note)
        card_layout.addLayout(form)

        self.paid_enabled = QCheckBox("Use a paid backup")
        self.paid_enabled.setToolTip("After the next day's free retry fails, you can confirm a paid continuation within your budgets.")
        self.paid_enabled.setChecked(bool(config.get("paid_enabled")))
        card_layout.addWidget(self.paid_enabled)
        self.paid_body = QWidget()
        paid_layout = QVBoxLayout(self.paid_body)
        paid_layout.setContentsMargins(0, 0, 0, 0)
        paid_layout.setSpacing(8)
        paid_form = QFormLayout()
        paid_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._model_row(paid_form, "paid_model", "Backup model", paid=True)
        self.paid_model_note = self._label("Uses the same OpenRouter key.")
        paid_form.addRow(self.paid_model_note)
        paid_layout.addLayout(paid_form)
        budgets = QHBoxLayout()
        budgets.setSpacing(12)
        for key, label, default in (("document_limit", "Per document", .10), ("monthly_limit", "Per month", 1.0)):
            column = QFormLayout()
            self._number(column, key, label, default, 2)
            self.fields[key].setPrefix("$ ")
            if key == "monthly_limit":
                self.fields[key].setToolTip("Calendar month in UTC; covers requests sent by this app.")
            budgets.addLayout(column, 1)
        paid_layout.addLayout(budgets)
        self.paid_help = self._label("")
        paid_layout.addWidget(self.paid_help)
        card_layout.addWidget(self.paid_body)

        test_row = QHBoxLayout()
        self.test_connection = QPushButton("Test connection")
        self.test_connection.setToolTip("Checks image input with the free profile. Paid backup is never used for this test.")
        self.test_connection.clicked.connect(self._test_model_connection)
        test_row.addWidget(self.test_connection)
        test_row.addStretch()
        card_layout.addLayout(test_row)
        self.test_status = self._label("")
        self.test_status.setVisible(False)
        card_layout.addWidget(self.test_status)
        content_layout.addWidget(card)

        advanced_holder = QFormLayout()
        advanced_holder.setContentsMargins(0, 0, 0, 0)
        self.primary_advanced_button, self.primary_advanced, self.primary_form = self._advanced(advanced_holder)
        content_layout.addLayout(advanced_holder)
        self._text(self.primary_form, "base_url", "Base URL")
        self._text(self.primary_form, "free_providers", "Free model hosts")
        self.fields["free_providers"].setPlaceholderText("Automatic")
        self.fields["free_providers"].setToolTip("Optional comma-separated OpenRouter provider slugs. Empty lets OpenRouter choose a host for the selected free model.")
        self.paid_advanced = QWidget()
        advanced_paid_form = QFormLayout(self.paid_advanced)
        advanced_paid_form.setContentsMargins(0, 0, 0, 0)
        advanced_paid_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._text(advanced_paid_form, "paid_api_key", "Separate backup key", secret=True)
        self.fields["paid_api_key"].setPlaceholderText("Use the main OpenRouter key")
        self._text(advanced_paid_form, "paid_providers", "Backup model hosts")
        self.fields["paid_providers"].setPlaceholderText("Automatic")
        self.fields["paid_providers"].setToolTip("Optional hosting-provider filter for the paid backup only.")
        self._number(advanced_paid_form, "input_price", "Input ceiling ($ / 1M)", .035, 6)
        self._number(advanced_paid_form, "output_price", "Output ceiling ($ / 1M)", 1.5, 6)
        self.primary_form.addRow(self.paid_advanced)
        self.primary_advanced_button.setChecked(self._service == "custom")
        content_layout.addStretch()
        scroll.setWidget(content)
        self.tabs.addTab(scroll, "Models")

        notion_tab = QWidget()
        notion_layout = QVBoxLayout(notion_tab)
        notion_layout.setContentsMargins(2, 14, 2, 2)
        notion_layout.setSpacing(12)
        heading = self._label("Send notes to Notion")
        heading.setObjectName("settingsHeading")
        notion_layout.addWidget(heading)
        notion_layout.addWidget(self._label("Connect an integration to the page where your notes belong."))
        notion_card = self._card()
        notion_form = QFormLayout(notion_card)
        notion_form.setContentsMargins(16, 14, 16, 14)
        notion_form.setSpacing(10)
        notion_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._text(notion_form, "notion_token", "Integration token", secret=True)
        self._text(notion_form, "notion_page_id", "Page URL")
        self.fields["notion_page_id"].setPlaceholderText("Paste a Notion page link")
        notion_layout.addWidget(notion_card)
        notion_layout.addStretch()
        self.tabs.addTab(notion_tab, "Notion")
        self.storage_tab = QWidget()
        storage_layout = QVBoxLayout(self.storage_tab)
        storage_layout.setContentsMargins(2, 14, 2, 2)
        storage_layout.setSpacing(12)
        heading = self._label("Local cache")
        heading.setObjectName("settingsHeading")
        storage_layout.addWidget(heading)
        storage_layout.addWidget(self._label("Old completed caches with saved outputs expire after 30 unused days. The current task, unfinished notes and pending retries are kept."))
        storage_card = self._card()
        cache_layout = QVBoxLayout(storage_card)
        cache_layout.setContentsMargins(16, 14, 16, 14)
        self.cache_summary = QLabel("Cached notes: —")
        cache_layout.addWidget(self.cache_summary)
        self.cache_reclaimable = self._label("Check reusable and expired notes here.")
        cache_layout.addWidget(self.cache_reclaimable)
        self.clean_cache_button = QPushButton("Clean unused cache")
        self.clean_cache_button.setToolTip("Expired caches may need model requests again if you regenerate them. Existing exported files are kept.")
        self.clean_cache_button.clicked.connect(lambda: self._load_storage(clean=True))
        cache_layout.addWidget(self.clean_cache_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.storage_status = self._label("")
        cache_layout.addWidget(self.storage_status)
        storage_layout.addWidget(storage_card)
        storage_layout.addWidget(self._label("Saved human revisions, their source-page notes, exported files, Notion pages and billing history are kept. Old temporary rendering files are removed only after their owning process has exited."))
        storage_layout.addStretch()
        self.tabs.addTab(self.storage_tab, "Storage")
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("saveSettings")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.paid_enabled.toggled.connect(self._paid_changed)
        self.fields["api_key"].editingFinished.connect(self._auto_load)
        self.fields["paid_api_key"].editingFinished.connect(lambda: self._auto_load(paid=True))
        self.tabs.currentChanged.connect(self._storage_selected)
        self._update_controls()
        if auto_refresh:
            QTimer.singleShot(0, self._auto_load)

    @staticmethod
    def _card():
        card = QFrame()
        card.setObjectName("settingsCard")
        return card

    @staticmethod
    def _label(text):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setObjectName("settingsCaption")
        return label

    def _text(self, form, key, label, secret=False):
        widget = QLineEdit(str(self._config.get(key, "")))
        if secret:
            widget.setEchoMode(QLineEdit.EchoMode.Password)
        self.fields[key] = widget
        form.addRow(label, widget)

    def _model_row(self, form, key, label, paid):
        row = QHBoxLayout()
        combo = ModelCombo(self._config.get(key, ""))
        button = QToolButton()
        button.setObjectName("refreshModels")
        button.setAccessibleName("Refresh models")
        button.setToolTip("Refresh models")
        button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        button.clicked.connect(lambda checked=False: self._load_catalog(paid, force=True))
        combo.activated.connect(lambda index: self._model_changed(paid))
        combo.lineEdit().editingFinished.connect(lambda: self._model_changed(paid))
        self.fields[key] = combo
        if paid:
            self.paid_refresh = button
        else:
            self.primary_refresh = button
        if paid:
            row.addWidget(combo, 1)
        else:
            self.primary_combo = combo
            self.custom_model = QLineEdit(str(self._config.get(key, "")))
            self.custom_model.setPlaceholderText("Model ID from your service")
            self.custom_model.editingFinished.connect(lambda: self._model_changed(False))
            self.primary_stack = QStackedWidget()
            self.primary_stack.addWidget(combo)
            self.primary_stack.addWidget(self.custom_model)
            if self._service == "custom":
                self.fields[key] = self.custom_model
                self.primary_stack.setCurrentIndex(1)
            row.addWidget(self.primary_stack, 1)
        row.addWidget(button)
        model_label = QLabel(label)
        if not paid:
            self.primary_model_label = model_label
        form.addRow(model_label, row)

    def _number(self, form, key, label, default, decimals):
        widget = QDoubleSpinBox()
        widget.setDecimals(decimals)
        widget.setRange(0, 100)
        widget.setValue(float(self._config.get(key, default)))
        self.fields[key] = widget
        form.addRow(label, widget)

    def _advanced(self, form):
        button = QToolButton()
        button.setObjectName("advancedToggle")
        button.setText("Advanced settings")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setArrowType(Qt.ArrowType.RightArrow)
        button.setCheckable(True)
        body = QWidget()
        layout = QFormLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        body.hide()
        button.toggled.connect(body.setVisible)
        button.toggled.connect(lambda expanded: button.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow))
        form.addRow(button)
        form.addRow(body)
        return button, body, layout

    def _provider_changed(self):
        outgoing = self._service
        self._service_drafts[outgoing] = {key: self.fields[key].model_id() if isinstance(self.fields[key], ModelCombo)
                                          else self.fields[key].text().strip()
                                          for key in ("api_key", "base_url", "vision_model", "free_providers")}
        self._service = self.provider.currentData()
        self.fields["vision_model"] = self.primary_combo if self._service == "openrouter" else self.custom_model
        self.primary_stack.setCurrentIndex(0 if self._service == "openrouter" else 1)
        defaults = {"api_key": "", "base_url": BASE_URL if self._service == "openrouter" else "",
                    "vision_model": FREE_MODEL if self._service == "openrouter" else "", "free_providers": ""}
        values = self._service_drafts.get(self._service, defaults)
        for key, value in values.items():
            if isinstance(self.fields[key], ModelCombo):
                self.fields[key].blockSignals(True)
                self.fields[key].clear()
                self.fields[key].set_model_id(value)
                self.fields[key].blockSignals(False)
            else:
                self.fields[key].setText(value)
        if self._service == "openrouter":
            self.fields["base_url"].setText(BASE_URL)
            self.model_note.setText("Browse models first; add a key when you're ready to generate.")
        self._last_models["vision_model"] = values["vision_model"]
        self._update_controls()
        self._auto_load()

    def _paid_changed(self):
        if self.paid_enabled.isChecked() and self.height() < 680:
            available = self.screen().availableGeometry().height() - 80
            self.resize(self.width(), max(self.height(), min(680, available)))
        self._update_controls()
        if self.paid_enabled.isChecked():
            self._auto_load(paid=True)

    def _update_controls(self):
        openrouter = self.provider.currentData() == "openrouter"
        busy = self.worker is not None
        paid = self.paid_enabled.isChecked()
        self.primary_form.setRowVisible(self.fields["base_url"], not openrouter)
        self.primary_form.setRowVisible(self.fields["free_providers"], openrouter)
        self.primary_advanced.setVisible(self.primary_advanced_button.isChecked())
        self.fields["base_url"].setReadOnly(openrouter)
        self.primary_refresh.setVisible(openrouter)
        self.primary_refresh.setEnabled(not busy)
        self.paid_body.setVisible(paid)
        self.paid_body.setEnabled(paid)
        self.paid_advanced.setVisible(paid)
        self.paid_advanced.setEnabled(paid)
        self.paid_refresh.setEnabled(not busy)
        self.test_connection.setEnabled(not busy)
        self.clean_cache_button.setEnabled(not busy)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(not busy)
        self.service_help.setText("One OpenRouter key for your models." if openrouter else "Using your custom model service.")
        self.connection_form.labelForField(self.fields["api_key"]).setText("OpenRouter key" if openrouter else "API key")
        self.primary_model_label.setText("Free model" if openrouter else "Model")
        self.fields["api_key"].setPlaceholderText("Paste your OpenRouter key" if openrouter else "API key for this service")
        if not openrouter and urlparse(self.fields["base_url"].text()).hostname in {"localhost", "127.0.0.1", "::1"}:
            self.service_help.setText("Using a saved local-model connection. Choose OpenRouter above for cloud models.")
        self.paid_help.setText("You'll confirm before each paid continuation." if paid else "Off · free model only.")
        if not openrouter:
            self.model_note.setText("Custom service: enter its model ID. Cloud model lists are available with OpenRouter.")

    def _auto_load(self, paid=False):
        if self._auto_refresh and not self._disposed and not self._closing:
            if paid:
                if self.paid_enabled.isChecked():
                    self._load_catalog(True)
            elif self.provider.currentData() == "openrouter":
                self._load_catalog(False)
            elif self.paid_enabled.isChecked():
                self._load_catalog(True)

    def _storage_selected(self, index):
        if self.tabs.widget(index) is self.storage_tab:
            self._load_storage()

    def _load_storage(self, clean=False):
        if self._disposed or self._closing:
            return
        if self.worker is not None:
            self._pending_storage = True
            return
        self._pending_storage = False
        self.storage_status.setText("Cleaning…" if clean else "Checking…")
        def operation(worker):
            if clean:
                return clean_storage(self.store_path, self._protected_task_id, worker.isInterruptionRequested)
            return storage_info(self.store_path, self._protected_task_id)
        def loaded(info):
            self.cache_summary.setText(f"Cached notes: {format_bytes(info['cache_bytes'])}")
            self.cache_reclaimable.setText(f"Reclaimable: {format_bytes(info['reclaimable_bytes'])} · {info['retention_days']}-day retention")
            if clean:
                self.storage_status.setText(f"Cleared {format_bytes(info['removed_bytes'])} · {info['removed_workspaces']} stale rendering folders removed.")
                self.cache_changed.emit()
            else:
                self.storage_status.setText("Automatic cleanup runs when the app starts. Saved files are kept.")
        self._start_operation(operation, loaded, self.storage_status.setText)

    def _load_catalog(self, paid=False, force=False):
        if self._disposed or self._closing or (not paid and self.provider.currentData() != "openrouter"):
            return
        if paid and not self.paid_enabled.isChecked():
            return
        label = self.paid_model_note if paid else self.model_note
        if self.worker is not None:
            self._pending_catalog.add(paid)
            return
        if not force and self._catalog_cache is not None:
            self._catalog_loaded(self._catalog_cache)
            return
        label.setText("Loading visual models from OpenRouter…")
        self._start_operation(lambda worker: list_note_models(cancel_callback=worker.isInterruptionRequested),
                              self._catalog_loaded, lambda message: label.setText(f"Could not load models. Your saved selection is unchanged. {message}"))

    def _catalog_loaded(self, models):
        self._catalog_cache = models
        self._models = {model.id: model for model in models}
        for paid in (False, True):
            if not paid and self.provider.currentData() != "openrouter":
                continue
            choices = [model for model in models if model.free != paid]
            self.fields["paid_model" if paid else "vision_model"].set_models(choices)
            label = self.paid_model_note if paid else self.model_note
            selected = self.fields["paid_model" if paid else "vision_model"].model_id()
            if not choices:
                label.setText(f"No compatible {'paid' if paid else 'free'} image models listed. Saved model retained; try refreshing.")
            elif selected not in {model.id for model in choices}:
                label.setText("Saved model isn't currently listed. Choose another or refresh.")
            elif paid:
                label.setText(f"{len(choices)} paid models · {self._models[selected].price_label}")
            else:
                label.setText(f"{len(choices)} free models · type to search")
            label.setToolTip("Prices are advertised minimums. Availability depends on the account, region and selected host.")

    def _model_changed(self, paid):
        key = "paid_model" if paid else "vision_model"
        field = self.fields[key]
        selected = field.model_id() if isinstance(field, ModelCombo) else field.text().strip()
        (field.lineEdit() if isinstance(field, ModelCombo) else field).setCursorPosition(0)
        self.fields[key].setToolTip(selected)
        if paid and selected in self._models:
            model = self._models[selected]
            self._paid_extra_prices = (model.image_price, model.request_price)
        if selected == self._last_models[key]:
            return
        self._last_models[key] = selected
        self.fields["paid_providers" if paid else "free_providers"].clear()
        if paid and selected in self._models:
            model = self._models[selected]
            for field, price in (("input_price", model.input_price), ("output_price", model.output_price)):
                self.fields[field].setValue(math.ceil(price * 1_000_000) / 1_000_000)
            self.paid_model_note.setText(model.price_label)

    def values(self):
        result = {"provider": self.provider.currentData(), "paid_enabled": self.paid_enabled.isChecked(),
                  "paid_image_price": self._paid_extra_prices[0], "paid_request_price": self._paid_extra_prices[1]}
        for key, widget in self.fields.items():
            if isinstance(widget, ModelCombo):
                result[key] = widget.model_id()
            else:
                result[key] = widget.value() if isinstance(widget, QDoubleSpinBox) else widget.text().strip()
        if result["provider"] == "openrouter":
            result["base_url"] = BASE_URL
        return result

    def _start_operation(self, operation, handler, error_handler):
        if self.worker is not None or self._disposed:
            return
        self._operation_handler = handler
        self._error_handler = error_handler
        self.worker = TaskWorker(operation, self)
        self.worker.result.connect(self._receive_result)
        self.worker.failed.connect(self._receive_error)
        self.worker.finished.connect(self._operation_finished)
        self.worker.start()
        self._update_controls()

    def _receive_result(self, result):
        if not self._closing:
            self._operation_handler(result)

    def _receive_error(self, message):
        if not self._closing:
            self._error_handler(message)

    def _show_status(self, text):
        self.test_status.setText(text)
        self.test_status.setVisible(bool(text))

    def _test_model_connection(self):
        if self.worker is not None:
            return
        config = self.values()
        self._show_status("Testing connection…")
        self._start_operation(lambda worker: make_client(config).test_connection(worker.isInterruptionRequested),
                              self._show_status, self._show_status)

    def _operation_finished(self):
        if self.sender() is not self.worker:
            return
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self._update_controls()
        if self._closing:
            self._disposed = True
            super().reject()
        elif self._pending_storage:
            QTimer.singleShot(0, self._load_storage)
        elif self._pending_catalog:
            paid = self._pending_catalog.pop()
            QTimer.singleShot(0, lambda: self._load_catalog(paid))

    def accept(self):
        if self.worker is None:
            self._disposed = True
            super().accept()

    def reject(self):
        self._closing = True
        self._pending_catalog.clear()
        self._pending_storage = False
        if self.worker is not None:
            self.worker.requestInterruption()
            self._show_status("Stopping…")
            return
        self._disposed = True
        super().reject()

    def closeEvent(self, event):
        self.reject()
        event.ignore() if self.worker is not None else event.accept()
