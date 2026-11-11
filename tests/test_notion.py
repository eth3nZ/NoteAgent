import json
import unittest

import httpx

from app.core.notion import NotionAPIError, NotionClient, prepare_notion_markdown


class NotionTests(unittest.TestCase):
    def test_error_bodies_and_network_exceptions_do_not_expose_content(self):
        private = "private-key-and-document-fragment"
        for body in ({"message": private}, private):
            def handler(request):
                return httpx.Response(401, **({"json": body} if isinstance(body, dict) else {"text": body}))
            client = NotionClient(private, transport=httpx.MockTransport(handler))
            with self.assertRaises(NotionAPIError) as caught:
                client.append_to_page("0123456789abcdef0123456789abcdef", private)
            self.assertNotIn(private, str(caught.exception))
            self.assertIn("401", str(caught.exception))
        def offline(request):
            raise httpx.ConnectError(private, request=request)
        with self.assertRaises(NotionAPIError) as caught:
            NotionClient(private, transport=httpx.MockTransport(offline)).append_to_page("0123456789abcdef0123456789abcdef", private)
        self.assertNotIn(private, str(caught.exception))

    def test_redirect_does_not_forward_notes_or_authorization(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(307, headers={"location": "https://untrusted.example/collect"})
        client = NotionClient("dummy", transport=httpx.MockTransport(handler))
        with self.assertRaisesRegex(NotionAPIError, "redirected"):
            client.append_to_page("0123456789abcdef0123456789abcdef", "private notes")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.host, "api.notion.com")

    def test_math_conversion_preserves_code_and_existing_dollar_math(self):
        markdown = r"""Inline \(x_i + 1\), existing $y=2$.
\[ \sigma(z) = \frac{1}{1+e^{-z}} \]
`r'\(not math\)'`
```python
pattern = r'\[literal\]'
print(r'\(unchanged\)')
```
~~~text
\(also unchanged\)
~~~
"""
        result = prepare_notion_markdown(markdown)
        self.assertIn("Inline $x_i + 1$, existing $y=2$.", result)
        self.assertIn("$$\n" + r"\sigma(z) = \frac{1}{1+e^{-z}}" + "\n$$", result)
        for snippet in ["`r'\\(not math\\)'`", "pattern = r'\\[literal\\]'", "print(r'\\(unchanged\\)')", "~~~text\n\\(also unchanged\\)\n~~~"]:
            self.assertIn(snippet, result)
        self.assertEqual(prepare_notion_markdown(result), result)

    def test_both_publication_modes_prepare_math(self):
        payloads = []
        def handler(request):
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "01234567-89ab-cdef-0123-456789abcdef"})
        client = NotionClient("test", transport=httpx.MockTransport(handler))
        page = "0123456789abcdef0123456789abcdef"
        client.create_child_page(page, "Lecture", r"Formula \(x=2\)")
        client.append_to_page(page, r"Formula \(x=2\)")
        self.assertEqual(payloads[0]["markdown"], "Formula $x=2$")
        self.assertIn("Formula $x=2$", payloads[1]["insert_content"]["content"])

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
