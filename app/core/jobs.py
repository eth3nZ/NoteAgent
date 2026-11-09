"""Serial document processing, resumable without a background service or a Qt dependency."""

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.cloud import CloudError, RequestCancelled
from app.core.generator.markdown import render_source_pages
from app.core.pipeline import PipelineCancelled, PipelineResult
from app.core.task_store import BudgetExceeded, TaskStore
from app.utils.helpers import ensure_output_dir, normalize_page_id, validate_file_type


def cache_key_for(doc_hash, fingerprint, prompt_hash, language, pages=8, dpi=160):
    return hashlib.sha256(json.dumps({
        "document": doc_hash, "primary": fingerprint, "prompt": prompt_hash,
        "language": language, "pages": pages, "dpi": dpi, "version": 1,
    }, sort_keys=True).encode()).hexdigest()


def next_free_reset(now):
    current = datetime.fromtimestamp(now, timezone.utc)
    return (current.replace(hour=0, minute=5, second=0, microsecond=0) + timedelta(days=1)).timestamp()


class DocumentJob:
    SEGMENT_PAGES = 8
    DPI = 160

    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.store = pipeline.store or TaskStore()
        self.task_id = None
        self.state_callback = self.progress = self.stream = self.cancel = None
        self.include_source_pages = False

    def run(self, input_path, output_dir, output_format, language, progress_callback,
            stream_callback, cancel_callback, notion_client, notion_page_id, notion_mode,
            task_id, state_callback, allow_paid, action, include_source_pages=None):
        self.progress, self.stream, self.cancel = progress_callback, stream_callback, cancel_callback
        self.state_callback = state_callback
        if output_format not in {"md", "ipynb"} or language not in {"zh", "en"}:
            raise ValueError("Unsupported output format or language")
        if action not in {"generate", "export", "publish"}:
            raise ValueError("Unknown task action")
        if action != "generate":
            self.task_id = task_id
            task = self.store.get(task_id)
            if task["status"] == "running":
                raise ValueError("Wait for the running task before exporting or publishing")
            self.include_source_pages = bool(task["options"].get("include_source_pages", False)
                                             if include_source_pages is None else include_source_pages)
            self.store.update(task_id, options={**task["options"], "include_source_pages": self.include_source_pages})
            if action == "export":
                return self._export(output_dir, output_format)
            return self._publish(notion_client, notion_page_id, notion_mode)
        path = Path(input_path).expanduser().resolve()
        if not path.is_file() or not validate_file_type(str(path)):
            raise ValueError("Choose an existing PDF or PPTX file")
        ensure_output_dir(output_dir)
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                self._check_cancel()
                digest.update(block)
        doc_hash = digest.hexdigest()
        primary = self.pipeline.vision_client
        backup = self.pipeline.paid_client
        if primary.config.paid:
            raise ValueError("The primary profile must be free. Configure paid requests as a manually approved backup.")
        key = cache_key_for(doc_hash, primary.config.fingerprint(), primary.prompt_hash(), language,
                            self.SEGMENT_PAGES, self.DPI)
        options = {"input_path": str(path), "output_dir": str(Path(output_dir).expanduser().resolve()),
                   "output_format": output_format, "language": language, "title": self.pipeline._document_title(str(path)),
                   "notion_page_id": notion_page_id, "notion_mode": notion_mode,
                   "primary_profile": primary.config.fingerprint()}
        if task_id is None:
            previous = self.store.latest_for_cache(key)
            if previous and "segment_pages" in previous["options"]:
                options["segment_pages"] = previous["options"]["segment_pages"]
        self.task_id = task_id or self.store.create(key, doc_hash, options)
        task = self.store.get(self.task_id)
        self.include_source_pages = bool(task["options"].get("include_source_pages", False)
                                         if include_source_pages is None else include_source_pages)
        options["include_source_pages"] = self.include_source_pages
        self.segment_pages = max(1, min(self.SEGMENT_PAGES, int(task["options"].get("segment_pages", self.SEGMENT_PAGES))))
        if task["cache_key"] != key:
            raise ValueError("The file, model, route or prompts changed. Start a new task; compatible cached segments remain available.")
        if task["status"] == "waiting_quota" and task["retry_at"] > self.store.clock():
            return self._result()
        if allow_paid and (not backup or not task["paid_eligible"]):
            raise ValueError("Paid continuation requires a configured backup and a failed free retry after the daily reset.")
        if self.store.uncertain(self.task_id):
            self.store.update(self.task_id, status="paused", reason="uncertain_cost")
            return self._result()
        self.store.acquire(self.task_id)
        self.store.update(self.task_id, options={**task["options"], **options}, retry_at=None)
        parser = None
        try:
            self._emit("Preparing document…")
            parser = self.pipeline._make_parser(str(path))
            pages = parser.parse(dpi=self.DPI, cancel_callback=self.cancel)
            if not pages:
                raise ValueError("The document has no readable pages")
            self.store.update(self.task_id, page_count=len(pages))
            cached = self._notes()
            if cached:
                self._emit(f"Reusing {len(cached)} cached pages")
                if self.stream:
                    self.stream(self._markdown())
            pending, group = [], []
            for page in pages:
                if page.page_num in cached:
                    if group:
                        pending.append(group)
                        group = []
                else:
                    group.append(page)
                    if len(group) == self.segment_pages:
                        pending.append(group)
                        group = []
            if group:
                pending.append(group)
            client = backup if allow_paid else primary
            # Cached/export-only runs do not contact the model catalog.
            if pending:
                self._check_cancel()
                client.prepare()
            while pending:
                self._check_cancel()
                segment = pending.pop(0)
                numbers = [p.page_num for p in segment]
                self._emit(f"Reading source pages {numbers[0]}–{numbers[-1]}…")
                try:
                    response = self._request(client, segment, language)
                except CloudError as exc:
                    if exc.kind == "context" and len(segment) > 1:
                        self._split(pending, segment, learn=True)
                        continue
                    task = self.store.get(self.task_id)
                    today = datetime.fromtimestamp(self.store.clock(), timezone.utc).date().isoformat()
                    after_reset = task["quota_day"] and task["quota_day"] < today
                    if not client.config.paid and (exc.kind == "daily_quota" or (
                        after_reset and exc.kind in {"rate_limit", "network", "service"}
                    )):
                        self._wait_for_quota()
                    else:
                        reason = f"page_{numbers[0]}_context" if exc.kind == "context" else exc.kind
                        self.store.update(self.task_id, status="paused", reason=reason)
                        self._emit(str(exc))
                    return self._result()
                if response.finish_reason != "stop" or not response.notes:
                    if len(segment) > 1 and response.finish_reason in {"length", "max_tokens", "invalid", "incomplete"}:
                        self._split(pending, segment, learn=response.finish_reason in {"length", "max_tokens"})
                        continue
                    self.store.update(self.task_id, status="paused", reason=f"page_{numbers[0]}_{response.finish_reason}")
                    return self._result()
                if self.stream:
                    fragment = "\n\n".join(f"{text}\n\n> Source: page {page}" for page, text in sorted(response.notes.items())) + "\n\n"
                    self.stream(fragment)
                self._emit(f"Saved source pages {numbers[0]}–{numbers[-1]} · {response.model} · {response.provider or 'provider not reported'}")
                if self.store.uncertain(self.task_id):
                    self.store.update(self.task_id, status="paused", reason="uncertain_cost")
                    return self._result()
            self._check_cancel()
            markdown = self._markdown()
            self.store.update(self.task_id, markdown=markdown)
            output = self.pipeline._save_output(render_source_pages(markdown, self.include_source_pages), str(path), output_dir, output_format, options["title"])
            self.store.update(self.task_id, output_path=output, status="completed", reason="", retry_at=None)
            if notion_mode != "none":
                self._check_cancel()
                return self._publish(notion_client, notion_page_id, notion_mode)
            return self._result()
        except (PipelineCancelled, RequestCancelled, InterruptedError):
            self.store.update(self.task_id, status="cancelled", reason="cancelled")
            return self._result()
        except BudgetExceeded:
            self.store.update(self.task_id, status="paused", reason="budget")
            return self._result()
        except CloudError as exc:
            self.store.update(self.task_id, status="paused", reason=exc.kind)
            self._emit(str(exc))
            return self._result()
        except Exception:
            self.store.update(self.task_id, status="failed", reason="processing_error")
            self._result()
            raise
        finally:
            if parser is not None:
                parser.cleanup()

    def _request(self, client, pages, language):
        for attempt in range(3):
            self._check_cancel()
            if hasattr(client, "check_allowance"):
                client.check_allowance(self.cancel)
            reservation = client.reservation_usd()
            self._check_cancel()
            request_id = self.store.reserve(self.task_id, client.model, client.config.paid, reservation,
                                            self.pipeline.document_limit, self.pipeline.monthly_limit)
            try:
                response = client.generate_segment(pages, language, self.cancel)
            except RequestCancelled:
                self.store.fail_request(request_id, uncertain=client.config.paid, detail="cancelled in flight")
                raise
            except CloudError as exc:
                self.store.fail_request(request_id, uncertain=client.config.paid and exc.uncertain, detail=exc.kind)
                if client.config.paid and exc.uncertain:
                    raise CloudError("Check the provider dashboard before retrying this paid request.", "uncertain_cost") from exc
                if exc.kind in {"rate_limit", "service", "network"} and attempt < 2:
                    delay = max(2 * (attempt + 1), exc.retry_after)
                    if delay > 60:
                        raise
                    self._emit(f"Temporary provider error; retrying in {delay:g}s…")
                    self._wait(delay)
                    continue
                raise
            except Exception:
                self.store.fail_request(request_id, uncertain=client.config.paid, detail="unexpected response error")
                raise
            valid = response.finish_reason == "stop" and bool(response.notes)
            try:
                self.store.commit_response(request_id, self.store.get(self.task_id)["cache_key"], response, valid)
            except Exception:
                self.store.fail_request(request_id, uncertain=client.config.paid, detail="unsettled response")
                raise CloudError("The response could not be settled safely.", "uncertain_cost")
            return response

    def _wait_for_quota(self):
        now = self.store.clock()
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        task = self.store.get(self.task_id)
        eligible = bool(task["quota_day"] and task["quota_day"] < day)
        self.store.update(self.task_id, status="waiting_paid" if eligible else "waiting_quota",
                          reason="daily_quota", quota_day=task["quota_day"] or day,
                          retry_at=None if eligible else next_free_reset(now), paid_eligible=int(eligible))

    def _split(self, pending, segment, learn=False):
        middle = len(segment) // 2
        if learn:
            self.segment_pages = min(self.segment_pages, max(1, middle))
            options = self.store.get(self.task_id)["options"]
            self.store.update(self.task_id, options={**options, "segment_pages": self.segment_pages})
            pending[:] = [part[start:start+self.segment_pages] for part in [segment, *pending]
                          for start in range(0, len(part), self.segment_pages)]
        else:
            pending[0:0] = [segment[:middle], segment[middle:]]
        self._emit("Splitting this segment to preserve complete page notes…")

    def _notes(self):
        task = self.store.get(self.task_id)
        notes = {}
        for segment in self.store.segments(task["cache_key"]):
            notes.update(segment["notes"])
        return notes

    def _markdown(self):
        task = self.store.get(self.task_id)
        notes = self._notes()
        missing = sorted(set(range(1, task["page_count"] + 1)) - notes.keys())
        parts = [f"# {task['options']['title']}"]
        if missing:
            label = "未完成笔记；缺失来源页" if task["options"]["language"] == "zh" else "Incomplete notes; missing source pages"
            parts.append(f"> {label}: {', '.join(map(str, missing))}")
        for page, content in sorted(notes.items()):
            parts.append(self.pipeline.md_generator.sanitize_fragment(content) + f"\n\n> Source: page {page}")
        return self.pipeline.md_generator.audit_document("\n\n".join(parts), task["options"]["title"]).content

    def _export(self, output_dir, output_format):
        task = self.store.get(self.task_id)
        if not self._notes():
            if task['status'] == 'completed':
                raise ValueError("These cached notes have expired. Open the exported file, or generate the document again.")
            raise ValueError("No completed pages are available to export yet")
        markdown = self._markdown()
        options = task["options"]
        source = options["input_path"]
        if len(self._notes()) < task["page_count"]:
            source = str(Path(source).with_stem(Path(source).stem + "_partial"))
        output = self.pipeline._save_output(render_source_pages(markdown, self.include_source_pages), source, output_dir, output_format, options["title"])
        self.store.update(self.task_id, markdown=markdown, output_path=output)
        return self._result()

    def _publish(self, client, page_id, mode):
        task = self.store.get(self.task_id)
        if task["status"] != "completed":
            raise ValueError("Complete the notes before publishing to Notion")
        if not client or not page_id or mode not in {"create_child", "append"}:
            self.store.update(self.task_id, reason="notion_setup")
            return self._result()
        options = task["options"]
        canonical = task["markdown"]
        if not canonical:
            if not self._notes():
                raise ValueError("These cached notes have expired. Open the exported file, or generate the document again.")
            canonical = self._markdown()
            self.store.update(self.task_id, markdown=canonical)
        markdown = render_source_pages(canonical, self.include_source_pages)
        try:
            normalized = normalize_page_id(page_id)
        except ValueError:
            self.store.update(self.task_id, reason="notion_setup")
            return self._result()
        targets = {page_id, normalized}
        # Recognize pre-normalization signatures saved by older versions, too.
        previous_target = options.get("notion_page_id", "")
        try:
            if normalize_page_id(previous_target) == normalized:
                targets.add(previous_target)
        except ValueError:
            pass
        signatures = {hashlib.sha256(f"{mode}:{target}:{markdown}".encode()).hexdigest() for target in targets}
        if options.get("published_signature") in signatures:
            return self._result()
        page_id = normalized
        signature = hashlib.sha256(f"{mode}:{page_id}:{markdown}".encode()).hexdigest()
        try:
            self._check_cancel()
            options["publication_state"] = "sending"
            self.store.update(self.task_id, options=options)
            if mode == "create_child":
                result = client.create_child_page(page_id, options["title"], markdown)
            else:
                result = client.append_to_page(page_id, markdown)
            options.update(published_signature=signature, publication_state="published",
                           notion_result={"page_id": result.page_id, "url": result.url, "mode": result.mode})
            self.store.update(self.task_id, options=options, reason="")
        except (PipelineCancelled, RequestCancelled):
            raise
        except Exception:
            options["publication_state"] = "uncertain"
            self.store.update(self.task_id, options=options, reason="notion_failed")
        return self._result()

    def _result(self):
        from app.core.notion import NotionPublishResult
        task = self.store.get(self.task_id)
        notion = task["options"].get("notion_result")
        result = PipelineResult(task["output_path"], task["options"]["title"], task["page_count"], 0, 1,
                                notion=NotionPublishResult(**notion) if notion else None,
                                task_id=self.task_id, status=task["status"], completed_pages=sorted(self._notes()),
                                cost_usd=self.store.cost(self.task_id), reason=task["reason"],
                                retry_at=task["retry_at"], paid_eligible=bool(task["paid_eligible"]))
        quota = getattr(self.pipeline.vision_client, "account_quota", None)
        result.free_requests_remaining = quota.remaining_requests if quota else None
        result.segment_pages = task["options"].get("segment_pages", self.SEGMENT_PAGES)
        if task["reason"]:
            result.warnings.append(task["reason"])
        if self.state_callback:
            self.state_callback(result)
        return result

    def _emit(self, message):
        if self.progress:
            self.progress(message)
        if self.state_callback and self.task_id:
            self._result()

    def _check_cancel(self):
        if self.cancel and self.cancel():
            raise PipelineCancelled("Cancelled")

    def _wait(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._check_cancel()
            time.sleep(min(.1, max(0, end - time.monotonic())))
