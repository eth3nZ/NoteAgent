"""End-to-end orchestration for document-to-notes conversion."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from app.core.agent.self_improving import GenerationResult, SelfImprovingGenerator
from app.core.generator.markdown import MarkdownGenerator, render_source_pages
from app.core.generator.notebook import NotebookGenerator
from app.core.maintenance import create_workspace
from app.core.notion import NotionClient, NotionPublishResult
from app.core.parser.pdf_parser import PDFParser
from app.core.parser.pptx_parser import PPTXParser
from app.core.vision.vision_client import ModelAPIError, PageAnalysis, VisionClient
from app.utils.helpers import (
    ensure_output_dir,
    get_file_extension,
    unique_output_path,
    validate_file_type,
)


class PipelineCancelled(RuntimeError):
    pass


@dataclass
class PipelineResult:
    output_path: str
    title: str
    page_count: int
    score: int
    iterations: int
    notion: Optional[NotionPublishResult] = None
    warnings: List[str] = field(default_factory=list)
    task_id: str = ""
    status: str = "completed"
    completed_pages: List[int] = field(default_factory=list)
    cost_usd: float = 0.0
    reason: str = ""
    retry_at: Optional[float] = None
    paid_eligible: bool = False
    free_requests_remaining: Optional[int] = None
    segment_pages: int = 8


class Pipeline:
    """Parse, visually understand, rewrite, export, and optionally publish."""

    def __init__(
        self,
        vision_client: VisionClient,
        self_improving_generator: Optional[SelfImprovingGenerator] = None,
        markdown_generator: Optional[MarkdownGenerator] = None,
        notebook_generator: Optional[NotebookGenerator] = None,
        store=None,
        paid_client=None,
        document_limit: float = .10,
        monthly_limit: float = 1.0,
    ):
        self.vision_client = vision_client
        self.generator = self_improving_generator or SelfImprovingGenerator(vision_client)
        self.md_generator = markdown_generator or MarkdownGenerator()
        self.nb_generator = notebook_generator or NotebookGenerator()
        self.store = store
        self.paid_client = paid_client
        self.document_limit = document_limit
        self.monthly_limit = monthly_limit

    def run(self, input_path: str, output_dir: str, output_format: str = "md",
            language: str = "zh", progress_callback=None, stream_callback=None,
            cancel_callback=None, notion_client=None, notion_page_id="", notion_mode="none",
            task_id=None, state_callback=None, allow_paid=False, action="generate",
            include_source_pages: Optional[bool] = None) -> PipelineResult:
        if hasattr(self.vision_client, "generate_segment") or (self.store is not None and action != "generate"):
            from app.core.jobs import DocumentJob
            return DocumentJob(self).run(
                input_path, output_dir, output_format, language, progress_callback,
                stream_callback, cancel_callback, notion_client, notion_page_id,
                notion_mode, task_id, state_callback, allow_paid, action, include_source_pages)
        # Keep the old injected page/generator API working for existing integrations.
        return self._run_legacy(input_path, output_dir, output_format, language,
                                progress_callback, stream_callback, cancel_callback,
                                notion_client, notion_page_id, notion_mode, bool(include_source_pages))

    def _run_legacy(
        self,
        input_path: str,
        output_dir: str,
        output_format: str = "md",
        language: str = "zh",
        progress_callback: Optional[Callable[[str], None]] = None,
        stream_callback: Optional[Callable[[str], None]] = None,
        cancel_callback: Optional[Callable[[], bool]] = None,
        notion_client: Optional[NotionClient] = None,
        notion_page_id: str = "",
        notion_mode: str = "none",
        include_source_pages: bool = False,
    ) -> PipelineResult:
        input_path = str(Path(input_path).expanduser().resolve())
        if not os.path.isfile(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")
        if not validate_file_type(input_path):
            raise ValueError("Only PDF and PPTX files are supported")
        if output_format not in {"md", "ipynb"}:
            raise ValueError("Output format must be 'md' or 'ipynb'")
        if language not in {"zh", "en"}:
            raise ValueError("Language must be 'zh' or 'en'")
        ensure_output_dir(output_dir)

        title = self._document_title(input_path)
        warnings: List[str] = []
        parser = self._make_parser(input_path)

        self._progress(progress_callback, language, "正在解析文档…", "Parsing document…")
        try:
            pages = parser.parse(dpi=160)
            if not pages:
                raise ValueError("The document contains no readable pages")
            if isinstance(parser, PPTXParser):
                backend = "LibreOffice" if parser.render_backend == "libreoffice" else "layout fallback"
                self._progress(
                    progress_callback,
                    language,
                    f"已解析 {len(pages)} 页（渲染：{backend}）",
                    f"Parsed {len(pages)} pages (renderer: {backend})",
                )
            else:
                self._progress(
                    progress_callback,
                    language,
                    f"已解析 {len(pages)} 页",
                    f"Parsed {len(pages)} pages",
                )

            analyses: List[PageAnalysis] = []
            for index, page in enumerate(pages, start=1):
                self._check_cancel(cancel_callback)
                self._progress(
                    progress_callback,
                    language,
                    f"视觉解析第 {page.page_num} 页（{index}/{len(pages)}）…",
                    f"Analyzing page {page.page_num} ({index}/{len(pages)})…",
                )
                try:
                    analysis = self.vision_client.analyze_page(
                        page_num=page.page_num,
                        text=page.text,
                        screenshot_path=page.screenshot_path or "",
                        language=language,
                    )
                    analysis.content = self.md_generator.sanitize_fragment(analysis.content)
                except ModelAPIError as exc:
                    if "429" not in str(exc):
                        raise
                    warning = (
                        "免费 API 限额已用尽；其余页面使用提取文本继续生成。"
                        if language == "zh"
                        else "The free API limit was reached; remaining pages use extracted text."
                    )
                    warnings.append(warning)
                    self._progress(progress_callback, language, warning, warning)
                    analyses.extend(self._text_fallback(item, language) for item in pages[index - 1:])
                    break
                analyses.append(analysis)
        finally:
            parser.cleanup()

        self._check_cancel(cancel_callback)
        source_material = self._combine_source(pages, analyses, language)
        self._progress(
            progress_callback,
            language,
            "正在合并、改写并检查笔记…",
            "Merging, rewriting, and checking notes…",
        )
        try:
            generation = self.generator.generate(
                raw_content=source_material,
                language=language,
                progress_callback=progress_callback,
                stream_callback=stream_callback,
            )
        except ModelAPIError as exc:
            # The valuable page-level results already exist. Do not discard them
            # when a final free-tier request is unavailable.
            warning = (
                f"最终改写不可用，已保留逐页视觉笔记：{exc}"
                if language == "zh"
                else f"Final rewriting was unavailable; page notes were preserved: {exc}"
            )
            warnings.append(warning)
            generation = GenerationResult(
                content=self.md_generator.generate(analyses, title=title, language=language),
                score=0,
                iterations=1,
                issues=[warning],
            )

        self._check_cancel(cancel_callback)
        audit = self.md_generator.audit_document(generation.content, title)
        markdown = render_source_pages(audit.content, include_source_pages)
        if audit.corrections:
            self._progress(
                progress_callback,
                language,
                f"格式审核已自动修正 {len(audit.corrections)} 类问题",
                f"Format review automatically corrected {len(audit.corrections)} issue type(s)",
            )
        if audit.issues:
            warning = (
                "格式审核仍发现：" + "；".join(audit.issues)
                if language == "zh"
                else "Format review still found: " + "; ".join(audit.issues)
            )
            warnings.append(warning)
            generation.score = min(generation.score, max(0, 100 - 15 * len(audit.issues)))
        output_path = self._save_output(markdown, input_path, output_dir, output_format, title)
        self._progress(
            progress_callback,
            language,
            f"本地文件已保存：{output_path}",
            f"Saved local file: {output_path}",
        )

        notion_result = None
        if notion_mode != "none":
            if notion_client is None or not notion_page_id.strip():
                warnings.append(
                    "Notion publishing was requested but its token/page ID is missing."
                )
            else:
                self._check_cancel(cancel_callback)
                self._progress(
                    progress_callback,
                    language,
                    "正在发布到 Notion…",
                    "Publishing to Notion…",
                )
                try:
                    if notion_mode == "create_child":
                        notion_result = notion_client.create_child_page(
                            notion_page_id, title, markdown
                        )
                    elif notion_mode == "append":
                        notion_result = notion_client.append_to_page(notion_page_id, markdown)
                    else:
                        raise ValueError(f"Unknown Notion mode: {notion_mode}")
                except Exception as exc:
                    warning = f"Notion publish failed (local file is safe): {exc}"
                    warnings.append(warning)
                    self._progress(progress_callback, language, warning, warning)

        return PipelineResult(
            output_path=output_path,
            title=title,
            page_count=len(pages),
            score=generation.score,
            iterations=generation.iterations,
            notion=notion_result,
            warnings=warnings,
        )

    def _make_parser(self, input_path: str):
        pdf = get_file_extension(input_path) == ".pdf"
        workspace = None
        if self.store is not None:
            root = self.store.path.parent/'workspaces'
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            workspace = create_workspace('pdf' if pdf else 'pptx', root)
        return PDFParser(input_path, workspace) if pdf else PPTXParser(input_path, workspace)

    def _combine_source(self, pages: list, analyses: List[PageAnalysis], language: str) -> str:
        analysis_by_page = {analysis.page_num: analysis for analysis in analyses}
        sections = []
        for page in pages:
            analysis = analysis_by_page.get(page.page_num)
            extracted_label = "提取文本" if language == "zh" else "Extracted text"
            visual_label = "视觉解析" if language == "zh" else "Visual analysis"
            sections.append(
                f"<!-- NOTEAGENT_PAGE:{page.page_num} -->\n"
                f"## Page {page.page_num}\n\n"
                f"**{extracted_label}**\n\n{page.text or '(none)'}\n\n"
                f"**{visual_label}**\n\n{analysis.content if analysis else '(none)'}"
            )
        return "\n\n".join(sections)

    def _text_fallback(self, page, language: str) -> PageAnalysis:
        empty = "*（未提取到文本）*" if language == "zh" else "*(No text extracted)*"
        content = self.md_generator.sanitize_fragment(page.text.strip()) if page.text else empty
        return PageAnalysis(page.page_num, content, content)

    def _save_output(
        self,
        markdown: str,
        input_path: str,
        output_dir: str,
        output_format: str,
        title: str,
    ) -> str:
        suffix = ".md" if output_format == "md" else ".ipynb"
        output_path = unique_output_path(output_dir, input_path, suffix)
        if output_format == "md":
            self.md_generator.save(markdown, output_path)
        else:
            body = markdown
            first_line = f"# {title}\n"
            if body.startswith(first_line):
                body = body[len(first_line):].lstrip()
            notebook = self.nb_generator.generate_from_markdown(body, title=title)
            self.nb_generator.save(notebook, output_path)
        return output_path

    def _document_title(self, input_path: str) -> str:
        return Path(input_path).stem.replace("_", " ").replace("-", " ").strip() or "Course Notes"

    def _check_cancel(self, cancel_callback: Optional[Callable[[], bool]]) -> None:
        if cancel_callback and cancel_callback():
            raise PipelineCancelled("Note generation was cancelled")

    def _progress(
        self,
        callback: Optional[Callable[[str], None]],
        language: str,
        zh: str,
        en: str,
    ) -> None:
        if callback:
            callback(zh if language == "zh" else en)
