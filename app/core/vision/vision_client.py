"""OpenAI-compatible multimodal client used by NoteAgent."""

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from app.utils.helpers import encode_image_to_base64


class ModelAPIError(RuntimeError):
    """A user-facing error returned by an LLM provider."""


@dataclass
class PageAnalysis:
    page_num: int
    content: str
    raw_response: str


class VisionClient:
    """Client for OpenRouter or a local OpenAI-compatible Ollama endpoint."""

    DEFAULT_MODEL = "qwen/qwen3.8-27b:free"
    DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
    DEFAULT_MAX_TOKENS = 4096
    LOCAL_DEFAULT_MODEL = "qwen3-vl:4b"
    LOCAL_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"

    SUGGESTED_MODELS = {
        "Qwen 3.8 27B (hosted, free)": "qwen/qwen3.8-27b:free",
        "OpenRouter free auto-router": "openrouter/free",
        "Qwen3-VL 4B (local Ollama)": "qwen3-vl:4b",
        "Qwen3-VL 8B (local Ollama)": "qwen3-vl:8b",
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
            {"role": "system", "content": self._build_system_prompt(language)},
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

    def _build_system_prompt(self, language: str) -> str:
        if language == "zh":
            return """你是一个严谨的课件解析助手。根据页面截图和提取文本生成忠实、清晰的 Markdown 笔记。

规则：
1. 以截图为准，提取文本仅作辅助；不要编造截图中没有的信息。
2. 保留标题层级、列表、代码、公式、表格和关键视觉关系。
3. 代码使用带语言名的 fenced code block；公式使用 LaTeX。
4. 对图表或流程图说明节点、方向和结论，不要只写“此处有图”。
5. 不要输出客套话或分析过程，只输出该页的笔记内容。"""
        return """You are a rigorous course-material parser. Produce faithful, clear Markdown notes from the page image and extracted text.

Rules:
1. Treat the image as authoritative and the extracted text as supporting context. Do not invent facts.
2. Preserve headings, lists, code, formulas, tables, and important visual relationships.
3. Use fenced code blocks with language names and LaTeX for formulas.
4. Describe nodes, direction, and conclusions in charts or flow diagrams.
5. Return only the notes for this page, without preamble or hidden reasoning."""

    def _build_user_message(self, page_num: int, text: str, language: str) -> str:
        text_preview = text[:12000] if text else "(No extractable text)"
        if language == "zh":
            return f"""把课件第 {page_num} 页转换成结构化中文笔记。

提取文本：
---
{text_preview}
---

请保留页面中的重要细节，并用 Markdown 直接输出。"""
        return f"""Convert page {page_num} into structured English notes.

Extracted text:
---
{text_preview}
---

Preserve important page details and respond directly in Markdown."""

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
        parts: List[str] = []
        timeout = httpx.Timeout(300.0, connect=20.0)
        try:
            with httpx.Client(
                timeout=timeout,
                follow_redirects=True,
                transport=self.transport,
            ) as client:
                with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.status_code >= 400:
                        response.read()
                        self._raise_api_error(response)
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
            self._raise_network_error(exc)
        result = "".join(parts).strip()
        if not result:
            raise ModelAPIError("The model returned an empty response")
        return result

    def _request_with_retry(self, url: str, headers: dict, payload: dict) -> httpx.Response:
        timeout = httpx.Timeout(180.0, connect=20.0)
        last_error: Optional[Exception] = None
        for attempt in range(2):
            try:
                with httpx.Client(
                    timeout=timeout,
                    follow_redirects=True,
                    transport=self.transport,
                ) as client:
                    response = client.post(url, headers=headers, json=payload)
                if response.status_code < 400:
                    return response
                if response.status_code not in {429, 500, 502, 503, 504} or attempt == 1:
                    self._raise_api_error(response)
                last_error = ModelAPIError(f"Provider temporarily unavailable ({response.status_code})")
            except httpx.RequestError as exc:
                last_error = exc
                if attempt == 1:
                    self._raise_network_error(exc)
            time.sleep(1.5 * (attempt + 1))
        raise ModelAPIError(str(last_error) if last_error else "Model request failed")

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
