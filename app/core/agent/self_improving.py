"""Chunk-safe generation with a small, free-tier-conscious refinement loop."""

import re
from dataclasses import dataclass
from typing import Callable, List, Optional

from app.core.agent.evaluator import NoteEvaluator
from app.core.agent.optimizer import NoteOptimizer
from app.core.vision.vision_client import ModelAPIError, VisionClient


@dataclass
class GenerationResult:
    content: str
    score: int
    iterations: int
    issues: List[str]


class SelfImprovingGenerator:
    """Generate notes without silently truncating long documents."""

    QUALITY_THRESHOLD = 82
    MAX_INPUT_CHARS = 70_000
    MAX_OPTIMIZATIONS = 1

    def __init__(
        self,
        vision_client: VisionClient,
        evaluator: Optional[NoteEvaluator] = None,
        optimizer: Optional[NoteOptimizer] = None,
    ):
        self.vision_client = vision_client
        self.evaluator = evaluator or NoteEvaluator()
        self.optimizer = optimizer or NoteOptimizer(vision_client)

    def generate(
        self,
        raw_content: str,
        language: str,
        progress_callback: Optional[Callable[[str], None]] = None,
        stream_callback: Optional[Callable[[str], None]] = None,
    ) -> GenerationResult:
        chunks = self._chunk_content(raw_content)
        outputs: List[str] = []
        scores: List[int] = []
        all_issues: List[str] = []
        maximum_iterations = 1

        for index, chunk in enumerate(chunks, start=1):
            label = f" ({index}/{len(chunks)})" if len(chunks) > 1 else ""
            if progress_callback:
                progress_callback(f"正在整理笔记{label}…" if language == "zh" else f"Structuring notes{label}…")

            current = self._initial_generate(chunk, language, stream_callback)
            score, issues = self.evaluator.evaluate(current, chunk, language)

            if score < self.QUALITY_THRESHOLD and issues and self.MAX_OPTIMIZATIONS:
                if progress_callback:
                    progress_callback(
                        f"正在修正 {len(issues)} 个质量问题{label}…"
                        if language == "zh"
                        else f"Fixing {len(issues)} quality issue(s){label}…"
                    )
                try:
                    optimized = self.optimizer.optimize(current, issues, chunk, language)
                    optimized_score, optimized_issues = self.evaluator.evaluate(
                        optimized, chunk, language
                    )
                    maximum_iterations = 2
                    if optimized_score >= score:
                        current, score, issues = optimized, optimized_score, optimized_issues
                except ModelAPIError:
                    # The first pass is still useful; a free-tier limit should not
                    # discard it merely because the optional refinement failed.
                    pass

            outputs.append(current.strip())
            scores.append(score)
            all_issues.extend(issues)

        content = "\n\n---\n\n".join(outputs).strip()
        average_score = round(sum(scores) / len(scores)) if scores else 0
        return GenerationResult(
            content=content,
            score=average_score,
            iterations=maximum_iterations,
            issues=list(dict.fromkeys(all_issues)),
        )

    def _initial_generate(
        self,
        raw_content: str,
        language: str,
        stream_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        if language == "zh":
            system = "你是专业的学习笔记编辑。忠实重组资料，不添加未经资料支持的事实。"
            prompt = f"""把下面逐页解析结果整理成连贯、可复习的中文 Markdown 笔记。

资料：
---
{raw_content}
---

要求：
1. 合并重复内容，但保留重要定义、步骤、公式、示例、代码和图表含义。
2. 使用清晰的标题层级与要点；不要写“第几页说了什么”式流水账。
3. 不得省略因视觉解析得到而原始文本中没有的关键信息。
4. 代码块必须带语言标注，公式使用 LaTeX。
5. 直接输出笔记，不要解释你的过程，也不要用代码围栏包住整份笔记。"""
        else:
            system = "You are a professional study-note editor. Restructure faithfully and add no unsupported facts."
            prompt = f"""Turn the page-by-page analysis below into cohesive, reviewable Markdown notes.

Material:
---
{raw_content}
---

Requirements:
1. Merge repetition while preserving definitions, procedures, formulas, examples, code, and diagram meaning.
2. Use a clear heading hierarchy and concise points, not a page-by-page narration.
3. Preserve important details recovered visually even when absent from extracted text.
4. Label fenced code blocks and use LaTeX for formulas.
5. Return only the notes; do not explain your process or wrap the whole response in a code fence."""

        return self.vision_client.complete_text(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            stream_callback=stream_callback,
        )

    def _chunk_content(self, content: str) -> List[str]:
        if len(content) <= self.MAX_INPUT_CHARS:
            return [content]

        sections = re.split(r"(?=\n?<!-- NOTEAGENT_PAGE:\d+ -->)", content)
        sections = [section.strip() for section in sections if section.strip()]
        if len(sections) <= 1:
            sections = [part.strip() for part in content.split("\n\n") if part.strip()]

        chunks: List[str] = []
        current: List[str] = []
        current_length = 0
        for section in sections:
            if len(section) > self.MAX_INPUT_CHARS:
                if current:
                    chunks.append("\n\n".join(current))
                    current, current_length = [], 0
                chunks.extend(
                    section[start:start + self.MAX_INPUT_CHARS]
                    for start in range(0, len(section), self.MAX_INPUT_CHARS)
                )
                continue
            if current and current_length + len(section) + 2 > self.MAX_INPUT_CHARS:
                chunks.append("\n\n".join(current))
                current, current_length = [], 0
            current.append(section)
            current_length += len(section) + 2
        if current:
            chunks.append("\n\n".join(current))
        return chunks or [content]
