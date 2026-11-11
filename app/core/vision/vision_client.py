"""OpenAI-compatible multimodal client used by NoteAgent."""

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from app.core.prompt_loader import render_prompt
from app.utils.helpers import encode_image_to_base64


class ModelAPIError(RuntimeError):
    """A user-facing error returned by an LLM provider."""


@dataclass
class PageAnalysis:
    page_num: int
    content: str
    raw_response: str


class VisionClient:
    """Client for hosted or local OpenAI-compatible multimodal endpoints."""

    DEFAULT_MODEL = "qwen/qwen3.8-27b"
    DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
    DEFAULT_MAX_TOKENS = 4096
    OPENROUTER_MODEL = "openrouter/free"
    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
    GEMINI_MODEL = "gemini-3.8-flash"
    GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
    LOCAL_DEFAULT_MODEL = "qwen3-vl:4b-instruct"
    LOCAL_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"

    SUGGESTED_MODELS = {
        "Qwen 3.8 27B (Groq free tier)": "qwen/qwen3.8-27b",
        "Gemini 3.8 Flash (Google free tier)": "gemini-3.8-flash",
        "OpenRouter free auto-router": OPENROUTER_MODEL,
        "Qwen3-VL 4B Instruct (local Ollama)": "qwen3-vl:4b-instruct",
        "Qwen3-VL 8B Instruct (local Ollama)": "qwen3-vl:8b-instruct",
    }

    def __init__(
        self,
        api_key: str = "",
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        llm_model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.2,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model.strip() or self.DEFAULT_MODEL
        self.llm_model = (llm_model or self.model).strip()
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.transport = transport

        if not self.base_url:
            raise ValueError("The model Base URL cannot be empty")
        if not self.is_local and not self.api_key:
            raise ValueError("An API key is required for a hosted model provider")

    @property
    def is_local(self) -> bool:
        hostname = (urlparse(self.base_url).hostname or "").lower()
        return hostname in {"localhost", "127.0.0.1", "::1"}

    def analyze_page(
        self,
        page_num: int,
        text: str,
        screenshot_path: str,
        language: str = "zh",
        stream_callback: Optional[Callable[[str], None]] = None,
    ) -> PageAnalysis:
        if not screenshot_path:
            raise ValueError(f"Page {page_num} has no rendered image")

        image_base64 = encode_image_to_base64(screenshot_path)
        messages = [
            {"role": "system", "content": self._build_system_prompt(language, page_num)},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": self._build_user_message(page_num, text, language),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_base64}"},
                    },
                ],
            },
        ]
        response_text = self._call_api(messages, stream_callback=stream_callback)
        if self._needs_code_recovery(response_text, text):
            recovered = self._extract_visible_code(
                page_num=page_num,
                text=text,
                image_base64=image_base64,
            )
            if recovered.strip() != "NO_CODE" and "```" in recovered:
                response_text = f"{response_text.rstrip()}\n\n{recovered.strip()}"
        return PageAnalysis(page_num, response_text, response_text)

    def complete_text(
        self,
        messages: List[Dict[str, Any]],
        stream_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        return self._call_api(messages, stream_callback=stream_callback, use_llm=True)

    def test_connection(self) -> str:
        """Make a minimal text request and return the provider's response."""
        return self.complete_text(
            [{"role": "user", "content": "Reply with exactly: OK"}]
        ).strip()

    def _build_system_prompt(self, language: str, page_num: int = 0) -> str:
        return render_prompt(
            "vision_system",
            language="Chinese" if language == "zh" else "English",
            page_num=page_num,
        )

    def _build_user_message(self, page_num: int, text: str, language: str) -> str:
        text_preview = text[:12000] if text else "(No extractable text)"
        return render_prompt(
            "vision_user",
            page_num=page_num,
            text=text_preview,
            language="Chinese" if language == "zh" else "English",
        )

    def _needs_code_recovery(self, response: str, extracted_text: str) -> bool:
        if re.search(
            r"```(?:python|py|javascript|typescript|java|c|cpp|c\+\+|r|sql)\b",
            response,
            re.IGNORECASE,
        ):
            return False
        signals = f"{response}\n{extracted_text}"
        return bool(
            re.search(
                r"\bpython\s+(?:script|code)|\bsource\s+code\b|\bcode\s+(?:snippet|listing|example)\b|"
                r"\bdataframe\b|\bterminal\s+output\b|\bconsole\s+output\b|"
                r"\bcode\s+editor\b|\bIDE\b",
                signals,
                re.IGNORECASE,
            )
        )

    def _extract_visible_code(
        self,
        page_num: int,
        text: str,
        image_base64: str,
    ) -> str:
        messages = [
            {"role": "system", "content": render_prompt("code_system")},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": render_prompt(
                            "code_user",
                            page_num=page_num,
                            text=text[:8000] if text else "(No extractable text)",
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_base64}"},
                    },
                ],
            },
        ]
        return self._call_api(messages)

    def _call_api(
        self,
        messages: List[Dict[str, Any]],
        stream_callback: Optional[Callable[[str], None]] = None,
        use_llm: bool = False,
    ) -> str:
        headers = {"Content-Type": "application/json", "X-Title": "NoteAgent"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.llm_model if use_llm else self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": stream_callback is not None,
        }
        url = f"{self.base_url}/chat/completions"
        if stream_callback:
            return self._stream_response(url, headers, payload, stream_callback)
        return self._sync_response(url, headers, payload)

    def _sync_response(self, url: str, headers: dict, payload: dict) -> str:
        response = self._request_with_retry(url, headers, payload)
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelAPIError("The model provider returned an unexpected response") from exc
        return self._content_to_text(content)

    def _stream_response(
        self,
        url: str,
        headers: dict,
        payload: dict,
        callback: Callable[[str], None],
    ) -> str:
        timeout = httpx.Timeout(300.0, connect=20.0)
        for attempt in range(3):
            parts: List[str] = []
            retry_response: Optional[httpx.Response] = None
            try:
                with httpx.Client(
                    timeout=timeout,
                    follow_redirects=True,
                    transport=self.transport,
                ) as client:
                    with client.stream("POST", url, headers=headers, json=payload) as response:
                        if response.status_code >= 400:
                            response.read()
                            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                                retry_response = response
                            else:
                                self._raise_api_error(response)
                        else:
                            for line in response.iter_lines():
                                if not line.startswith("data:"):
                                    continue
                                data_str = line[5:].strip()
                                if data_str == "[DONE]":
                                    break
                                try:
                                    data = json.loads(data_str)
                                    content = data["choices"][0]["delta"].get("content")
                                except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                                    continue
                                text = self._content_to_text(content)
                                if text:
                                    parts.append(text)
                                    callback(text)
            except httpx.RequestError as exc:
                if attempt == 2:
                    self._raise_network_error(exc)
                time.sleep(1.5 * (attempt + 1))
                continue

            if retry_response is not None:
                time.sleep(self._retry_delay(retry_response, attempt))
                continue
            result = "".join(parts).strip()
            if not result:
                raise ModelAPIError("The model returned an empty response")
            return result
        raise ModelAPIError("Model streaming request failed after retries")

    def _request_with_retry(self, url: str, headers: dict, payload: dict) -> httpx.Response:
        timeout = httpx.Timeout(180.0, connect=20.0)
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                with httpx.Client(
                    timeout=timeout,
                    follow_redirects=True,
                    transport=self.transport,
                ) as client:
                    response = client.post(url, headers=headers, json=payload)
                if response.status_code < 400:
                    return response
                if response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                    self._raise_api_error(response)
                last_error = ModelAPIError(f"Provider temporarily unavailable ({response.status_code})")
                delay = self._retry_delay(response, attempt)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt == 2:
                    self._raise_network_error(exc)
                delay = 1.5 * (attempt + 1)
            time.sleep(delay)
        raise ModelAPIError(str(last_error) if last_error else "Model request failed")

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After", "").strip()
        try:
            return min(65.0, max(0.5, float(retry_after)))
        except ValueError:
            pass
        try:
            data = response.json()
            error = data.get("error", data)
            message = error.get("message", "") if isinstance(error, dict) else str(error)
        except ValueError:
            message = response.text
        match = re.search(
            r"(?:try again in|retry after)\s*([0-9]+(?:\.[0-9]+)?)\s*(ms|s|sec|seconds)?",
            message,
            re.IGNORECASE,
        )
        if match:
            delay = float(match.group(1))
            if (match.group(2) or "").lower() == "ms":
                delay /= 1000
            return min(65.0, max(0.5, delay))
        return 1.5 * (attempt + 1)

    def _raise_api_error(self, response: httpx.Response) -> None:
        message = ""
        try:
            data = response.json()
            error = data.get("error", data)
            message = error.get("message", "") if isinstance(error, dict) else str(error)
        except ValueError:
            message = response.text[:500]

        hints = {
            401: "Check the API key in Settings.",
            402: "This model/provider requires credits; choose a :free model.",
            403: "The provider rejected this key or model.",
            404: "The configured model or endpoint was not found.",
            413: "The request exceeds this provider's token allowance; use local Ollama or smaller chunks.",
            429: "The free-tier rate limit was reached. Wait for reset or use local Ollama.",
        }
        suffix = hints.get(response.status_code, "")
        detail = message or response.reason_phrase or "Unknown provider error"
        raise ModelAPIError(
            f"Model API error {response.status_code}: {detail}"
            + (f" {suffix}" if suffix else "")
        )

    def _raise_network_error(self, exc: httpx.RequestError) -> None:
        if self.is_local:
            raise ModelAPIError(
                "Cannot reach local Ollama. Start Ollama and make sure the configured model is installed."
            ) from exc
        raise ModelAPIError(f"Cannot reach the model provider: {exc}") from exc

    def _content_to_text(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                item.get("text", "") for item in content if isinstance(item, dict)
            )
        return str(content)

    def close(self) -> None:
        """Kept for backwards compatibility; requests use scoped clients."""
