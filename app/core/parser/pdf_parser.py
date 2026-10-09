"""
PDF Parser - Extracts text and generates page screenshots using PyMuPDF.
"""

import os
import tempfile
from dataclasses import dataclass
from typing import List, Optional

import pymupdf as fitz


@dataclass
class PageContent:
    """Represents content from a single page."""
    page_num: int
    text: str
    screenshot_path: Optional[str] = None
    has_images: bool = False


class PDFParser:
    """Parse PDF files: extract text and generate page screenshots."""

    def __init__(self, pdf_path: str, temp_dir: Optional[str] = None):
        """
        Args:
            pdf_path: Path to the PDF file
            temp_dir: Directory for temporary screenshot files
        """
        self.pdf_path = pdf_path
        self.temp_dir = temp_dir or tempfile.mkdtemp(prefix="noteagent_pdf_")
        self.doc: Optional[fitz.Document] = None

    def parse(self, dpi: int = 200) -> List[PageContent]:
        """
        Parse PDF: extract text and generate screenshots for each page.

        Args:
            dpi: Resolution for page screenshots (higher = clearer but larger)

        Returns:
            List of PageContent for each page
        """
        if not os.path.exists(self.pdf_path):
            raise FileNotFoundError(f"PDF not found: {self.pdf_path}")

        try:
            self.doc = fitz.open(self.pdf_path)
        except (fitz.FileDataError, RuntimeError) as exc:
            raise ValueError(f"Unable to open PDF: {exc}") from exc
        pages: List[PageContent] = []

        for page_num in range(len(self.doc)):
            page = self.doc[page_num]

            # Extract text
            text = page.get_text().strip()

            # Generate screenshot
            screenshot_path = self._render_page(page, page_num, dpi)

            # Check for embedded images
            image_list = page.get_images(full=True)
            has_images = len(image_list) > 0

            pages.append(PageContent(
                page_num=page_num + 1,
                text=text,
                screenshot_path=screenshot_path,
                has_images=has_images
            ))

        return pages

    def _render_page(self, page: fitz.Page, page_num: int, dpi: int) -> str:
        """Render a page as a PNG screenshot."""
        zoom = dpi / 72  # Default PDF resolution is 72 DPI
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)

        # Save as PNG
        filename = f"page_{page_num + 1:04d}.png"
        filepath = os.path.join(self.temp_dir, filename)
        pix.save(filepath)

        return filepath

    def cleanup(self):
        """Remove temporary screenshot files."""
        if self.doc is not None:
            self.doc.close()
            self.doc = None
        if os.path.exists(self.temp_dir):
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
