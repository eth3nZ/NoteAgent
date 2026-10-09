import tempfile
import unittest
from pathlib import Path

from app.core.agent.self_improving import GenerationResult
from app.core.parser.pdf_parser import PageContent
from app.core.pipeline import Pipeline
from app.core.vision.vision_client import PageAnalysis


class FakeParser:
    def __init__(self):
        self.cleaned = False

    def parse(self, dpi=160):
        return [
            PageContent(1, "alpha beta", "/tmp/page1.png", False),
            PageContent(2, "gamma delta", "/tmp/page2.png", True),
        ]

    def cleanup(self):
        self.cleaned = True


class FakeVisionClient:
    def analyze_page(self, page_num, text, screenshot_path, language):
        return PageAnalysis(page_num, f"visual-{page_num}: {text}", "raw")


class FakeGenerator:
    def __init__(self):
        self.raw_content = ""

    def generate(self, raw_content, language, progress_callback=None, stream_callback=None):
        self.raw_content = raw_content
        return GenerationResult(
            "## Concepts\n\n- alpha beta\n- gamma delta\n- visual-1\n- visual-2",
            92,
            1,
            [],
        )


class PipelineTests(unittest.TestCase):
    def test_pipeline_uses_visual_analysis_and_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "Lecture One.pdf"
            input_path.write_bytes(b"placeholder")
            existing = Path(directory) / "Lecture One_notes.md"
            existing.write_text("keep me", encoding="utf-8")

            parser = FakeParser()
            generator = FakeGenerator()
            pipeline = Pipeline(
                FakeVisionClient(), self_improving_generator=generator
            )
            pipeline._make_parser = lambda _: parser
            result = pipeline.run(
                str(input_path), directory, output_format="md", language="en"
            )

            self.assertTrue(parser.cleaned)
            self.assertIn("visual-1", generator.raw_content)
            self.assertIn("alpha beta", generator.raw_content)
            self.assertTrue(result.output_path.endswith("Lecture One_notes_2.md"))
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep me")
            output = Path(result.output_path).read_text(encoding="utf-8")
            self.assertTrue(output.startswith("# Lecture One"))

    def test_pipeline_writes_valid_notebook(self):
        import nbformat

        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "Demo.pdf"
            input_path.write_bytes(b"placeholder")
            parser = FakeParser()
            pipeline = Pipeline(
                FakeVisionClient(), self_improving_generator=FakeGenerator()
            )
            pipeline._make_parser = lambda _: parser
            result = pipeline.run(
                str(input_path), directory, output_format="ipynb", language="en"
            )
            notebook = nbformat.read(result.output_path, as_version=4)
            self.assertGreaterEqual(len(notebook.cells), 2)
            self.assertEqual(notebook.cells[0].source, "# Demo")


if __name__ == "__main__":
    unittest.main()
