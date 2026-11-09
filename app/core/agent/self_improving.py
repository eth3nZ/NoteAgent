"""Chunk-safe generation with a small, free-tier-conscious refinement loop."""

import re
from dataclasses import dataclass
from typing import Callable, List, Optional

from app.core.agent.evaluator import NoteEvaluator
from app.core.agent.optimizer import NoteOptimizer
from app.core.prompt_loader import render_prompt
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
    # Groq's free multimodal tier can reject a single request above roughly
    # 7K input tokens. Keep room for prompt instructions and provider variance.
    MAX_INPUT_TOKENS = 4_500
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
        output_language = "Chinese" if language == "zh" else "English"
        system = render_prompt("generation_system", language=output_language)
        prompt = render_prompt(
            "generation_user",
            language=output_language,
            raw_content=raw_content,
        )

        return self.vision_client.complete_text(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            stream_callback=stream_callback,
        )

    def _chunk_content(self, content: str) -> List[str]:
        if self._estimate_tokens(content) <= self.MAX_INPUT_TOKENS:
            return [content]

        sections = re.split(r"(?=\n?<!-- NOTEAGENT_PAGE:\d+ -->)", content)
        sections = [section.strip() for section in sections if section.strip()]
        if len(sections) <= 1:
            sections = [part.strip() for part in content.split("\n\n") if part.strip()]

        chunks: List[str] = []
        current: List[str] = []
        current_tokens = 0
        for section in sections:
            section_tokens = self._estimate_tokens(section)
            if section_tokens > self.MAX_INPUT_TOKENS:
                if current:
                    chunks.append("\n\n".join(current))
                    current, current_tokens = [], 0
                chunks.extend(self._split_oversized_section(section))
                continue
            if current and current_tokens + section_tokens > self.MAX_INPUT_TOKENS:
                chunks.append("\n\n".join(current))
                current, current_tokens = [], 0
            current.append(section)
            current_tokens += section_tokens
        if current:
            chunks.append("\n\n".join(current))
        return chunks or [content]

    def _split_oversized_section(self, section: str) -> List[str]:
        """Split a very large page without dropping any source material."""
        parts = [part.strip() for part in section.split("\n\n") if part.strip()]
        if len(parts) <= 1:
            max_chars = max(1, self.MAX_INPUT_TOKENS * 2)
            return [
                section[start:start + max_chars]
                for start in range(0, len(section), max_chars)
            ]

        chunks: List[str] = []
        current: List[str] = []
        current_tokens = 0
        for part in parts:
            part_tokens = self._estimate_tokens(part)
            if part_tokens > self.MAX_INPUT_TOKENS:
                if current:
                    chunks.append("\n\n".join(current))
                    current, current_tokens = [], 0
                max_chars = max(1, self.MAX_INPUT_TOKENS * 2)
                chunks.extend(
                    part[start:start + max_chars]
                    for start in range(0, len(part), max_chars)
                )
                continue
            if current and current_tokens + part_tokens > self.MAX_INPUT_TOKENS:
                chunks.append("\n\n".join(current))
                current, current_tokens = [], 0
            current.append(part)
            current_tokens += part_tokens
        if current:
            chunks.append("\n\n".join(current))
        return chunks

    def _estimate_tokens(self, content: str) -> int:
        """Conservative dependency-free estimate for English, code, and CJK."""
        cjk_count = len(re.findall(r"[\u3400-\u9fff]", content))
        other_count = max(0, len(content) - cjk_count)
        return cjk_count + (other_count + 2) // 3
