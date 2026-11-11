"""
Note Optimizer - Uses LLM to fix issues found during evaluation.
"""

from typing import List

from app.core.prompt_loader import render_prompt
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
        return render_prompt(
            "optimization_system",
            language="Chinese" if language == "zh" else "English",
        )

    def _build_optimization_prompt(
        self,
        note_content: str,
        issues: List[str],
        raw_content: str,
        language: str,
    ) -> str:
        """Build the optimization prompt."""
        return render_prompt(
            "optimization_user",
            note_content=note_content,
            issues="\n".join(f"- {issue}" for issue in issues),
            raw_content=raw_content[:16000] if raw_content else "(No source content)",
            language="Chinese" if language == "zh" else "English",
        )
