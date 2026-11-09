"""Small synchronous multimodal client. Retries and spending belong to the pipeline."""

import base64
import codecs
import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx

from app.core.prompt_loader import render_prompt
from app.core.vision.vision_client import ModelAPIError


FREE_MODEL = "nvidia/nemotron-3.5-super-vl:free"
PAID_MODEL = "qwen/qwen3.8-27b"
BASE_URL = "https://openrouter.ai/api/v1"


class RequestCancelled(RuntimeError):
    pass


class CloudError(ModelAPIError):
    def __init__(self, message, kind="service", retry_after=0, uncertain=False):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after
        self.uncertain = uncertain


@dataclass(frozen=True)
class ModelConfig:
    api_key: str = field(default="", repr=False)
    base_url: str = BASE_URL
    model: str = FREE_MODEL
    providers: tuple = ()
    paid: bool = False
    input_price: float = 0.035  # Maximum USD per million tokens, enforced at routing.
    output_price: float = 1.50
    max_tokens: int = 8192
    image_price: float = 0.0  # USD per image; paid route ceiling.
    request_price: float = 0.0

    def fingerprint(self):
        values = asdict(self)
        # Accepted page content survives provider/budget adjustments and key rotation.
        for key in ("api_key", "providers", "input_price", "output_price", "image_price", "request_price"):
            values.pop(key)
        return values


@dataclass
class SegmentResponse:
    notes: dict
    finish_reason: str
    model: str
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Optional[float] = None
    provider_request_id: str = ""


@dataclass(frozen=True)
class CatalogModel:
    id: str
    name: str
    input_price: float
    output_price: float
    context_length: int
    free: bool
    image_price: float = 0.0
    request_price: float = 0.0

    @property
    def price_label(self):
        if self.free:
            return "Free"
        label = f"from ${self.input_price:g} in / ${self.output_price:g} out per 1M tokens"
        if self.image_price:
            label += f" + ${self.image_price:g}/image"
        if self.request_price:
            label += f" + ${self.request_price:g}/request"
        return label


@dataclass
class AccountQuota:
    remaining_requests: Optional[int]
    daily_limit: Optional[int]
    remaining_budget: Optional[float]
    fetched_at: float


def fetch_account_quota(api_key, transport=None, cancel_callback=None):
    """Authenticated metadata only; missing quota fields mean unknown, never zero."""
    if not api_key.strip():
        raise CloudError("Add an OpenRouter key to check free allowance.", "config")
    if cancel_callback and cancel_callback():
        raise RequestCancelled("Allowance check cancelled")
    try:
        with httpx.Client(timeout=httpx.Timeout(12, connect=5), transport=transport, follow_redirects=False) as client:
            response = client.get(f"{BASE_URL}/key", headers={"Authorization": f"Bearer {api_key.strip()}"})
        if cancel_callback and cancel_callback():
            raise RequestCancelled("Allowance check cancelled")
        if response.status_code != 200:
            CloudClient._raise_error(response)
        data = response.json()["data"]
        free = data.get("free_model_daily_requests") or {}
        def count(name):
            value = free.get(name)
            if value is None:
                return None
            if isinstance(value, bool) or int(value) != float(value) or int(value) < 0:
                raise ValueError("Invalid request count")
            return int(value)
        budgets = [data.get("limit_remaining"), (data.get("effective_budget") or {}).get("remaining_usd")]
        budgets = [float(value) for value in budgets if value is not None]
        if any(not math.isfinite(value) for value in budgets):
            raise ValueError("Invalid account budget")
        return AccountQuota(count("remaining"), count("limit"), max(0, min(budgets)) if budgets else None, time.time())
    except httpx.RequestError as exc:
        raise CloudError(f"Cannot check OpenRouter allowance: {type(exc).__name__}", "network") from exc
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise CloudError("Unexpected account allowance response.", "config") from exc


def _read_model_catalog(base_url, headers, transport=None, cancel_callback=None, timeout=None):
    if cancel_callback and cancel_callback():
        raise RequestCancelled("Model list loading cancelled")
    try:
        with httpx.Client(timeout=timeout or httpx.Timeout(30, connect=10), transport=transport,
                          follow_redirects=False) as client:
            response = client.get(f"{base_url}/models", headers=headers)
        if cancel_callback and cancel_callback():
            raise RequestCancelled("Model list loading cancelled")
        if response.status_code != 200:
            CloudClient._raise_error(response)
        rows = response.json()["data"]
        if not isinstance(rows, list):
            raise ValueError("Expected a model list")
        return rows
    except httpx.RequestError as exc:
        raise CloudError(f"Cannot reach the model catalog: {type(exc).__name__}", "network") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise CloudError("Unexpected model catalog response.", "config") from exc


def list_note_models(api_key="", transport=None, cancel_callback=None):
    """Read public metadata without credentials or inference. The key argument is legacy API compatibility."""
    rows = _read_model_catalog(BASE_URL, {"X-Title": "NoteAgent"}, transport, cancel_callback,
                               httpx.Timeout(15, connect=5))
    models = []
    for row in rows:
        try:
            model_id = row["id"]
            if (not isinstance(model_id, str) or "/" not in model_id or
                    model_id.lower().startswith(("openrouter/", "~")) or model_id.endswith(":batch")):
                continue
            architecture = row.get("architecture") or {}
            if "image" not in architecture.get("input_modalities", []):
                continue
            if "text" not in architecture.get("output_modalities", ["text"]):
                continue
            supported = row.get("supported_parameters") or []
            if "structured_outputs" not in supported:
                continue
            prices = row["pricing"]
            input_price, output_price = float(prices["prompt"]) * 1_000_000, float(prices["completion"]) * 1_000_000
            extras = [float(prices.get(key) or 0) for key in ("image", "request")]
            if any(not math.isfinite(p) or p < 0 for p in (input_price, output_price, *extras)):
                continue
            free = model_id.endswith(":free") and not any((input_price, output_price, *extras))
            if not free and (model_id.endswith(":free") or not any((input_price, output_price, *extras))):
                continue
            models.append(CatalogModel(model_id, str(row.get("name") or model_id), input_price,
                                       output_price, int(row["context_length"]), free, *extras))
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return sorted(models, key=lambda m: (not m.free, m.input_price + m.output_price, m.name.lower()))


class CloudClient:
    def __init__(self, config: ModelConfig, transport=None):
        self.config = config
        self.transport = transport
        self.context_length = None
        self.reasoning_options = {}
        self.account_quota = None
        self._last_dispatched = 0.0
        self.model = config.model
        self.base_url = config.base_url.rstrip("/")
        self._validate()

    @property
    def is_openrouter(self):
        return urlparse(self.base_url).hostname == "openrouter.ai"

    def _validate(self):
        c = self.config
        if not c.api_key.strip():
            raise ValueError("Add an API key in Settings or OPENROUTER_API_KEY.")
        if not c.model or c.model.lower().startswith("openrouter/"):
            raise ValueError("Choose a fixed model; automatic model routers are disabled.")
        if self.is_openrouter and (c.model.startswith("~") or c.model.endswith(":batch")):
            raise ValueError("Choose a fixed synchronous model; aliases and Batch routes are not supported.")
        url = urlparse(self.base_url)
        if url.scheme != "https" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Hosted model endpoints must use HTTPS.")
        if not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Invalid model Base URL.")
        if not c.paid and self.is_openrouter and not c.model.endswith(":free"):
            raise ValueError("The free profile must use an explicit :free model.")
        if c.paid and not self.is_openrouter:
            raise ValueError("Paid budget enforcement currently requires OpenRouter.")
        if any(not math.isfinite(p) or p < 0 for p in (c.input_price, c.output_price, c.image_price, c.request_price)):
            raise ValueError("Price ceilings must be finite non-negative numbers.")
        if not 64 <= c.max_tokens <= 32768:
            raise ValueError("Output limit must be between 64 and 32768 tokens.")

    def _headers(self):
        return {"Authorization": f"Bearer {self.config.api_key.strip()}",
                "Content-Type": "application/json", "X-Title": "NoteAgent"}

    def _http(self):
        return httpx.Client(timeout=httpx.Timeout(30, connect=10), transport=self.transport,
                            follow_redirects=False)

    def prepare(self):
        """Check advertised image support and get an input upper bound for reservations."""
        if self.context_length is not None:
            return
        if not self.is_openrouter:
            self.context_length = 131072
            return
        try:
            model = next((m for m in self._catalog_data() if m["id"] == self.model), None)
            if not model or "image" not in model.get("architecture", {}).get("input_modalities", []):
                raise CloudError("The selected model is unavailable or does not advertise image input.", "config")
            supported = model.get("supported_parameters")
            if supported is not None and "structured_outputs" not in supported:
                raise CloudError("The selected model does not advertise strict JSON-schema output. Choose another image model.", "capability")
            self.context_length = int(model["context_length"])
            self.reasoning_options = model.get("reasoning") or {}
            if not 0 < self.context_length <= 10_000_000:
                raise ValueError("Invalid context limit")
        except (ValueError, KeyError, TypeError) as exc:
            raise CloudError("Unexpected model catalog response.", "config") from exc

    def _catalog_data(self, cancel_callback=None, timeout=None):
        return _read_model_catalog(self.base_url, self._headers(), self.transport, cancel_callback, timeout)

    def reservation_usd(self):
        self.prepare()
        if not self.config.paid:
            return 0.0
        # Reserve against the ENTIRE advertised context, not an unreliable image-token estimate.
        return ((self.context_length * self.config.input_price +
                 self.config.max_tokens * self.config.output_price) / 1_000_000
                + 8 * self.config.image_price + self.config.request_price)

    def check_allowance(self, cancel_callback=None):
        if not self.is_openrouter:
            return
        if self.account_quota is None or time.time() - self.account_quota.fetched_at >= 60:
            self.account_quota = fetch_account_quota(self.config.api_key, self.transport, cancel_callback)
        elif not self.config.paid and self.account_quota.remaining_requests == 0:
            # Failed attempts are counted conservatively locally. Reconcile with
            # the server before declaring the whole UTC day's allowance spent.
            self.account_quota = fetch_account_quota(self.config.api_key, self.transport, cancel_callback)
        if not self.config.paid and self.account_quota.remaining_requests == 0:
            raise CloudError("The free daily request quota is exhausted.", "daily_quota")
        if self.config.paid and self.account_quota.remaining_budget is not None:
            if self.reservation_usd() > self.account_quota.remaining_budget:
                raise CloudError("The next request cannot fit the OpenRouter key's remaining budget.", "credits")
        # Single-request scheduling with a small margin below the free 20 RPM cap.
        while self._last_dispatched and time.monotonic() - self._last_dispatched < 3.2:
            if cancel_callback and cancel_callback():
                raise RequestCancelled("Cancelled while waiting for the request interval")
            time.sleep(.1)

    def prompt_hash(self):
        return hashlib.sha256(render_prompt("segment_system", language="English").encode()).hexdigest()

    def generate_segment(self, pages, language="zh", cancel_callback=None, *, probe=False):
        if cancel_callback and cancel_callback():
            raise RequestCancelled("Cancelled before sending a request")
        numbers = [p.page_num for p in pages]
        parts = [{"type": "text", "text": (
            f"Process ALL original source pages {numbers}. Each image is preceded by its source page number. "
            "Return a JSON object with a notes array, one entry per page: "
            '{"notes":[{"page":1,"markdown":"## Topic\\nDetailed notes..."}]}. '
            "Use the original page numbers exactly. A genuinely empty page still needs an entry explaining it.") }]
        for page in pages:
            parts.append({"type": "text", "text": f"Source page {page.page_num}\nExtracted text:\n{page.text}"})
            data = base64.b64encode(Path(page.screenshot_path).read_bytes()).decode()
            parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}})
        if not probe:
            target = "Simplified Chinese" if language == "zh" else "English"
            parts.append({"type": "text", "text": (
                f"Final check: write ALL headings and explanatory prose in {target}. "
                f"Translate the source explanations into {target}; do not copy them in a different language. "
                "Keep code and literal strings unchanged. Return only the requested page_notes JSON.")})
        payload = {
            "model": self.model, "max_tokens": (1024 if self.reasoning_options.get("mandatory") else 256) if probe else self.config.max_tokens,
            "temperature": 0.1, "stream": True, "stream_options": {"include_usage": True},
            "messages": [{"role": "system", "content": render_prompt(
                "segment_system", language="Chinese" if language == "zh" else "English")},
                {"role": "user", "content": parts}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "page_notes", "strict": True, "schema": {
                    "type": "object", "additionalProperties": False, "required": ["notes"],
                    "properties": {"notes": {"type": "array", "items": {
                        "type": "object", "additionalProperties": False, "required": ["page", "markdown"],
                        "properties": {"page": {"type": "integer", "enum": numbers},
                                       "markdown": {"type": "string"}}}}}}}},
        }
        if probe:
            payload["messages"][0]["content"] = (
                "Transcribe only the visible characters in the supplied image. "
                "Return the requested JSON notes array, with the transcription as markdown. Do not explain.")
            parts[0]["text"] = 'Return {"notes":[{"page":1,"markdown":"exact visible text"}]}.'
        if self.is_openrouter:
            prices = {"prompt": self.config.input_price if self.config.paid else 0,
                      "completion": self.config.output_price if self.config.paid else 0,
                      "image": self.config.image_price if self.config.paid else 0,
                      "request": self.config.request_price if self.config.paid else 0}
            payload["provider"] = {"require_parameters": True, "max_price": prices}
            if self.config.providers:
                payload["provider"]["only"] = list(self.config.providers)
            if self.reasoning_options.get("mandatory"):
                payload["reasoning"] = {"exclude": True}
                supported = self.reasoning_options.get("supported_efforts") or []
                effort = next((level for level in ("minimal", "low", "medium", "high", "xhigh", "max") if level in supported), None)
                if effort:
                    payload["reasoning"]["effort"] = effort
            elif not self.model.lower().startswith("google/"):
                payload["reasoning"] = {"enabled": False}
        result = self._send(payload, numbers, cancel_callback)
        if not probe and language == "zh" and result.finish_reason == "stop":
            for markdown in result.notes.values():
                prose = re.sub(r"```.*?```", "", markdown, flags=re.S)
                prose = re.sub(r"\\\[.*?\\\]|\\\(.*?\\\)|\$\$.*?\$\$|\$[^$\n]*\$", "", prose, flags=re.S)
                prose = "\n".join(line for line in prose.splitlines() if not line.lstrip().startswith("|"))
                if len(re.findall(r"[A-Za-z]{3,}", prose)) >= 35 and len(re.findall(r"[\u4e00-\u9fff]", prose)) < 5:
                    result.notes, result.finish_reason = {}, "language"
                    break
        return result

    def _send(self, payload, numbers, cancel_callback):
        texts, usage = [], {}
        finish, model, provider, provider_request_id = "", self.model, "", ""
        started = time.monotonic()
        if cancel_callback and cancel_callback():
            raise RequestCancelled("Cancelled before sending a request")
        try:
            with self._http() as client:
                self._last_dispatched = started
                if self.account_quota is not None and not self.config.paid and self.account_quota.remaining_requests is not None:
                    self.account_quota.remaining_requests = max(0, self.account_quota.remaining_requests - 1)
                with client.stream("POST", f"{self.base_url}/chat/completions",
                                   headers=self._headers(), json=payload) as response:
                    if response.status_code >= 300:
                        body = b"".join(self._checked_chunks(response, started, cancel_callback))
                        # iter_bytes() already decoded HTTP content compression.
                        # Reusing Content-Encoding would decompress the JSON twice
                        # and disguise an upstream HTTP error as a network failure.
                        headers = dict(response.headers)
                        for name in ("content-encoding", "content-length", "transfer-encoding"):
                            headers.pop(name, None)
                        self._raise_error(httpx.Response(response.status_code, headers=headers, content=body))
                    if "text/event-stream" not in response.headers.get("content-type", ""):
                        data = json.loads(b"".join(self._checked_chunks(response, started, cancel_callback)))
                        choice = data["choices"][0]
                        texts.append(choice["message"].get("content", ""))
                        finish = choice.get("finish_reason", "")
                        usage, model, provider = data.get("usage", {}), data.get("model", model), data.get("provider", "")
                        provider_request_id = data.get("id", "")
                    else:
                        for line in self._lines(response, started, cancel_callback):
                            if not line.startswith("data:"):
                                continue
                            value = line[5:].strip()
                            if value == "[DONE]":
                                break
                            if not value:
                                continue
                            data = json.loads(value)
                            if data.get("error"):
                                raise CloudError("The upstream stream failed.", "service", uncertain=True)
                            usage = data.get("usage") or usage
                            provider_request_id = data.get("id") or provider_request_id
                            model, provider = data.get("model", model), data.get("provider", provider)
                            for choice in data.get("choices", []):
                                texts.append(choice.get("delta", {}).get("content") or "")
                                finish = choice.get("finish_reason") or finish
        except httpx.RequestError as exc:
            raise CloudError(f"Model connection failed: {type(exc).__name__}", "network", uncertain=True) from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise CloudError("Malformed model response; request outcome is uncertain.", "service", uncertain=True) from exc
        result = SegmentResponse({}, finish, model, provider,
                                 int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0),
                                 usage.get("cost"), provider_request_id)
        if self.config.paid and self.account_quota and self.account_quota.remaining_budget is not None and result.cost is not None:
            try:
                charged = float(result.cost)
                if math.isfinite(charged) and charged >= 0:
                    self.account_quota.remaining_budget = max(0, self.account_quota.remaining_budget - charged)
            except (ValueError, TypeError):
                pass  # Settlement rejects an invalid charge and pauses the task.
        if finish == "stop":
            try:
                rows = json.loads("".join(texts))["notes"]
                result.notes = {row["page"]: row["markdown"] for row in rows}
                if any(type(row["page"]) is not int for row in rows) or len(rows) != len(numbers) or set(result.notes) != set(numbers) or any(
                    not isinstance(v, str) or not v.strip() for v in result.notes.values()
                ):
                    result.notes = {}
                    result.finish_reason = "incomplete"
            except (ValueError, KeyError, TypeError):
                result.finish_reason = "invalid"
        if model != self.model:
            # OpenRouter sometimes returns the canonical ID; reject unexpected substitutions.
            if model.removesuffix(":free") != self.model.removesuffix(":free"):
                result.notes, result.finish_reason = {}, "unexpected_model"
        return result

    def _checked_chunks(self, response, started, cancel):
        size = 0
        for chunk in response.iter_bytes():
            if cancel and cancel():
                raise RequestCancelled("Request cancelled; spending may need reconciliation.")
            if time.monotonic() - started > 180:
                raise CloudError("Request exceeded its total time limit.", "network", uncertain=True)
            size += len(chunk)
            if size > 16 * 1024 * 1024:
                raise CloudError("Provider response exceeded the size limit.", "service", uncertain=True)
            yield chunk

    def _lines(self, response, started, cancel):
        decoder = codecs.getincrementaldecoder("utf-8")()
        pending = ""
        for chunk in self._checked_chunks(response, started, cancel):
            pending += decoder.decode(chunk)
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                yield line.rstrip("\r")
        pending += decoder.decode(b"", final=True)
        if pending:
            yield pending.rstrip("\r")

    @staticmethod
    def _raise_error(response):
        try:
            detail = json.dumps(response.json(), ensure_ascii=False)
        except ValueError:
            detail = response.text
        lower = detail.lower()
        status = response.status_code
        kind = "service"
        if status in {401, 400, 404, 413, 422}:
            kind = "context" if status == 413 or "context length" in lower or "too many images" in lower else "config"
        if status in {400, 403}:
            kind = "region" if any(s in lower for s in ("region", "country", "location", "banned", "geograph")) else "config"
            if kind == "config" and ("context length" in lower or ("image" in lower and any(
                s in lower for s in ("maximum", "too many", "at most", "exceed", "limit")
            ))):
                kind = "context"
        if status == 402:
            kind = "credits"
        if status == 404:
            if "data policy" in lower or "privacy" in lower:
                kind = "privacy"
            elif "parameter" in lower or "structured output" in lower or "json_schema" in lower:
                kind = "capability"
            elif "routing" in lower or "provider preferences" in lower:
                kind = "routing"
        if status == 429:
            kind = "daily_quota" if any(s in lower for s in ("per-day", "per day", "daily", "requests/day")) else "rate_limit"
        try:
            delay = max(0, float(response.headers.get("retry-after", "2")))
        except ValueError:
            delay = 2
        # Do not surface arbitrary provider response bodies (they can contain request data).
        hints = {"region": "This model/provider is unavailable in the current region. Choose another allowed provider.",
                 "privacy": "OpenRouter account data-policy settings exclude this route. Review the route's data-use requirements in OpenRouter privacy settings.",
                 "capability": "No route supports the requested image, structured-output or streaming parameters. Choose a compatible model/provider.",
                 "routing": "No endpoint matches the configured provider or price ceilings. Check this fixed model's available routes.",
                 "config": "Check the key, model, supported parameters and provider allowlist.",
                 "credits": "Provider credits are unavailable. No automatic paid fallback was attempted.",
                 "daily_quota": "The free daily request quota is exhausted.",
                 "rate_limit": "The provider is temporarily rate limited.",
                 "context": "This segment exceeds the provider's input or image limit."}
        raise CloudError(f"Model HTTP {status}. {hints.get(kind, 'The provider is temporarily unavailable.')}",
                         kind, delay, uncertain=status >= 500)

    def test_connection(self, cancel_callback=None):
        """Real image + structured-output probe, never a hidden paid test."""
        import tempfile
        from types import SimpleNamespace
        from PIL import Image, ImageDraw
        self.prepare()
        self.check_allowance(cancel_callback)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probe.png"
            image = Image.new("RGB", (320, 120), "white")
            ImageDraw.Draw(image).text((30, 45), "NOTEAGENT 4827", fill="black")
            image = image.resize((640, 240))
            image.save(path)
            result = self.generate_segment([SimpleNamespace(page_num=1, text="", screenshot_path=str(path))], "en", cancel_callback, probe=True)
        if result.finish_reason != "stop" or "4827" not in result.notes.get(1, ""):
            raise CloudError("Image probe failed. This route has not passed visual capability checks.", "config")
        return f"Image request passed · {result.model} · {result.provider or 'provider not reported'}"
