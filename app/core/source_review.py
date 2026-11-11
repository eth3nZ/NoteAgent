"""Local source-page viewing. Rendering and cleanup run outside the GUI thread."""

import hashlib
from pathlib import Path

import pymupdf

from app.core.parser.pptx_parser import PPTXParser


class SourceRenderer:
    def __init__(self, path, expected_digest):
        self.path = Path(path)
        self.expected_digest = expected_digest
        self.identity = None
        self.parser = None
        self.slides = None

    def render(self, page, cancel=None):
        if not self.path.is_file():
            raise ValueError('Source file is missing. Use Locate source to select the original document.')
        stat = self.path.stat()
        identity = (stat.st_size, stat.st_mtime_ns)
        if identity != self.identity:
            digest = hashlib.sha256()
            with self.path.open('rb') as source:
                for block in iter(lambda: source.read(1024*1024), b''):
                    self._check(cancel)
                    digest.update(block)
            if digest.hexdigest() != self.expected_digest:
                raise ValueError('This file differs from the document used to generate these notes. Select the original source.')
            self.identity = identity
        self._check(cancel)
        if self.path.suffix.lower() == '.pdf':
            with pymupdf.open(self.path) as document:
                if not 1 <= page <= len(document):
                    raise ValueError('Source page is out of range')
                source_page = document[page-1]
                scale = min(2, 1200/max(source_page.rect.width, source_page.rect.height))
                image = source_page.get_pixmap(matrix=pymupdf.Matrix(scale, scale)).tobytes('png')
        elif self.path.suffix.lower() == '.pptx':
            if self.slides is None:
                self.parser = PPTXParser(str(self.path))
                self.slides = self.parser.parse(dpi=120, cancel_callback=cancel)
            if not 1 <= page <= len(self.slides):
                raise ValueError('Source slide is out of range')
            image = Path(self.slides[page-1].screenshot_path).read_bytes()
        else:
            raise ValueError('Choose the original PDF or PPTX')
        self._check(cancel)
        return image

    @staticmethod
    def _check(cancel):
        if cancel and cancel():
            raise InterruptedError('Source review cancelled')

    def close(self):
        if self.parser:
            self.parser.cleanup()
        self.parser = self.slides = None
