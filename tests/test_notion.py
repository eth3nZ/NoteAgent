import json
import unittest

import httpx

from app.core.notion import NotionClient


class NotionTests(unittest.TestCase):
    def test_create_child_page_with_markdown(self):
        seen = {}

        def handler(request):
            seen["request"] = request
            return httpx.Response(
                200,
                json={
                    "id": "01234567-89ab-cdef-0123-456789abcdef",
                    "url": "https://www.notion.so/created",
                },
            )

        client = NotionClient("ntn_test", transport=httpx.MockTransport(handler))
        result = client.create_child_page(
            "0123456789abcdef0123456789abcdef", "Lecture", "## Notes\n\nHello"
        )
        request = seen["request"]
        payload = json.loads(request.content)
        self.assertEqual(request.url.path, "/v1/pages")
        self.assertEqual(request.headers["notion-version"], "2026-03-11")
        self.assertEqual(payload["markdown"], "## Notes\n\nHello")
        self.assertEqual(payload["parent"]["page_id"], "01234567-89ab-cdef-0123-456789abcdef")
        self.assertEqual(result.url, "https://www.notion.so/created")

    def test_append_uses_markdown_endpoint(self):
        seen = {}

        def handler(request):
            seen["request"] = request
            return httpx.Response(200, json={"object": "page_markdown"})

        client = NotionClient("ntn_test", transport=httpx.MockTransport(handler))
        result = client.append_to_page(
            "0123456789abcdef0123456789abcdef", "# Appended"
        )
        request = seen["request"]
        payload = json.loads(request.content)
        self.assertEqual(request.method, "PATCH")
        self.assertTrue(request.url.path.endswith("/markdown"))
        self.assertEqual(payload["type"], "insert_content")
        self.assertEqual(payload["insert_content"]["position"], {"type": "end"})
        self.assertIn("0123456789abcdef", result.url)


if __name__ == "__main__":
    unittest.main()
