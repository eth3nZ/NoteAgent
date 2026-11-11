"""Independent document acceptance run. Offline by default; --live uses FREE OpenRouter only."""

import argparse
import ast
import json
import os
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import nbformat

from app.core.cloud import BASE_URL, FREE_MODEL, CloudClient, CloudError, ModelConfig, fetch_account_quota
from app.core.configuration import migrate_config
from app.core.settings_store import SettingsStore
from app.core.pipeline import Pipeline
from app.core.preflight import inspect_document
from app.core.task_store import TaskStore
from app.utils.helpers import load_config


class ValidationClient(CloudClient):
    def __init__(self, config, transport=None):
        super().__init__(config, transport)
        self.observations = []

    def generate_segment(self, pages, language="zh", cancel_callback=None, **kwargs):
        entry = {"pages": [page.page_num for page in pages], "probe": bool(kwargs.get("probe"))}
        self.observations.append(entry)
        try:
            result = super().generate_segment(pages, language, cancel_callback, **kwargs)
            entry.update(finish=result.finish_reason, model=result.model, provider=result.provider,
                         input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                         reported_cost=result.cost, provider_request_id=result.provider_request_id)
            return result
        except Exception as exc:
            entry.update(error=type(exc).__name__)
            if isinstance(exc, CloudError):
                entry.update(error_kind=exc.kind, message=str(exc))
            raise


def offline_transport(model, language="en"):
    """Exercise actual HTTP serialization/parsing with plainly identified fixture responses."""
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": model, "context_length": 1000000,
                "architecture": {"input_modalities": ["text", "image"]}}]})
        if request.url.path.endswith("/key"):
            return httpx.Response(200, json={"data": {"free_model_daily_requests": {"remaining": 50, "limit": 50}}})
        payload = json.loads(request.content)
        numbers = payload["response_format"]["json_schema"]["schema"]["properties"]["notes"]["items"]["properties"]["page"]["enum"]
        content = payload["messages"][1]["content"]
        images = [part for part in content if part["type"] == "image_url"]
        if len(images) != len(numbers) or any(not part["image_url"]["url"].startswith("data:image/png;base64,") for part in images):
            raise AssertionError("A source page image is missing")
        notes = []
        for number in numbers:
            source = next(part["text"].split("Extracted text:\n", 1)[1] for part in content
                          if part["type"] == "text" and part["text"].startswith(f"Source page {number}\n"))
            label = "离线测试文本转录（未翻译，不代表模型准确性）" if language == "zh" else "Offline fixture transcription"
            notes.append({"page": number, "markdown": f"## {label}\n\n" + (source.strip() or "No text layer on this page.")})
        return httpx.Response(200, json={"model": model, "provider": "offline-fixture", "id": "offline",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"notes": notes})}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0}})
    return httpx.MockTransport(handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--live", action="store_true", help="Send source text/images to a fixed FREE OpenRouter model")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--config", type=Path, default=Path.home()/".noteagent_config.json")
    parser.add_argument("--model", help="Fixed :free model ID; never a router or paid model")
    parser.add_argument("--language", choices=["zh", "en"], default="en",
                        help="Test language (default: en); normal app generation follows its language setting")
    parser.add_argument("--include-source-pages", action="store_true", help="Include source-page references in exported notes")
    args = parser.parse_args()
    saved = (SettingsStore(args.config).load(fields=("api_key",)).config if args.live else
             migrate_config(load_config(str(args.config))))
    if args.live and saved.get("provider") == "openrouter" and "api_key" in saved.get("_unavailable_credentials", ()):
        parser.error("The saved key is unavailable. Unlock Keychain and save Settings, or enter the key again.")
    model = args.model or (saved.get("vision_model") if saved.get("provider") == "openrouter" else FREE_MODEL)
    if not model or not model.endswith(":free") or model.startswith("openrouter/"):
        parser.error("This acceptance runner only permits a fixed :free model. Paid calls are disabled.")
    key = (saved.get("api_key", "") if saved.get("provider") == "openrouter" else "") or os.environ.get("OPENROUTER_API_KEY", "")
    if args.live and not key:
        parser.error("Save an OpenRouter key in Settings or set OPENROUTER_API_KEY. Never paste keys into chat.")
    output = args.output_dir or Path("dev_artifacts") / ("validation-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=True)
    client = ValidationClient(ModelConfig(api_key=key if args.live else "offline-fixture", model=model, base_url=BASE_URL),
                              None if args.live else offline_transport(model, args.language))
    store = TaskStore(output/"validation.sqlite3")
    summary = inspect_document(args.input)
    print(f"Mode: {'LIVE FREE API' if args.live else 'OFFLINE HTTP FIXTURE; not a quality evaluation'}", flush=True)
    print(f"Document: {summary.page_count} pages; model: {model}; language: {args.language}; paid calls: disabled", flush=True)
    started = time.monotonic()
    quota_before = quota_after = None
    if args.live:
        try:
            client.account_quota = fetch_account_quota(key)
            quota_before = asdict(client.account_quota)
            print(f"Free requests remaining (server): {client.account_quota.remaining_requests}", flush=True)
            print(client.test_connection(), flush=True)
        except CloudError as exc:
            report = {"mode": "live-free", "status": "paused", "phase": "image_probe",
                      "reason": exc.kind, "model": model, "inference_attempts": len(client.observations),
                      "message": str(exc), "requests": client.observations}
            (output/"report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"Image probe paused: {exc}", flush=True)
            return 2
    pipeline = Pipeline(client, store=store)
    result = pipeline.run(str(args.input), str(output), language=args.language,
                          include_source_pages=args.include_source_pages,
                          progress_callback=lambda message: print(message, flush=True))
    files = {"markdown": result.output_path}
    notebook_checks = None
    if result.completed_pages:
        if result.status != "completed":
            files["markdown"] = pipeline.run(str(args.input), str(output), language=args.language,
                                             task_id=result.task_id, action="export").output_path
        notebook = pipeline.run(str(args.input), str(output), output_format="ipynb", language=args.language,
                                task_id=result.task_id, action="export")
        exported = nbformat.read(notebook.output_path, as_version=4)
        nbformat.validate(exported)
        code_cells = [cell for cell in exported.cells if cell.cell_type == "code"]
        for cell in code_cells:
            ast.parse(cell.source)
            assert cell.execution_count is None and not cell.outputs, "Notebook must remain unexecuted"
        notebook_checks = {"schema_valid": True, "code_cells": len(code_cells),
                           "python_syntax_valid": True, "all_code_unexecuted": True}
        files["notebook"] = notebook.output_path
    before = len(client.observations)
    if result.status == "completed":
        with tempfile.TemporaryDirectory(prefix="noteagent_validation_") as scratch:
            cached = pipeline.run(str(args.input), scratch, language=args.language, include_source_pages=args.include_source_pages)
            assert cached.status == "completed" and len(client.observations) == before, "Cache rerun sent inference"
        # Keep the real exported result as the latest task, rather than the deleted scratch file.
        store.update(result.task_id, output_path=files['markdown'])
    if args.live:
        try:
            quota_after = asdict(fetch_account_quota(key))
        except CloudError:
            pass  # Keep the completed exports if final metadata is temporarily unavailable.
    report = {"mode": "live-free" if args.live else "offline-fixture", "status": result.status,
              "reason": result.reason, "model": model, "language": args.language, "include_source_pages": args.include_source_pages,
              "prompt_hash": client.prompt_hash(), "pages": summary.page_count,
              "completed_pages": result.completed_pages,
              "missing_pages": sorted(set(range(1, summary.page_count+1))-set(result.completed_pages)),
              "elapsed_seconds": round(time.monotonic()-started, 2), "inference_attempts": before,
              "cached_rerun_inference_attempts": len(client.observations)-before,
              "input_tokens_reported": sum(entry.get("input_tokens", 0) for entry in client.observations),
              "output_tokens_reported": sum(entry.get("output_tokens", 0) for entry in client.observations),
              "reported_cost_usd": sum(entry.get("reported_cost") or 0 for entry in client.observations),
              "allowance_before": quota_before, "allowance_after": quota_after,
              "files": files, "requests": client.observations, "notebook_checks": notebook_checks,
              "quality_review": "Manual comparison of formulas, code and diagrams is required; offline fixtures do not verify fidelity."}
    (output/"report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "requests"}, indent=2, ensure_ascii=False), flush=True)
    return 0 if result.status == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
