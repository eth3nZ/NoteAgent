"""Local document estimates and short-lived cloud metadata; no inference or Qt."""

import hashlib
import math
import time
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from pptx import Presentation

from app.core.cloud import AccountQuota, RequestCancelled, fetch_account_quota, list_note_models
from app.core.configuration import make_model_config
from app.core.jobs import cache_key_for
from app.core.prompt_loader import render_prompt
from app.core.task_store import TaskStore


@dataclass(frozen=True)
class DocumentSummary:
    path: str
    digest: str
    page_tokens: tuple
    image_tokens_high: tuple

    @property
    def page_count(self):
        return len(self.page_tokens)


@dataclass
class DocumentEstimate:
    document: DocumentSummary
    cached_pages: int
    pending_pages: int
    segment_pages: int
    planned_requests: int
    quota: AccountQuota | None
    quota_note: str
    cost_low: float | None
    cost_high: float | None
    cost_model: str
    price_note: str
    backup_enabled: bool
    backup_warning: str = ""


def _check(cancel):
    if cancel and cancel():
        raise RequestCancelled("Estimate cancelled")


def _text_tokens(text):
    wide = sum(ord(char) > 127 for char in text)
    return math.ceil(wide + (len(text) - wide) / 4)


def inspect_document(path, cancel=None):
    """Read text and dimensions only. Never render or upload a page for estimation."""
    path = Path(path).expanduser().resolve()
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            _check(cancel)
            digest.update(block)
    tokens, images = [], []
    def add_page(text, width, height):
        tokens.append(_text_tokens(text))
        tiles = math.ceil(width / 512) * math.ceil(height / 512)
        images.append(max(1024, min(8192, tiles * 256)))
    if path.suffix.lower() == ".pdf":
        with pymupdf.open(path) as document:
            for page in document:
                _check(cancel)
                add_page(page.get_text(), page.rect.width * 160 / 72, page.rect.height * 160 / 72)
    elif path.suffix.lower() == ".pptx":
        presentation = Presentation(path)
        def text_of(shapes):
            values = []
            for shape in shapes:
                if hasattr(shape, "text"):
                    values.append(shape.text)
                if getattr(shape, "has_table", False):
                    values.extend(cell.text for row in shape.table.rows for cell in row.cells)
                if hasattr(shape, "shapes"):
                    values.append(text_of(shape.shapes))
            return "\n".join(values)
        for slide in presentation.slides:
            _check(cancel)
            text = text_of(slide.shapes)
            if slide.has_notes_slide:
                text += slide.notes_slide.notes_text_frame.text
            add_page(text, presentation.slide_width / 914400 * 160, presentation.slide_height / 914400 * 160)
    else:
        raise ValueError("Choose a PDF or PPTX file")
    if not tokens:
        raise ValueError("The document has no pages")
    return DocumentSummary(str(path), digest.hexdigest(), tuple(tokens), tuple(images))


def build_estimate(document, config, models=(), quota=None, quota_note="", store=None):
    primary = make_model_config(config)
    prompt_hash = hashlib.sha256(render_prompt("segment_system", language="English").encode()).hexdigest()
    key = cache_key_for(document.digest, primary.fingerprint(), prompt_hash, config.get("language", "zh"))
    cached, segment_pages = set(), 8
    if store:
        cached = {page for segment in store.segments(key) for page in segment["notes"]}
        previous = store.latest_for_cache(key)
        if previous:
            segment_pages = max(1, min(8, int(previous["options"].get("segment_pages", 8))))
    pending = [index for index in range(document.page_count) if index + 1 not in cached]
    requests = math.ceil(len(pending) / segment_pages)
    hosted = config.get("provider") == "openrouter"
    backup_enabled = hosted and bool(config.get("paid_enabled"))
    model_id = config.get("paid_model", "") if backup_enabled else ""
    model = next((model for model in models if model.id == model_id), None)
    low = high = None
    backup_warning = ""
    note = "No verified price for this service. Custom requests may be billed by the provider."
    if model and not model.free:
        # Reference cost if ALL remaining pages used this paid model. Not a spending cap:
        # image tokenization, reasoning, retries and hosted endpoint prices can differ.
        text = sum(document.page_tokens[index] for index in pending)
        input_low = text + 512 * len(pending) + requests * 400
        input_high = text + sum(document.image_tokens_high[index] for index in pending) + requests * 400
        output_low = min(requests * 8192, sum(max(250, document.page_tokens[index] * .6) for index in pending))
        output_high = min(requests * 8192, sum(max(1500, document.page_tokens[index] * 1.8) for index in pending))
        low = (input_low * model.input_price + output_low * model.output_price) / 1_000_000
        high = (input_high * model.input_price + output_high * model.output_price) / 1_000_000
        extras = len(pending) * model.image_price + requests * model.request_price
        low += extras
        high += extras
        ceilings = make_model_config(config, paid=True)
        if (ceilings.input_price < model.input_price or ceilings.output_price < model.output_price or
                ceilings.image_price < model.image_price or ceilings.request_price < model.request_price):
            backup_warning = "Backup price ceilings are below current advertised prices. Select the backup model in Settings to update its price limits."
        reservation = (model.context_length * ceilings.input_price + ceilings.max_tokens * ceilings.output_price) / 1_000_000 + 8 * ceilings.image_price + ceilings.request_price
        if reservation > float(config.get("document_limit", .1)):
            backup_warning += f" The conservative next-request reservation (${reservation:.4f}) exceeds the document cap."
        note = ("Rough reference at advertised minimum prices, if all pending pages use this model. "
                "Not a quote or upper bound. Image billing, reasoning and retries can change the total. "
                "Actual paid requests still require approval and must fit the reservation and both budgets.")
    elif hosted:
        note = ("Paid price unavailable; refresh metadata. The selected free route remains $0." if backup_enabled
                else "Paid backup is off. Enable it in Settings to estimate the selected backup's cost.")
    return DocumentEstimate(document, len(cached & set(range(1, document.page_count + 1))), len(pending),
                            segment_pages, requests, quota, quota_note, low, high, model_id, note,
                            backup_enabled, backup_warning.strip())


class PreflightService:
    """Owned by one UI worker at a time; cache only local scans and metadata."""
    def __init__(self):
        self._documents = {}
        self._models = None
        self._model_time = 0
        self._accounts = {}

    def estimate(self, path, config, store_path=None, cancel=None, refresh=False):
        path = Path(path).expanduser().resolve()
        stat = path.stat()
        identity = (str(path), stat.st_size, stat.st_mtime_ns)
        document = self._documents.get(identity)
        if document is None:
            document = inspect_document(path, cancel)
            self._documents = {identity: document}
        models, quota, quota_note = (), None, "Free allowance is unavailable for custom services."
        price_warning = ""
        if config.get("provider") == "openrouter":
            if self._models is None or refresh or time.time() - self._model_time > 300:
                try:
                    self._models = list_note_models(cancel_callback=cancel)
                    self._model_time = time.time()
                except RequestCancelled:
                    raise
                except Exception:
                    price_warning = "Model prices could not be refreshed; cached prices may be outdated."
            models = self._models or ()
            key = make_model_config(config).api_key
            quota_note = "Add an OpenRouter key to check today's free requests."
            if key:
                fingerprint = hashlib.sha256(key.encode()).hexdigest()
                quota = self._accounts.get(fingerprint)
                if quota is None or refresh or time.time() - quota.fetched_at >= 60:
                    try:
                        quota = fetch_account_quota(key, cancel_callback=cancel)
                        self._accounts[fingerprint] = quota
                    except RequestCancelled:
                        raise
                    except Exception as exc:
                        quota_note = f"Allowance could not be refreshed: {exc}"
                        quota = None
                if quota is not None:
                    quota_note = "Server snapshot; requests are refreshed before generation and tracked during the task."
        _check(cancel)
        store = TaskStore(store_path) if store_path and Path(store_path).exists() else None
        estimate = build_estimate(document, config, models, quota, quota_note, store)
        if price_warning and estimate.backup_enabled:
            estimate.price_note = price_warning + " " + estimate.price_note
            estimate.backup_warning = (price_warning + " " + estimate.backup_warning).strip()
        return estimate
