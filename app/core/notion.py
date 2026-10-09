"""Minimal Notion publisher using the current Markdown content API."""

import time
from dataclasses import dataclass
from typing import Optional

import httpx

from app.utils.helpers import normalize_page_id


class NotionAPIError(RuntimeError):
    """A user-facing Notion API failure."""


@dataclass
class NotionPublishResult:
    page_id: str
    url: str
    mode: str


class NotionClient:
    BASE_URL = "https://api.notion.com/v1"
    API_VERSION = "2026-03-11"

    def __init__(
        self,
        token: str,
        base_url: str = BASE_URL,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        if not token.strip():
            raise ValueError("A Notion access token is required")
        self.token = token.strip()
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    def create_child_page(
        self, parent_page_id: str, title: str, markdown: str
    ) -> NotionPublishResult:
        parent_id = normalize_page_id(parent_page_id)
        payload = {
            "parent": {"type": "page_id", "page_id": parent_id},
            "properties": {
                "title": {
                    "type": "title",
                    "title": [{"type": "text", "text": {"content": title[:200]}}],
                }
            },
            "markdown": markdown,
            "markdown_version": "v1",
            "notifications": {"mode": "silent"},
        }
        data = self._request("POST", "/pages", payload)
        page_id = data.get("id", "")
        if not page_id:
            raise NotionAPIError("Notion created a page but did not return its ID")
        return NotionPublishResult(
            page_id=page_id,
            url=data.get("url") or self._page_url(page_id),
            mode="create_child",
        )

    def append_to_page(self, page_id: str, markdown: str) -> NotionPublishResult:
        normalized_id = normalize_page_id(page_id)
        payload = {
            "type": "insert_content",
            "insert_content": {
                "content": f"\n\n{markdown.strip()}\n",
                "position": {"type": "end"},
            },
            "markdown_version": "v1",
        }
        self._request("PATCH", f"/pages/{normalized_id}/markdown", payload)
        return NotionPublishResult(
            page_id=normalized_id,
            url=self._page_url(normalized_id),
            mode="append",
        )

    def _request(self, method: str, path: str, payload: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.API_VERSION,
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(90.0, connect=20.0)
        for attempt in range(2):
            try:
                with httpx.Client(
                    timeout=timeout,
                    transport=self.transport,
                    follow_redirects=True,
                ) as client:
                    response = client.request(
                        method, f"{self.base_url}{path}", headers=headers, json=payload
                    )
            except httpx.RequestError as exc:
                raise NotionAPIError(f"Cannot reach Notion: {exc}") from exc

            if response.status_code < 400:
                try:
                    return response.json()
                except ValueError as exc:
                    raise NotionAPIError("Notion returned an invalid response") from exc
            if response.status_code == 429 and attempt == 0:
                retry_after = response.headers.get("Retry-After", "1")
                try:
                    delay = min(5.0, max(0.5, float(retry_after)))
                except ValueError:
                    delay = 1.0
                time.sleep(delay)
                continue
            self._raise_error(response)
        raise NotionAPIError("Notion request failed")

    def _raise_error(self, response: httpx.Response) -> None:
        try:
            data = response.json()
            message = data.get("message") or data.get("code") or response.reason_phrase
        except ValueError:
            message = response.text[:500] or response.reason_phrase
        hints = {
            401: "Check the Notion token.",
            403: "Give the connection Insert/Update Content permission and connect it to this page.",
            404: "The page was not found or has not been shared with the connection.",
        }
        hint = hints.get(response.status_code, "")
        raise NotionAPIError(
            f"Notion API error {response.status_code}: {message}"
            + (f" {hint}" if hint else "")
        )

    def _page_url(self, page_id: str) -> str:
        return f"https://www.notion.so/{page_id.replace('-', '')}"
