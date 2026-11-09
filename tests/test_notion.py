import json
import unittest

import httpx

from app.core.notion import NotionClient, prepare_notion_markdown


class NotionTests(unittest.TestCase):
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
