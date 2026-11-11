"""PowerPoint parser with real rendering and a layout-aware fallback."""

import io
import os
import shutil
import subprocess
from app.core.maintenance import create_workspace
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE


@dataclass
class PageContent:
    """Represents content from a single slide."""

    page_num: int
    text: str
    screenshot_path: Optional[str] = None
    has_images: bool = False


class PPTXParser:
    """Extract text and render slides to images suitable for a vision model.

    LibreOffice is used when available because ``python-pptx`` cannot render a
    slide. The fallback renderer preserves shape coordinates, pictures, text
    boxes, and simple tables instead of creating the old text-only fake slide.
    """

    def __init__(self, pptx_path: str, temp_dir: Optional[str] = None):
        self.pptx_path = pptx_path
        self.temp_dir = temp_dir or create_workspace("pptx")
        self.prs: Optional[Presentation] = None
        self.render_backend = "fallback"

    def parse(self, dpi: int = 160, cancel_callback=None, skip_pages=None) -> List[PageContent]:
        self.cancel_callback = cancel_callback
        self._skip_pages = set(skip_pages or ())
        self._check_cancel()
        if not os.path.exists(self.pptx_path):
            raise FileNotFoundError(f"PPTX not found: {self.pptx_path}")

        try:
            self.prs = Presentation(self.pptx_path)
        except Exception as exc:
            raise ValueError(f"Unable to open PowerPoint file: {exc}") from exc

        rendered = [] if set(range(1, len(self.prs.slides)+1)) <= self._skip_pages else self._render_with_libreoffice(dpi)
        if rendered and len(rendered) == len(self.prs.slides):
            self.render_backend = "libreoffice"
        else:
            rendered = []
            self.render_backend = "fallback"

        pages: List[PageContent] = []
        for slide_index, slide in enumerate(self.prs.slides):
            self._check_cancel()
            text, has_images = self._extract_slide_content(slide)
            if slide_index+1 in self._skip_pages:
                screenshot_path = None
            elif rendered:
                screenshot_path = rendered[slide_index]
            else:
                screenshot_path = self._render_slide_fallback(slide, slide_index, dpi)
            pages.append(
                PageContent(
                    page_num=slide_index + 1,
                    text=text,
                    screenshot_path=screenshot_path,
                    has_images=has_images,
                )
            )
        return pages

    def _check_cancel(self):
        callback = getattr(self, "cancel_callback", None)
        if callback and callback():
            raise InterruptedError("PowerPoint rendering cancelled")

    def _extract_slide_content(self, slide) -> Tuple[str, bool]:
        text_parts: List[str] = []
        has_images = False

        for shape in self._iter_shapes(slide.shapes):
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                has_images = True
            if getattr(shape, "has_text_frame", False):
                for paragraph in shape.text_frame.paragraphs:
                    paragraph_text = paragraph.text.strip()
                    if paragraph_text:
                        text_parts.append(paragraph_text)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    text_parts.append(" | ".join(cells))

        try:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                text_parts.append(f"[Speaker notes]\n{notes}")
        except (AttributeError, KeyError, ValueError):
            pass

        return "\n".join(text_parts), has_images

    def _iter_shapes(self, shapes) -> Iterable:
        for shape in shapes:
            yield shape
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from self._iter_shapes(shape.shapes)

    def _find_soffice(self) -> Optional[str]:
        configured = os.environ.get("NOTEAGENT_SOFFICE_PATH", "").strip()
        candidates = [
            configured,
            shutil.which("soffice"),
            shutil.which("libreoffice"),
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
            "/usr/lib/libreoffice/program/soffice",
        ]
        for candidate in candidates:
            if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None

    def _render_with_libreoffice(self, dpi: int) -> List[Optional[str]]:
        soffice = self._find_soffice()
        if not soffice:
            return []

        render_dir = Path(self.temp_dir) / "libreoffice"
        render_dir.mkdir(parents=True, exist_ok=True)
        profile_dir = Path(self.temp_dir) / "lo_profile"
        command = [
            soffice,
            "--headless",
            f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(render_dir),
            str(Path(self.pptx_path).resolve()),
        ]
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            started = time.monotonic()
            try:
                while True:
                    self._check_cancel()
                    if time.monotonic() - started > 120:
                        raise subprocess.TimeoutExpired(command, 120)
                    try:
                        process.communicate(timeout=.1)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
        except (OSError, subprocess.SubprocessError):
            return []

        pdfs = sorted(render_dir.glob("*.pdf"))
        if process.returncode != 0 or not pdfs:
            return []

        output_paths: List[Optional[str]] = []
        try:
            with fitz.open(str(pdfs[0])) as document:
                matrix = fitz.Matrix(dpi / 72, dpi / 72)
                for page_index, page in enumerate(document):
                    self._check_cancel()
                    if page_index+1 in self._skip_pages:
                        output_paths.append(None)
                        continue
                    output_path = Path(self.temp_dir) / f"slide_{page_index + 1:04d}.png"
                    page.get_pixmap(matrix=matrix, alpha=False).save(str(output_path))
                    output_paths.append(str(output_path))
        except (fitz.FileDataError, RuntimeError, OSError):
            return []
        return output_paths

    def _render_slide_fallback(self, slide, slide_index: int, dpi: int) -> str:
        assert self.prs is not None
        width = max(960, int(self.prs.slide_width / 914400 * dpi))
        height = max(540, int(self.prs.slide_height / 914400 * dpi))
        canvas = Image.new("RGB", (width, height), self._background_color(slide))
        draw = ImageDraw.Draw(canvas)
        scale_x = width / self.prs.slide_width
        scale_y = height / self.prs.slide_height

        for shape in slide.shapes:
            try:
                box = (
                    int(shape.left * scale_x),
                    int(shape.top * scale_y),
                    max(1, int(shape.width * scale_x)),
                    max(1, int(shape.height * scale_y)),
                )
            except (AttributeError, TypeError):
                continue

            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                self._draw_picture(canvas, shape, box)
                continue

            self._draw_shape_background(draw, shape, box)
            if getattr(shape, "has_table", False):
                self._draw_table(draw, shape, box, dpi)
            elif getattr(shape, "has_text_frame", False):
                self._draw_text_frame(draw, shape, box, dpi)

        output_path = Path(self.temp_dir) / f"slide_{slide_index + 1:04d}.png"
        canvas.save(output_path, "PNG")
        return str(output_path)

    def _background_color(self, slide) -> Tuple[int, int, int]:
        try:
            rgb = slide.background.fill.fore_color.rgb
            if rgb is not None:
                return tuple(rgb)
        except (AttributeError, TypeError, ValueError):
            pass
        return (255, 255, 255)

    def _draw_picture(self, canvas: Image.Image, shape, box: Tuple[int, int, int, int]) -> None:
        x, y, width, height = box
        try:
            picture = Image.open(io.BytesIO(shape.image.blob)).convert("RGB")
            crop_left = int(picture.width * float(getattr(shape, "crop_left", 0) or 0))
            crop_top = int(picture.height * float(getattr(shape, "crop_top", 0) or 0))
            crop_right = int(picture.width * (1 - float(getattr(shape, "crop_right", 0) or 0)))
            crop_bottom = int(picture.height * (1 - float(getattr(shape, "crop_bottom", 0) or 0)))
            if crop_right > crop_left and crop_bottom > crop_top:
                picture = picture.crop((crop_left, crop_top, crop_right, crop_bottom))
            picture = picture.resize((width, height), Image.Resampling.LANCZOS)
            canvas.paste(picture, (x, y))
        except (OSError, ValueError, AttributeError):
            ImageDraw.Draw(canvas).rectangle((x, y, x + width, y + height), outline=(180, 180, 180))

    def _draw_shape_background(self, draw: ImageDraw.ImageDraw, shape, box) -> None:
        x, y, width, height = box
        fill_color = None
        line_color = None
        try:
            if shape.fill.type is not None and shape.fill.fore_color.rgb is not None:
                fill_color = tuple(shape.fill.fore_color.rgb)
        except (AttributeError, TypeError, ValueError):
            pass
        try:
            if shape.line.color.rgb is not None:
                line_color = tuple(shape.line.color.rgb)
        except (AttributeError, TypeError, ValueError):
            pass
        if fill_color or line_color:
            draw.rectangle(
                (x, y, x + width, y + height),
                fill=fill_color,
                outline=line_color,
                width=2 if line_color else 1,
            )

    def _font(self, size: int, bold: bool = False) -> ImageFont.ImageFont:
        regular_candidates = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        bold_candidates = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        for path in (bold_candidates + regular_candidates if bold else regular_candidates):
            try:
                return ImageFont.truetype(path, max(10, size))
            except OSError:
                continue
        return ImageFont.load_default()

    def _draw_text_frame(self, draw: ImageDraw.ImageDraw, shape, box, dpi: int) -> None:
        x, y, width, height = box
        padding = max(4, int(dpi / 24))
        cursor_y = y + padding
        max_y = y + height - padding

        for paragraph in shape.text_frame.paragraphs:
            text = paragraph.text.strip()
            if not text:
                cursor_y += padding
                continue
            run = paragraph.runs[0] if paragraph.runs else None
            point_size = 18.0
            bold = False
            color = (30, 30, 30)
            if run is not None:
                if run.font.size is not None:
                    point_size = float(run.font.size.pt)
                bold = bool(run.font.bold)
                try:
                    if run.font.color.rgb is not None:
                        color = tuple(run.font.color.rgb)
                except (AttributeError, TypeError, ValueError):
                    pass
            font = self._font(int(point_size * dpi / 72), bold)
            wrapped = self._wrap_text(draw, text, font, max(20, width - 2 * padding))
            line_height = max(12, int(point_size * dpi / 60))
            for line in wrapped:
                if cursor_y + line_height > max_y:
                    return
                draw.text((x + padding, cursor_y), line, fill=color, font=font)
                cursor_y += line_height
            cursor_y += max(2, padding // 2)

    def _wrap_text(self, draw, text: str, font, max_width: int) -> List[str]:
        words = text.split()
        if len(words) <= 1:
            words = list(text)
            separator = ""
        else:
            separator = " "
        lines: List[str] = []
        current = ""
        for word in words:
            candidate = word if not current else f"{current}{separator}{word}"
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if current and bbox[2] - bbox[0] > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines or [""]

    def _draw_table(self, draw: ImageDraw.ImageDraw, shape, box, dpi: int) -> None:
        x, y, width, height = box
        rows = len(shape.table.rows)
        columns = len(shape.table.columns)
        if not rows or not columns:
            return
        cell_width = width / columns
        cell_height = height / rows
        font = self._font(max(10, int(11 * dpi / 72)))
        for row_index, row in enumerate(shape.table.rows):
            for column_index, cell in enumerate(row.cells):
                left = int(x + column_index * cell_width)
                top = int(y + row_index * cell_height)
                right = int(x + (column_index + 1) * cell_width)
                bottom = int(y + (row_index + 1) * cell_height)
                draw.rectangle((left, top, right, bottom), outline=(150, 150, 150), width=1)
                text = " ".join(cell.text.split())
                lines = self._wrap_text(draw, text, font, max(10, right - left - 6))
                draw.multiline_text((left + 3, top + 3), "\n".join(lines[:4]), fill=(30, 30, 30), font=font)

    def cleanup(self) -> None:
        self.prs = None
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
