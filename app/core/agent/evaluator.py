"""Fast local quality checks for generated notes."""

import re
from typing import List, Set, Tuple


class NoteEvaluator:
    """Evaluate note quality without spending another model request."""

    def evaluate(
        self, note_content: str, raw_content: str, language: str
    ) -> Tuple[int, List[str]]:
        if not note_content.strip():
            message = "生成内容为空" if language == "zh" else "The generated notes are empty"
            return 0, [message]

        score = 0
        issues: List[str] = []

        coverage = self._calculate_coverage(note_content, raw_content)
        if coverage >= 0.55:
            score += 30
        elif coverage >= 0.35:
            score += 20
            issues.append(self._message(language, "部分原始要点未被覆盖", "Some source points are missing"))
        else:
            score += 8
            issues.append(self._message(language, "原始内容覆盖不足", "Source coverage is too low"))

        headings = len(re.findall(r"^#{1,6}\s+\S", note_content, re.MULTILINE))
        list_items = len(re.findall(r"^\s*(?:[-*+] |\d+[.)] )", note_content, re.MULTILINE))
        if headings >= 2:
            score += 15
        elif headings == 1:
            score += 8
            issues.append(self._message(language, "需要更清晰的章节结构", "Use clearer section headings"))
        else:
            issues.append(self._message(language, "缺少 Markdown 标题", "Markdown headings are missing"))
        if list_items >= 2 or len(note_content) < 500:
            score += 5
        else:
            issues.append(self._message(language, "长段落应拆分为要点", "Break long prose into key points"))

        code_blocks = self._extract_code_blocks(note_content)
        source_looks_like_code = "```" in raw_content or bool(
            re.search(r"\b(?:def|class|function|import|SELECT|const|let)\b", raw_content)
        )
        if not code_blocks and not source_looks_like_code:
            score += 15
        elif code_blocks and all(block[0] for block in code_blocks):
            score += 15
        elif code_blocks:
            score += 7
            issues.append(self._message(language, "代码块缺少语言标注", "Code fences need language labels"))
        else:
            issues.append(self._message(language, "源材料中的代码可能遗漏", "Code from the source may be missing"))

        if self._language_consistent(note_content, language):
            score += 15
        else:
            score += 5
            issues.append(self._message(language, "输出语言不一致", "The output language is inconsistent"))

        if self._has_balanced_fences(note_content):
            score += 10
        else:
            issues.append(self._message(language, "Markdown 代码围栏未闭合", "A Markdown code fence is unclosed"))

        paragraphs = [part.strip() for part in note_content.split("\n\n") if part.strip()]
        if len(paragraphs) >= 2 or len(note_content) < 300:
            score += 10
        else:
            score += 4
            issues.append(self._message(language, "内容需要分段", "The content needs paragraph breaks"))

        return min(100, score), issues

    def _calculate_coverage(self, note_content: str, raw_content: str) -> float:
        if not raw_content.strip():
            return 1.0
        terms = self._key_terms(raw_content)
        if not terms:
            return 1.0
        normalized_note = re.sub(r"\s+", "", note_content.lower())
        covered = sum(1 for term in terms if term in normalized_note)
        return covered / len(terms)

    def _key_terms(self, content: str) -> Set[str]:
        normalized = content.lower()
        normalized = re.sub(r"<!--.*?-->", " ", normalized, flags=re.DOTALL)
        normalized = re.sub(r"^#{1,6}\s+page\s+\d+\s*$", " ", normalized, flags=re.MULTILINE)
        for boilerplate in ("extracted text", "visual analysis", "提取文本", "视觉解析"):
            normalized = normalized.replace(boilerplate, " ")
        latin = {
            token
            for token in re.findall(r"[a-z0-9_][a-z0-9_.+-]{2,}", normalized)
            if token
            not in {
                "page",
                "slide",
                "notes",
                "none",
                "the",
                "and",
                "with",
                "from",
                "this",
                "that",
            }
        }
        chinese_terms: Set[str] = set()
        for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
            if len(sequence) <= 4:
                chinese_terms.add(sequence)
            else:
                chinese_terms.update(sequence[index:index + 2] for index in range(len(sequence) - 1))
        # Extremely large decks do not need every repeated token for a useful estimate.
        return set(sorted(latin | chinese_terms)[:2000])

    def _extract_code_blocks(self, content: str) -> List[Tuple[str, str]]:
        return [
            (match.group(1).strip(), match.group(2))
            for match in re.finditer(r"```([^\n`]*)\n(.*?)```", content, re.DOTALL)
        ]

    def _language_consistent(self, content: str, language: str) -> bool:
        letters = len(re.findall(r"[A-Za-z]", content))
        chinese = len(re.findall(r"[\u4e00-\u9fff]", content))
        if language == "zh":
            return chinese > 0 and chinese >= letters * 0.15
        return letters > 0

    def _has_balanced_fences(self, content: str) -> bool:
        return content.count("```") % 2 == 0

    def _message(self, language: str, zh: str, en: str) -> str:
        return zh if language == "zh" else en
