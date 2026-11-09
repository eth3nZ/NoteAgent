import json
import tempfile
import unittest
from pathlib import Path

import httpx
import pymupdf
from PIL import Image

from app.core.pipeline import Pipeline
from app.core.vision.vision_client import VisionClient


class EndToEndTests(unittest.TestCase):
    def test_code_page_gets_focused_recovery_when_initial_pass_only_summarizes(self):
        requests = []
        responses = [
            "The Python script computes predictions from a DataFrame.",
            "```python\nvalue = 4\nprint(value)\n```\n\n```text\n4\n```",
        ]

        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": responses[len(requests) - 1]}}]},
            )

        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (100, 100), "white").save(image_path)
            client = VisionClient(
                base_url="http://127.0.0.1:11434/v1",
                model="stub-vision",
                transport=httpx.MockTransport(handler),
            )
            analysis = client.analyze_page(12, "12", str(image_path), "en")
            self.assertEqual(len(requests), 2)
            self.assertIn("```python", analysis.content)
            self.assertIn("```text", analysis.content)

    def test_real_pdf_http_payload_and_pipeline(self):
        requests = []

        def model_handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": "# Alpha Notes\n\n## Topic\n\n- alpha beta\n- visual diagram"
                            }
                        }
                    ]
                },
            )

        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "Alpha.pdf"
            document = pymupdf.open()
            page = document.new_page()
            page.insert_text((72, 72), "alpha beta visual diagram")
            document.save(pdf_path)
            document.close()

            client = VisionClient(
                base_url="http://127.0.0.1:11434/v1",
                model="stub-vision",
                llm_model="stub-text",
                transport=httpx.MockTransport(model_handler),
            )
            result = Pipeline(client).run(
                str(pdf_path), directory, output_format="md", language="en"
            )
            output = Path(result.output_path).read_text(encoding="utf-8")
            self.assertIn("Alpha Notes", output)
            self.assertGreaterEqual(len(requests), 2)
            first_content = requests[0]["messages"][1]["content"]
            self.assertEqual(first_content[1]["type"], "image_url")
            self.assertTrue(
                first_content[1]["image_url"]["url"].startswith("data:image/png;base64,")
            )
            self.assertEqual(requests[1]["model"], "stub-text")


if __name__ == "__main__":
    unittest.main()
