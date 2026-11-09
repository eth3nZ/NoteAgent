import json
import gzip
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from PIL import Image

from app.core.cloud import BASE_URL, FREE_MODEL, CloudClient, CloudError, ModelConfig, RequestCancelled, SegmentResponse


def catalog(model=FREE_MODEL):
    return httpx.Response(200, json={"data": [{"id": model, "context_length": 1000000,
                                              "architecture": {"input_modalities": ["text", "image"]}}]})


class CloudTests(unittest.TestCase):
    def test_compressed_http_error_preserves_status_and_retry_after(self):
        body = gzip.compress(json.dumps({"error": {"message": "too many requests"}}).encode())
        class Compressed(httpx.SyncByteStream):
            def __iter__(self):
                yield body[:10]
                yield body[10:]
        client = CloudClient(ModelConfig(api_key="test"), httpx.MockTransport(lambda request: httpx.Response(
            429, headers={"content-encoding": "gzip", "content-type": "application/json", "retry-after": "7"},
            stream=Compressed())))
        with self.assertRaises(CloudError) as caught:
            client._send({}, [1], None)
        self.assertEqual(caught.exception.kind, "rate_limit")
        self.assertEqual(caught.exception.retry_after, 7)

    def test_fixed_free_image_route_and_stream_usage(self):
        seen = []

        def handler(request):
            if request.method == "GET":
                return catalog()
            payload = json.loads(request.content)
            seen.append(payload)
            text = json.dumps({"notes": [{"page": 7, "markdown": "## Formula\n\n$x=2$"}]})
            events = [{"model": FREE_MODEL, "provider": "NVIDIA", "choices": [{"delta": {"content": text}, "finish_reason": "stop"}]},
                      {"choices": [], "usage": {"prompt_tokens": 700, "completion_tokens": 30, "cost": 0}}]
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  text="".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n")

        client = CloudClient(ModelConfig(api_key="test", providers=("nvidia",)), httpx.MockTransport(handler))
        client.prepare()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "page.png"
            Image.new("RGB", (100, 100), "white").save(path)
            result = client.generate_segment([SimpleNamespace(page_num=7, text="x = 2", screenshot_path=str(path))])
        self.assertEqual(result.notes, {7: "## Formula\n\n$x=2$"})
        self.assertEqual(result.input_tokens, 700)
        self.assertEqual(seen[0]["model"], FREE_MODEL)
        self.assertNotIn("models", seen[0])
        self.assertEqual(seen[0]["provider"]["max_price"], {"prompt": 0, "completion": 0, "image": 0, "request": 0})
        self.assertEqual(seen[0]["provider"]["only"], ["nvidia"])
        self.assertTrue(any(part["type"] == "image_url" for part in seen[0]["messages"][1]["content"]))

    def test_obviously_wrong_language_is_rejected_without_translating_source_code(self):
        english = "This page explains the important relationships between inputs weights and outputs. " * 7
        client = CloudClient(ModelConfig(api_key="test"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"page.png"
            Image.new("RGB", (30,30), "white").save(path)
            page = SimpleNamespace(page_num=1,text="source",screenshot_path=str(path))
            for markdown, expected in [(english,"language"), ("## 代码\n```python\nprint("+repr(english)+")\n```", "stop")]:
                response = SegmentResponse({1:markdown}, "stop", FREE_MODEL)
                with patch.object(client,"_send",return_value=response):
                    self.assertEqual(client.generate_segment([page],language="zh").finish_reason,expected)

    def test_error_categories_do_not_confuse_region_and_quota(self):
        client = CloudClient(ModelConfig(api_key="test"))
        for status, message, expected in [(403, "country not supported", "region"),
                                          (429, "free-models-per-day", "daily_quota"),
                                          (429, "too many requests", "rate_limit"),
                                          (401, "bad key", "config"), (402, "credits", "credits"),
                                          (404, "No endpoints found matching your data policy", "privacy"),
                                          (404, "No endpoints found that support the requested parameters", "capability"),
                                          (404, "No endpoints found matching provider routing preferences", "routing"),
                                          (400, "context length exceeded", "context")]:
            with self.subTest(status=status, message=message), self.assertRaises(CloudError) as caught:
                client._raise_error(httpx.Response(status, json={"error": {"message": message}}))
            self.assertEqual(caught.exception.kind, expected)

    def test_automatic_routers_are_rejected(self):
        for model in ("openrouter/free", "openrouter/auto"):
            with self.subTest(model=model), self.assertRaises(ValueError):
                CloudClient(ModelConfig(api_key="test", model=model))
        with self.assertRaises(ValueError):
            CloudClient(ModelConfig(api_key="test", model="qwen/qwen3.8-27b"))

    def test_image_model_without_strict_schema_support_is_rejected_before_inference(self):
        calls=[]
        def handler(request):
            calls.append(request.method)
            return httpx.Response(200,json={"data":[{"id":FREE_MODEL,"context_length":1000000,
                "architecture":{"input_modalities":["text","image"]},"supported_parameters":["response_format"]}]})
        client=CloudClient(ModelConfig(api_key="test"),httpx.MockTransport(handler))
        with self.assertRaises(CloudError) as caught:
            client.prepare()
        self.assertEqual(caught.exception.kind,"capability")
        self.assertEqual(calls,["GET"])

    def test_gemini_route_is_allowed_and_mandatory_reasoning_is_not_disabled(self):
        model = "google/gemini-test"
        requests = []
        def handler(request):
            if request.method == "GET":
                return httpx.Response(200, json={"data": [{"id": model, "context_length": 1000000,
                    "architecture": {"input_modalities": ["text", "image"]},
                    "reasoning": {"mandatory": True, "supported_efforts": ["high", "medium", "low", "minimal"]}}]})
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"model": model, "provider": "Google AI Studio", "choices": [
                {"message": {"content": json.dumps({"notes": [{"page": 1, "markdown": "## Notes\nFormula: $x=2$"}]})},
                 "finish_reason": "stop"}], "usage": {"cost": .001}})
        client = CloudClient(ModelConfig(api_key="test", model=model, paid=True, providers=("google-ai-studio",)),
                             httpx.MockTransport(handler))
        client.prepare()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "page.png"
            Image.new("RGB", (100, 100), "white").save(path)
            response = client.generate_segment([SimpleNamespace(page_num=1, text="x=2", screenshot_path=str(path))])
        self.assertEqual(response.finish_reason, "stop")
        self.assertEqual(requests[0]["model"], model)
        self.assertNotIn("ignore", requests[0]["provider"])
        self.assertEqual(requests[0]["provider"]["only"], ["google-ai-studio"])
        self.assertEqual(requests[0]["reasoning"], {"effort": "minimal", "exclude": True})

    def test_paid_reservation_uses_context_bound_and_router_price_ceiling(self):
        client = CloudClient(ModelConfig(api_key="test", model="qwen/qwen3.8-27b", paid=True),
                             httpx.MockTransport(lambda r: catalog("qwen/qwen3.8-27b")))
        self.assertAlmostEqual(client.reservation_usd(), .047288)

    def test_missing_and_duplicate_page_entries_fail_validation(self):
        client = CloudClient(ModelConfig(api_key="test"))
        for rows in ([{"page": 1, "markdown": "one"}], [{"page": 1, "markdown": "one"}, {"page": 1, "markdown": "two"}]):
            client.transport = httpx.MockTransport(lambda r: httpx.Response(200, json={
                "choices": [{"message": {"content": json.dumps({"notes": rows})}, "finish_reason": "stop"}]}))
            result = client._send({}, [1, 2], None)
            self.assertEqual(result.finish_reason, "incomplete")
            self.assertFalse(result.notes)

    def test_truncated_reply_is_not_accepted(self):
        client = CloudClient(ModelConfig(api_key="test"), httpx.MockTransport(lambda r: httpx.Response(200, json={
            "choices": [{"message": {"content": '{"notes":['}, "finish_reason": "length"}],
            "usage": {"cost": 0}})))
        self.assertEqual(client._send({}, [1], None).finish_reason, "length")

    def test_cancelled_stream_is_not_retried_inside_client(self):
        client = CloudClient(ModelConfig(api_key="test"), httpx.MockTransport(lambda r: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text='data: {"choices": []}\n\n')))
        with self.assertRaises(RequestCancelled):
            client._send({}, [1], lambda: True)

    def test_provider_and_price_changes_preserve_accepted_content_fingerprint(self):
        first = ModelConfig(api_key="old", providers=("nvidia",))
        second = ModelConfig(api_key="new", providers=("another-host",), input_price=.1, output_price=2)
        self.assertEqual(first.fingerprint(), second.fingerprint())

    def test_stream_can_decode_unicode_across_byte_boundaries(self):
        data = {"choices": [{"delta": {"content": json.dumps({"notes": [{"page": 1, "markdown": "公式与代码"}]}, ensure_ascii=False)}, "finish_reason": "stop"}]}
        raw = ("data: " + json.dumps(data, ensure_ascii=False) + "\n\ndata: [DONE]\n\n").encode()
        class Bytes(httpx.SyncByteStream):
            def __iter__(self):
                for byte in raw:
                    yield bytes([byte])
        client = CloudClient(ModelConfig(api_key="test"), httpx.MockTransport(lambda r: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Bytes())))
        self.assertEqual(client._send({}, [1], None).notes[1], "公式与代码")

    def test_slow_stream_total_deadline_is_enforced(self):
        client = CloudClient(ModelConfig(api_key="test"), httpx.MockTransport(lambda r: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text="data: {}\n\n")))
        with patch("app.core.cloud.time.monotonic", side_effect=[0, 181]), self.assertRaises(CloudError) as caught:
            client._send({}, [1], None)
        self.assertTrue(caught.exception.uncertain)
