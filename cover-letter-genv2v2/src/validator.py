"""Validator module for cover letter generation.

Checks generated cover letters against a set of rules and produces
structured feedback for the repair pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional
import re


@dataclass
class Violation:
    rule: str
    severity: str  # "error" | "warn"
    evidence: str
    fix_hint: str


@dataclass
class ValidationResult:
    violations: List[Violation] = field(default_factory=list)
    word_count: int = 0

    @property
    def ok(self) -> bool:
        return not any(v.severity == "error" for v in self.violations)

    def format_feedback(self) -> str:
        """Human-readable feedback string for the repair pass.

        Priority order:
          1. word_count_too_high — with exact delta and hard CUT imperative.
          2. forbidden_claim     — with hard DELETE (no synonym) imperative.
          3. everything else     — standard format.

        Putting critical constraints first ensures the repair LLM acts on them
        before softer hints, and doesn't bury the word-count limit.
        """
        if not self.violations:
            return ""

        import re as _re

        word_count_viols = [v for v in self.violations if v.rule == "word_count_too_high"]
        forbidden_viols  = [v for v in self.violations if v.rule == "forbidden_claim"]
        other_viols      = [v for v in self.violations
                            if v.rule not in ("word_count_too_high", "forbidden_claim")]

        parts: List[str] = []

        # --- PRIORITY 1: word count ---
        for v in word_count_viols:
            m = _re.search(r"(\d+)\s*слов", v.evidence)
            current = int(m.group(1)) if m else self.word_count
            delta = max(current - 110, 1)
            parts.append(
                "🔴 КРИТИЧНО — СЛИШКОМ ДЛИННОЕ ПИСЬМО\n"
                f"   Текущий объём: {current} слов. Лимит: 110 слов. Нужно убрать: {delta} слов.\n"
                f"   ДЕЙСТВИЕ: СОКРАТИ письмо ровно на {delta} слов. "
                "Удали вводные обороты, повторы, дублирующиеся детали. "
                "НЕ добавляй новый текст. НЕ переписывай — только режь.\n"
                f"   -> {v.fix_hint}"
            )

        # --- PRIORITY 2: forbidden_claim ---
        for v in forbidden_viols:
            parts.append(
                "🔴 КРИТИЧНО — ЗАПРЕЩЁННОЕ УТВЕРЖДЕНИЕ\n"
                f"   Найдено: «{v.evidence}»\n"
                "   ДЕЙСТВИЕ: УДАЛИ это слово/фразу полностью. "
                "НЕ заменяй синонимом. НЕ перефразируй с тем же смыслом. Просто убери.\n"
                f"   -> {v.fix_hint}"
            )

        # --- PRIORITY 3: everything else ---
        for v in other_viols:
            parts.append(f"- [{v.severity}] {v.rule}: {v.evidence}\n    -> {v.fix_hint}")

        header = "Нарушения, которые надо исправить (в порядке приоритета):\n"
        return header + "\n\n".join(parts)
