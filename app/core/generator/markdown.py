"""
Markdown Generator - Saves notes as Markdown files.
"""

import re
from dataclasses import dataclass, field
from typing import List

from app.core.vision.vision_client import PageAnalysis


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
        """Remove model wrappers and presentation/IDE noise from one page."""
        content = self._strip_markdown_wrappers(content.strip())
        content = re.sub(
            r"(?im)^[ \t]*<a[ \t]+[^>]*id=['\"]?page-\d+['\"]?[^>]*>[ \t]*</a>[ \t]*$",
            "",
            content,
        )
        content = re.sub(
            r"(?im)^[ \t]*#{1,6}[ \t]+(?:page|slide|第)[ \t]*\d+(?:[ \t]*[页:]?[^\n]*)?$",
            "",
            content,
        )

        lines: List[str] = []
        in_fence = False
        fence_language = ""
        skip_metadata = False
        skip_page_toc = False
        for line in content.splitlines():
            fence_match = re.match(r"^\s*```([^\s`]*)", line)
            if fence_match:
                if not in_fence:
                    in_fence = True
                    fence_language = fence_match.group(1).lower()
                    skip_metadata = False
                else:
                    in_fence = False
                    fence_language = ""
                lines.append(line.rstrip())
                continue

            if not in_fence:
                if re.match(
                    r"^\s{0,3}#{1,6}\s+(?:Table of Contents|目录)\s*$",
                    line,
                    re.IGNORECASE,
                ):
                    skip_page_toc = True
                    continue
                if skip_page_toc:
                    if re.match(r"^\s*-\s+\[[^\]]+\]\(#page-\d+\)\s*$", line, re.IGNORECASE):
                        continue
                    if not line.strip():
                        continue
                    skip_page_toc = False
                if re.match(
                    r"^\s{0,3}#{1,6}\s+(?:IDE Context|Interface Elements|Status Bar|Footer)\b",
                    line,
                    re.IGNORECASE,
                ):
                    skip_metadata = True
                    continue
                if skip_metadata:
                    if re.match(r"^\s{0,3}#{1,6}\s+\S", line):
                        skip_metadata = False
                    elif line.strip() in {"---", "***"}:
                        skip_metadata = False
                        lines.append(line.rstrip())
                        continue
                    else:
                        continue
                if self._is_noise_line(line):
                    continue
                if re.match(r"^\s*(?:\*{0,2}(?:page|slide)\s*)?\d{1,3}\*{0,2}\s*$", line, re.IGNORECASE):
                    continue
            elif fence_language in {"python", "py", "python3", "ipython"}:
                if re.match(r"^\s*\d{1,4}\s*$", line):
                    continue
                line = re.sub(r"^(\s*)\d{1,4}\s{2}(?=\s*[A-Za-z_#@])", r"\1", line)
            elif fence_language in {"text", "console", "terminal", "shell", "bash"}:
                if self._is_noise_line(line):
                    continue

            lines.append(line.rstrip())

        cleaned = "\n".join(lines)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

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
        content = re.sub(r"\n(?:\s*---\s*\n){2,}", "\n\n---\n", content)
        content = re.sub(r"\n{3,}", "\n\n", content).strip() + "\n"

        issues: List[str] = []
        if re.search(r"<a\s+[^>]*id=", content, re.IGNORECASE):
            issues.append("HTML anchors remain")
        if content.count("```") % 2:
            issues.append("Unbalanced fenced code block")
        if self._has_unlabeled_opening_fence(content):
            issues.append("A fenced block has no language")
        if re.search(r"(?im)^#{1,6}\s+(?:page|slide|第)\s*\d+", content):
            issues.append("Page-number headings remain")
        if any(self._is_noise_line(line) for line in content.splitlines()):
            issues.append("Terminal or IDE noise remains")
        if self._count_document_titles(content) != 1:
            issues.append("Document must contain exactly one level-one title")

        return MarkdownAuditResult(
            content=content,
            corrections=list(dict.fromkeys(corrections)),
            issues=issues,
        )

    def _strip_outer_markdown_fence(self, content: str) -> str:
        lines = content.splitlines()
        if (
            len(lines) >= 2
            and re.match(r"^\s*```(?:markdown|md)\s*$", lines[0], re.IGNORECASE)
            and re.match(r"^\s*```\s*$", lines[-1])
        ):
            return "\n".join(lines[1:-1]).strip()
        return content

    def _strip_markdown_wrappers(self, content: str) -> str:
        """Remove one or more model-added outer Markdown fences."""
        lines = content.splitlines()
        if not any(
            re.match(r"^\s*```(?:markdown|md)\s*$", line, re.IGNORECASE)
            for line in lines
        ):
            return self._strip_outer_markdown_fence(content)

        remove = set()
        index = 0
        while index < len(lines):
            if not re.match(
                r"^\s*```(?:markdown|md)\s*$", lines[index], re.IGNORECASE
            ):
                index += 1
                continue
            remove.add(index)
            nested_fences = 0
            cursor = index + 1
            while cursor < len(lines):
                match = re.match(r"^\s*```([^\s`]*)\s*$", lines[cursor])
                if not match:
                    cursor += 1
                    continue
                language = match.group(1).lower()
                if language:
                    nested_fences += 1
                elif nested_fences:
                    nested_fences -= 1
                else:
                    remove.add(cursor)
                    break
                cursor += 1
            index = cursor + 1
        return "\n".join(
            line for index, line in enumerate(lines) if index not in remove
        ).strip()

    def _normalize_fenced_blocks(self, content: str) -> tuple[str, List[str]]:
        corrections: List[str] = []
        lines = content.splitlines()
        normalized: List[str] = []
        in_fence = False
        for index, line in enumerate(lines):
            match = re.match(r"^(\s*)```([^\s`]*)\s*$", line)
            if not match:
                normalized.append(line)
                continue
            if not in_fence:
                language = match.group(2).lower()
                if not language:
                    preview = "\n".join(lines[index + 1:index + 12])
                    language = self._infer_fence_language(preview)
                    corrections.append(f"Labeled an unlabeled block as {language}")
                elif language in {"console", "terminal", "shell"}:
                    language = "text"
                    corrections.append("Normalized terminal block language to text")
                normalized.append(f"```{language}")
                in_fence = True
            else:
                normalized.append("```")
                in_fence = False
        if in_fence:
            normalized.append("```")
            corrections.append("Closed an unterminated fenced block")
        return "\n".join(normalized), corrections

    def _normalize_headings(self, content: str, title: str) -> tuple[str, List[str]]:
        corrections: List[str] = []
        lines = content.splitlines()
        h1_indices: List[int] = []
        in_fence = False
        for index, line in enumerate(lines):
            if re.match(r"^\s*```", line):
                in_fence = not in_fence
                continue
            if not in_fence and re.match(r"^#\s+\S", line):
                h1_indices.append(index)
        if not h1_indices:
            lines = [f"# {title}", ""] + lines
            corrections.append("Added the document title")
        elif len(h1_indices) > 1:
            for index in h1_indices[1:]:
                lines[index] = "#" + lines[index]
            corrections.append("Demoted duplicate level-one headings")
        return "\n".join(lines), corrections

    def _infer_fence_language(self, content: str) -> str:
        if re.search(
            r"(?m)^\s*(?:from\s+\S+\s+import|import\s+\S+|def\s+\w+|class\s+\w+)|"
            r"\b(?:np|pd|plt)\.\w+|^\s*\w+\s*=\s*",
            content,
        ):
            return "python"
        return "text"

    def _has_unlabeled_opening_fence(self, content: str) -> bool:
        in_fence = False
        for line in content.splitlines():
            match = re.match(r"^\s*```([^\s`]*)\s*$", line)
            if not match:
                continue
            if not in_fence and not match.group(1):
                return True
            in_fence = not in_fence
        return False

    def _count_document_titles(self, content: str) -> int:
        count = 0
        in_fence = False
        for line in content.splitlines():
            if re.match(r"^\s*```", line):
                in_fence = not in_fence
                continue
            if not in_fence and re.match(r"^#\s+\S", line):
                count += 1
        return count

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
