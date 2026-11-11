import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from app.core.parser.pptx_parser import PPTXParser


class FallbackPPTXParser(PPTXParser):
    def _find_soffice(self):
        return None


class PowerPointParserTests(unittest.TestCase):
    def make_deck(self, path, count=3):
        deck = Presentation()
        for number in range(1,count+1):
            slide = deck.slides.add_slide(deck.slide_layouts[6])
            slide.shapes.add_textbox(Inches(.5),Inches(.5),Inches(6),Inches(1)).text=f'Topic {number}'
        deck.save(path)

    def test_cached_slides_skip_fallback_rendering_and_keep_original_numbering(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'slides.pptx'
            self.make_deck(path)
            parser = FallbackPPTXParser(str(path))
            try:
                with patch.object(parser,'_render_slide_fallback',wraps=parser._render_slide_fallback) as render:
                    pages = parser.parse(dpi=72,skip_pages={1,3})
                self.assertEqual(render.call_count,1)
                self.assertEqual(render.call_args.args[1],1)
                self.assertEqual([page.page_num for page in pages],[1,2,3])
                self.assertTrue(all(f'Topic {page.page_num}' in page.text for page in pages))
                self.assertIsNone(pages[0].screenshot_path)
                self.assertIsNone(pages[2].screenshot_path)
                self.assertTrue(Path(pages[1].screenshot_path).is_file())
            finally:
                parser.cleanup()

    def test_cached_slides_skip_libreoffice_pdf_rasterization(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'slides.pptx'
            self.make_deck(path)
            parser = PPTXParser(str(path))
            class Converted:
                returncode = 0
                def communicate(self, timeout=None):
                    output = Path(parser.temp_dir)/'libreoffice/converted.pdf'
                    if not output.exists():
                        with pymupdf.open() as document:
                            for _ in range(3):
                                document.new_page(width=300,height=200)
                            document.save(output)
                    return b'',b''
                def poll(self):
                    return 0
            try:
                with patch.object(parser,'_find_soffice',return_value='fixture-soffice'), \
                     patch('app.core.parser.pptx_parser.subprocess.Popen',return_value=Converted()):
                    pages = parser.parse(dpi=72,skip_pages={1,3})
                self.assertEqual(parser.render_backend,'libreoffice')
                self.assertIsNone(pages[0].screenshot_path)
                self.assertIsNone(pages[2].screenshot_path)
                self.assertTrue(Path(pages[1].screenshot_path).is_file())
                self.assertEqual([path.name for path in Path(parser.temp_dir).glob('*.png')],['slide_0002.png'])
            finally:
                parser.cleanup()

    def test_fully_cached_pptx_does_not_start_libreoffice_or_draw_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'slides.pptx'
            self.make_deck(path)
            parser = PPTXParser(str(path))
            try:
                with patch.object(parser,'_render_with_libreoffice',side_effect=AssertionError('No conversion needed')), \
                     patch.object(parser,'_render_slide_fallback',side_effect=AssertionError('No images needed')):
                    pages = parser.parse(skip_pages={1,2,3})
                self.assertEqual(len(pages),3)
                self.assertTrue(all(page.screenshot_path is None for page in pages))
                self.assertTrue(all(page.text for page in pages))
            finally:
                parser.cleanup()

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
