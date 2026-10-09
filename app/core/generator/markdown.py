"""
Markdown Generator - Saves notes as Markdown files.
"""

from typing import List

from app.core.vision.vision_client import PageAnalysis


class MarkdownGenerator:
    """Generate Markdown notes from page analyses."""

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
        lines = []

        # Title
        lines.append(f"# {title}")
        lines.append("")

        # Table of contents
        if language == "zh":
            lines.append("## 目录")
        else:
            lines.append("## Table of Contents")
        lines.append("")

        for analysis in page_analyses:
            page_label = f"第 {analysis.page_num} 页" if language == "zh" else f"Page {analysis.page_num}"
            lines.append(f"- [{page_label}](#page-{analysis.page_num})")
        lines.append("")

        # Content for each page
        for analysis in page_analyses:
            page_label = f"第 {analysis.page_num} 页" if language == "zh" else f"Page {analysis.page_num}"
            lines.append(f"## {page_label}")
            lines.append(f"<a id='page-{analysis.page_num}'></a>")
            lines.append("")

            # Add the analyzed content
            if analysis.content:
                lines.append(analysis.content)
            else:
                lines.append(
                    "*（无内容）*" if language == "zh" else "*(No content)*"
                )
            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def save(self, content: str, output_path: str):
        """Save Markdown content to file."""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

    def prepare_document(self, content: str, title: str) -> str:
        """Normalize a model response into a standalone Markdown document."""
        content = content.strip()
        if content.startswith("```markdown") and content.endswith("```"):
            content = content[len("```markdown"): -3].strip()
        if not content.startswith("# "):
            content = f"# {title}\n\n{content}"
        return content.rstrip() + "\n"
