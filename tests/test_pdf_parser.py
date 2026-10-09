import tempfile
import unittest
from pathlib import Path

import pymupdf
from PIL import Image

from app.core.parser.pdf_parser import PDFParser


class PDFParserTests(unittest.TestCase):
    def test_pdf_text_render_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "sample.pdf"
            document = pymupdf.open()
            page = document.new_page(width=400, height=300)
            page.insert_text((40, 60), "Hello PDF")
            page.draw_rect((40, 90, 180, 180), color=(0, 0, 1), fill=(0.8, 0.9, 1))
            document.save(pdf_path)
            document.close()

            parser = PDFParser(str(pdf_path))
            pages = parser.parse(dpi=96)
            self.assertEqual(len(pages), 1)
            self.assertIn("Hello PDF", pages[0].text)
            self.assertTrue(Path(pages[0].screenshot_path).exists())
            with Image.open(pages[0].screenshot_path) as rendered:
                self.assertGreater(rendered.width, 400)
            parser.cleanup()
            self.assertFalse(Path(pages[0].screenshot_path).exists())


if __name__ == "__main__":
    unittest.main()
