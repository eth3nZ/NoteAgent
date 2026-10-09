"""
Note Optimizer - Uses LLM to fix issues found during evaluation.
"""

from typing import List
from app.core.vision.vision_client import VisionClient


class NoteOptimizer:
    """Optimize notes based on evaluation feedback."""

    def __init__(self, vision_client: VisionClient):
        """
        Args:
            vision_client: VisionClient instance for LLM calls
        """
        self.vision_client = vision_client

    def optimize(
        self,
        note_content: str,
        issues: List[str],
        raw_content: str,
        language: str,
    ) -> str:
        """
        Optimize note content based on identified issues.

        Args:
            note_content: Current note content
            issues: List of issues from evaluator
            raw_content: Original extracted text from document
            language: Output language ("zh" or "en")

        Returns:
            Optimized note content
        """
        # Build optimization prompt
        prompt = self._build_optimization_prompt(
            note_content, issues, raw_content, language
        )

        # Use the vision client's LLM capabilities (text-only mode)
        messages = [
            {"role": "system", "content": self._build_optimization_system_prompt(language)},
            {"role": "user", "content": prompt},
        ]

        return self.vision_client.complete_text(messages)

    def _build_optimization_system_prompt(self, language: str) -> str:
        """Build system prompt for optimization."""
        if language == "zh":
            return """你是一个专业的笔记优化助手。你的任务是针对给定的笔记内容进行针对性优化。

规则：
1. 只修正指出的问题，保持原有正确内容不变
2. 保持原有的Markdown格式
3. 不要添加新内容，除非是为了补充缺失的原始内容
4. 直接输出优化后的完整笔记，不要添加任何解释或备注"""
        else:
            return """You are a professional note optimization assistant. Your task is to make targeted improvements to the given notes.

Rules:
1. Only fix the identified issues; keep the correct content unchanged
2. Preserve the existing Markdown format
3. Do not add new content unless it's to supplement missing original content
4. Output the complete optimized notes directly, without any explanations"""

    def _build_optimization_prompt(
        self,
        note_content: str,
        issues: List[str],
        raw_content: str,
        language: str,
    ) -> str:
        """Build the optimization prompt."""
        if language == "zh":
            issues_text = "\n".join(f"- {issue}" for issue in issues)
            raw_preview = raw_content[:16000] if raw_content else "(无原始内容)"
            return f"""原始笔记内容如下：
---
{note_content}
---

质量评估发现以下问题，请针对性地修正：
{issues_text}

修正要求：
1. 只修正上述问题，保持原有正确内容不变
2. 如果需要补充内容，请基于以下原始资料：
{raw_preview}
3. 保持输出语言为中文

请直接输出修正后的完整笔记："""
        else:
            issues_text = "\n".join(f"- {issue}" for issue in issues)
            raw_preview = raw_content[:16000] if raw_content else "(No original content)"
            return f"""Original note content:
---
{note_content}
---

Quality evaluation found the following issues that need fixing:
{issues_text}

Requirements:
1. Only fix the issues listed above; keep correct content unchanged
2. If content needs to be supplemented, base it on the following original material:
{raw_preview}
3. Keep the output language as English

Output the complete revised notes directly:"""
