import tempfile
import unittest
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from app.core.parser.pptx_parser import PPTXParser


class FallbackPPTXParser(PPTXParser):
    def _find_soffice(self):
        return None


class PowerPointParserTests(unittest.TestCase):
    def test_fallback_preserves_picture_position_and_text(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "red.png"
            Image.new("RGB", (100, 100), (240, 10, 10)).save(image_path)

            deck_path = Path(directory) / "deck.pptx"
            deck = Presentation()
            slide = deck.slides.add_slide(deck.slide_layouts[6])
            text_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(4), Inches(1))
            text_box.text = "Neural Network Diagram"
            slide.shapes.add_picture(
                str(image_path), Inches(5), Inches(2), width=Inches(1), height=Inches(1)
            )
            deck.save(deck_path)

            parser = FallbackPPTXParser(str(deck_path))
            pages = parser.parse(dpi=96)
            self.assertEqual(parser.render_backend, "fallback")
            self.assertEqual(len(pages), 1)
            self.assertIn("Neural Network Diagram", pages[0].text)
            self.assertTrue(pages[0].has_images)

            rendered = Image.open(pages[0].screenshot_path).convert("RGB")
            pixel = rendered.getpixel((int(5.5 * 96), int(2.5 * 96)))
            self.assertGreater(pixel[0], 200)
            self.assertLess(pixel[1], 50)
            parser.cleanup()
            self.assertFalse(Path(pages[0].screenshot_path).exists())


if __name__ == "__main__":
    unittest.main()
