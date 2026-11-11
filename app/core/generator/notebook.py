"""Jupyter Notebook output that preserves Markdown/code ordering."""

import ast
from typing import List

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

from app.core.vision.vision_client import PageAnalysis
from app.core.markdown_text import OPEN_FENCE, markdown_blocks


class NotebookGenerator:
    PYTHON_LANGUAGES = {"python", "py", "python3", "ipython"}

    def generate(
        self,
        page_analyses: List[PageAnalysis],
        title: str = "课程笔记",
        language: str = "zh",
    ) -> nbformat.NotebookNode:
        sections = []
        for analysis in page_analyses:
            label = f"第 {analysis.page_num} 页" if language == "zh" else f"Page {analysis.page_num}"
            sections.append(f"## {label}\n\n{analysis.content or ''}")
        return self.generate_from_markdown("\n\n---\n\n".join(sections), title)

    def generate_from_markdown(
        self, content: str, title: str = "Course Notes"
    ) -> nbformat.NotebookNode:
        notebook = new_notebook()
        notebook.metadata = {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3"},
            "noteagent": {"format_version": 1},
        }
        notebook.cells = [new_markdown_cell(source=f"# {title}")]
        notebook.cells.extend(self._markdown_to_cells(content))
        return notebook

    def _markdown_to_cells(self, content: str) -> List[nbformat.NotebookNode]:
        cells: List[nbformat.NotebookNode] = []
        for fenced, block in markdown_blocks(content):
            if not fenced:
                self._append_markdown(cells, block)
                continue
            lines = block.splitlines(keepends=True)
            opening = OPEN_FENCE.match(lines[0])
            closing = OPEN_FENCE.match(lines[-1]) if len(lines) > 1 else None
            complete = bool(closing and closing[1][0] == opening[1][0] and
                            len(closing[1]) >= len(opening[1]) and not closing[2].strip())
            language = opening[2].strip().lower()
            code = "".join(lines[1:-1]).removesuffix("\n") if complete else ""
            if complete and language in self.PYTHON_LANGUAGES and self._is_python(code):
                cells.append(new_code_cell(source=code))
            else:
                self._append_markdown(cells, block)

        return cells

    @staticmethod
    def _is_python(code: str) -> bool:
        # Screenshots can contain output or incomplete snippets mislabeled as Python.
        # Preserve those verbatim as Markdown rather than creating invalid code cells.
        try:
            ast.parse(code)
        except (SyntaxError, ValueError):
            return False
        return True

    def _append_markdown(self, cells: List[nbformat.NotebookNode], text: str) -> None:
        text = text.strip()
        if not text:
            return
        if cells and cells[-1].cell_type == "markdown":
            cells[-1].source = f"{cells[-1].source}\n\n{text}"
        else:
            cells.append(new_markdown_cell(source=text))

    def save(self, notebook: nbformat.NotebookNode, output_path: str) -> None:
        with open(output_path, "w", encoding="utf-8") as file:
            nbformat.write(notebook, file)
