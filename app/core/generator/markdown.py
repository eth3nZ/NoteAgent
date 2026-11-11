"""
Markdown Generator - Saves notes as Markdown files.
"""

import re
from dataclasses import dataclass, field
from typing import List

from app.core.vision.vision_client import PageAnalysis
from app.core.markdown_text import OPEN_FENCE, map_prose, markdown_blocks, prose_text


def render_source_pages(content: str, include_source_pages: bool = False) -> str:
    """Hide generated page-reference lines while preserving code and source content."""
    if include_source_pages:
        return content
    lines, fence = [], None
    for line in content.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if marker:
            token, rest = marker.groups()
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not rest.strip():
                fence = None
        elif fence is None and re.fullmatch(
            r"[ \t]*>[ \t]*(?:Source:[ \t]*page[ \t]+\d+|来源[：:][ \t]*第[ \t]*\d+[ \t]*页)[ \t]*",
            line.rstrip("\r\n"), re.IGNORECASE,
        ):
            if lines and not lines[-1].strip():
                lines.pop()
            continue
        lines.append(line)
    return "".join(lines)


@dataclass
class MarkdownAuditResult:
    content: str
    corrections: List[str] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)


class MarkdownGenerator:
    """Generate and deterministically review Notion-compatible Markdown."""

    def generate(
        self,
        page_analyses: List[PageAnalysis],
        title: str = "课程笔记",
        language: str = "zh",
    ) -> str:
        """
        Generate a complete Markdown document from page analyses.

        Args:
            page_analyses: List of PageAnalysis from vision client
            title: Document title
            language: Output language

        Returns:
            Complete Markdown content
        """
        lines = [f"# {title}", ""]
        for analysis in page_analyses:
            content = self.sanitize_fragment(analysis.content or "")
            if not content:
                continue
            lines.append(content)
            if self._needs_source_reference(content):
                source_label = (
                    f"来源：第 {analysis.page_num} 页"
                    if language == "zh"
                    else f"Source: page {analysis.page_num}"
                )
                if not re.search(r"source\s+page|来源.{0,4}页", content, re.IGNORECASE):
                    lines.extend(["", f"> {source_label}"])
            lines.extend(["", "---", ""])

        return self.audit_document("\n".join(lines), title).content

    def save(self, content: str, output_path: str):
        """Save Markdown content to file."""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

    def prepare_document(self, content: str, title: str) -> str:
        """Normalize a model response into a standalone Markdown document."""
        return self.audit_document(content, title).content

    def sanitize_fragment(self, content: str) -> str:
        """Clean prose and incidental console chrome, preserving source code verbatim."""
        content = self._strip_markdown_wrappers(content.strip())
        blocks = []
        for fenced, block in markdown_blocks(content):
            if not fenced:
                blocks.append(self._sanitize_prose(block))
                continue
            language = OPEN_FENCE.match(block)[2].strip().lower()
            if language in {"text", "console", "terminal"}:
                block = "".join(line for line in block.splitlines(keepends=True) if not self._is_noise_line(line))
            blocks.append(block)
        return "".join(blocks).strip()

    def _sanitize_prose(self, content):
        content = re.sub(r"(?im)^[ \t]*<a[ \t]+[^>]*id=['\"]?page-\d+['\"]?[^>]*>[ \t]*</a>[ \t]*$", "", content)
        content = re.sub(r"(?im)^[ \t]*#{1,6}[ \t]+(?:page|slide|第)[ \t]*\d+(?:[ \t]*[页:]?[^\n]*)?$", "", content)
        lines, skip_metadata, skip_page_toc = [], False, False
        for line in content.splitlines(keepends=True):
            if re.match(r"^\s{0,3}#{1,6}\s+(?:Table of Contents|目录)\s*$", line, re.I):
                skip_page_toc = True
                continue
            if skip_page_toc:
                if not line.strip() or re.match(r"^\s*-\s+\[[^\]]+\]\(#page-\d+\)\s*$", line, re.I):
                    continue
                skip_page_toc = False
            if re.match(r"^\s{0,3}#{1,6}\s+(?:IDE Context|Interface Elements|Status Bar|Footer)\b", line, re.I):
                skip_metadata = True
                continue
            if skip_metadata:
                if re.match(r"^\s{0,3}#{1,6}\s+\S", line) or line.strip() in {"---", "***"}:
                    skip_metadata = False
                else:
                    continue
            if self._is_noise_line(line):
                continue
            if re.match(r"^\s*\*{0,2}(?:page|slide)\s*\d{1,3}\*{0,2}\s*$", line, re.I):
                continue
            lines.append(line)
        return re.sub(r"\n{3,}", "\n\n", "".join(lines))

    def audit_document(self, content: str, title: str) -> MarkdownAuditResult:
        """Apply safe fixes, then report any unresolved format defects."""
        corrections: List[str] = []
        original = content
        content = self.sanitize_fragment(content)
        if content != original.strip():
            corrections.append("Removed model wrappers, page anchors, or UI noise")

        content, fence_corrections = self._normalize_fenced_blocks(content)
        corrections.extend(fence_corrections)
        content, heading_corrections = self._normalize_headings(content, title)
        corrections.extend(heading_corrections)
        content = map_prose(content, lambda text: re.sub(r"\n(?:\s*---\s*\n){2,}", "\n\n---\n", text))
        content = map_prose(content, lambda text: re.sub(r"\n{3,}", "\n\n", text)).strip() + "\n"

        issues: List[str] = []
        prose = prose_text(content)
        if re.search(r"<a\s+[^>]*id=", prose, re.IGNORECASE):
            issues.append("HTML anchors remain")
        if self._has_unlabeled_opening_fence(content):
            issues.append("A fenced block has no language")
        if re.search(r"(?im)^#{1,6}\s+(?:page|slide|第)\s*\d+", prose):
            issues.append("Page-number headings remain")
        if any(self._is_noise_line(line) for line in prose.splitlines()):
            issues.append("Terminal or IDE noise remains")
        if self._count_document_titles(content) != 1:
            issues.append("Document must contain exactly one level-one title")

        return MarkdownAuditResult(
            content=content,
            corrections=list(dict.fromkeys(corrections)),
            issues=issues,
        )

    def _strip_markdown_wrappers(self, content: str) -> str:
        """Unwrap model Markdown fences without inspecting nested source-code bodies."""
        result, code_fence, wrapper = [], None, False
        for line in content.splitlines(keepends=True):
            match = OPEN_FENCE.match(line)
            if code_fence:
                result.append(line)
                if (match and match[1][0] == code_fence[0] and len(match[1]) >= len(code_fence)
                        and not match[2].strip()):
                    code_fence = None
                continue
            if match:
                if match[2].strip().lower() in {'markdown', 'md'}:
                    wrapper = True
                    continue
                if wrapper and not match[2].strip():
                    wrapper = False
                    continue
                code_fence = match[1]
            result.append(line)
        return ''.join(result).strip()

    def _normalize_fenced_blocks(self, content: str) -> tuple[str, List[str]]:
        corrections, normalized = [], []
        for fenced, block in markdown_blocks(content):
            if not fenced:
                normalized.append(block)
                continue
            lines = block.splitlines(keepends=True)
            match = OPEN_FENCE.match(lines[0])
            marker, language = match[1], match[2].strip()
            if not language:
                language = self._infer_fence_language("".join(lines[1:12]))
                corrections.append(f"Labeled an unlabeled block as {language}")
            elif language.lower() in {"console", "terminal", "shell"}:
                language = "text"
                corrections.append("Normalized terminal block language to text")
            lines[0] = marker + language + "\n"
            closing = OPEN_FENCE.match(lines[-1]) if len(lines) > 1 else None
            if not (closing and closing[1][0] == marker[0] and len(closing[1]) >= len(marker) and not closing[2].strip()):
                lines.append(("" if lines[-1].endswith("\n") else "\n") + marker + "\n")
                corrections.append("Closed an unterminated fenced block")
            normalized.append("".join(lines))
        return "".join(normalized), corrections

    def _normalize_headings(self, content: str, title: str) -> tuple[str, List[str]]:
        corrections, seen = [], 0
        def normalize(text):
            nonlocal seen
            def heading(match):
                nonlocal seen
                seen += 1
                return match[0] if seen == 1 else "#" + match[0]
            return re.sub(r"(?m)^# +\S[^\n]*", heading, text)
        content = map_prose(content, normalize)
        if not seen:
            content = f"# {title}\n\n" + content
            corrections.append("Added the document title")
        elif seen > 1:
            corrections.append("Demoted duplicate level-one headings")
        return content, corrections

    def _infer_fence_language(self, content: str) -> str:
        if re.search(
            r"(?m)^\s*(?:from\s+\S+\s+import|import\s+\S+|def\s+\w+|class\s+\w+)|"
            r"\b(?:np|pd|plt)\.\w+|^\s*\w+\s*=\s*",
            content,
        ):
            return "python"
        return "text"

    def _has_unlabeled_opening_fence(self, content: str) -> bool:
        return any(not OPEN_FENCE.match(block)[2].strip()
                   for fenced, block in markdown_blocks(content) if fenced)

    def _count_document_titles(self, content: str) -> int:
        return len(re.findall(r"(?m)^# +\S", prose_text(content)))

    def _is_noise_line(self, line: str) -> bool:
        stripped = line.strip()
        return bool(
            re.search(
                r"process finished with exit code|"
                r"^(?:\*\*)?(?:command|executable|environment|python version|line endings|encoding|reader mode)(?:\*\*)?\s*:|"
                r"(?:[A-Za-z]:\\|/)[^\s]*python(?:3|\.exe)?\s+.*\.py\b|"
                r"^python(?:3|\.exe)?\s+(?:[A-Za-z]:\\|/).+\.py\b",
                stripped,
                re.IGNORECASE,
            )
        )

    def _needs_source_reference(self, content: str) -> bool:
        return "```" in content or bool(
            re.search(r"\b(?:diagram|flowchart|示意图|流程图)\b", content, re.IGNORECASE)
        )
