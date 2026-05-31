"""Deterministic + semantic validation for generated cover letters.

Deterministic rules (regex/string-level, no LLM):
- word count in [min_words, max_words]
- every number token in the letter must be in `allowed_numbers`
- forbidden claims (grounded against the resume) must not appear (substring)
- forbidden regex patterns (grounded) must not match (regex)
- no years-of-experience framing in the opener (first 2 sentences)
- tech tokens outside the global allowlist are flagged

Semantic rules (LLM, T=0, JSON): handled by `prompts/validator.py`.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .facts import CanonicalFacts
from .llm_client import LLMClient
from .prompts.validator import VALIDATOR_SYSTEM, build_validator_user


logger = logging.getLogger(__name__)


# Tech tokens that are always allowed in cover letters (case-insensitive match).
# These are common, generic terms — specific stack items come from CanonicalFacts.allowed_tech.
BASE_ALLOWED_TECH: Set[str] = {
    "Flutter", "Dart", "BLoC", "Cubit", "gRPC", "JWT", "REST", "API",
    "Web", "iOS", "Android", "Firebase", "FCM", "Clean", "Architecture",
    "GraphQL", "SQL", "SDK", "OTP", "B2B", "ERP", "CI/CD", "Git",
    "WebView", "SQLite", "URL", "HTTP", "HTTPS", "UI", "UX",
    "production", "backend", "frontend", "mobile", "open", "source",
    "legacy", "deploy", "release", "build", "pipeline", "DI", "ORM",
    "auth", "profile", "unit", "retry", "flow", "crop", "avatar",
    "interceptor", "middleware", "token", "refresh", "widget", "state",
    "real", "time", "real-time", "DTO",
    "UGC", "proto", "sealed", "wrapper", "wizard", "admin",
    "moderator", "dashboard",
}


@dataclass
class Violation:
    """A single validation failure."""

    rule: str
    severity: str  # "hard" | "soft"
    evidence: str
    fix_hint: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "evidence": self.evidence,
            "fix_hint": self.fix_hint,
        }


@dataclass
class ValidationResult:
    """Result of running validation on a letter.

    Fields are intentionally mutable: pipeline.py copies `used_numbers`/`used_tech`
    from the deterministic result onto the semantic result before returning.
    """

    passed: bool
    violations: List[Violation] = field(default_factory=list)
    word_count: int = 0
    used_numbers: List[str] = field(default_factory=list)
    used_tech: List[str] = field(default_factory=list)

    def format_feedback(self) -> str:
        """Human-readable feedback string for the repair pass."""
        if not self.violations:
            return ""
        lines: List[str] = []
        for v in self.violations:
            lines.append(
                f"- [{v.severity}] {v.rule}: {v.evidence}\n  -> {v.fix_hint}"
            )
        return "Нарушения, которые надо исправить:\n" + "\n".join(lines)


# --- Deterministic validation -------------------------------------------------

# Matches number tokens like "3", "11 000", "11 381", "32 840", "3.0.2",
# "3+", "10+", "1,3", "2 682". The {0,12} allows multi-thousand numbers with
# internal whitespace to match as ONE token (previously {0,6} broke "11 381"
# into ("11", "381") and only the first half was checked against the whitelist).
_NUMBER_RE = re.compile(r"(?<!\w)(\d[\d\s.,]{0,12}\d|\d)\+?(?!\w)")

# Matches years-of-experience phrases anywhere in a string:
#   "3+ года", "5 лет", "три года", "три+ года", "3 г.", "3+ г"
_YEARS_RE = re.compile(
    r"(?i)\b(\d+\+?|один|два|три|четыре|пять|шесть|семь|восемь|девять|десять)"
    r"\s*(?:\+\s*)?"
    r"(год(?:а|ов)?|лет|г\.?)\b"
)


def _split_sentences(text: str) -> List[str]:
    """Naive sentence split on . ! ? while keeping empty parts out."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _extract_numbers(text: str) -> List[str]:
    """Extract every number token from the letter, normalized (no inner spaces)."""
    out: List[str] = []
    for raw in _NUMBER_RE.findall(text):
        normalized = re.sub(r"\s+", "", raw)
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def _normalize_number(token: str) -> str:
    """Strip trailing '+' and inner whitespace so '3+' and '3' compare equal to whitelist '3'."""
    return re.sub(r"\s+", "", token).rstrip("+")


def _extract_tech(text: str, allowed: Set[str]) -> List[str]:
    """Return tech tokens from the allowed set that actually appear in the letter."""
    lower = text.lower()
    found: List[str] = []
    for tech in allowed:
        if not tech:
            continue
        if tech.lower() in lower and tech not in found:
            found.append(tech)
    return found


def validate_deterministic(
    letter: str,
    *,
    facts: CanonicalFacts,
    allowed_numbers: List[str],
    selected_achievements: Optional[List[str]] = None,
    min_words: int = 70,
    max_words: int = 110,
    universal_mode: bool = False,
) -> ValidationResult:
    """Run all deterministic checks. Returns a ValidationResult."""

    violations: List[Violation] = []
    text = letter.strip()
    words = text.split()
    word_count = len(words)

    # 1. Word count
    if word_count < min_words:
        violations.append(Violation(
            rule="word_count_too_low",
            severity="hard",
            evidence=f"{word_count} слов (минимум {min_words})",
            fix_hint=f"Расширь содержание до {min_words}-{max_words} слов, добавив 1-2 конкретных факта о проекте.",
        ))
    elif word_count > max_words:
        violations.append(Violation(
            rule="word_count_too_high",
            severity="hard",
            evidence=f"{word_count} слов (максимум {max_words})",
            fix_hint=f"Сократи до {min_words}-{max_words} слов, убрав общие фразы.",
        ))

    # 2. Numbers — every number in the letter must be in the whitelist.
    allowed_set = {_normalize_number(n) for n in allowed_numbers if n}
    # Also allow numbers that appear in the candidate's resume globally.
    allowed_set.update(_normalize_number(n) for n in facts.allowed_numbers if n)

    used_numbers_raw = _extract_numbers(text)
    used_numbers: List[str] = []
    for token in used_numbers_raw:
        norm = _normalize_number(token)
        if norm and norm not in used_numbers:
            used_numbers.append(norm)
        if norm and norm not in allowed_set:
            violations.append(Violation(
                rule="invented_number",
                severity="hard",
                evidence=f"число '{token}' отсутствует в whitelist",
                fix_hint=f"Удали '{token}' или замени на число из allowed_numbers: {sorted(allowed_set)[:8]}",
            ))

    # 3. Forbidden claims — substring (grounded against the resume).
    grounded_forbidden = facts.forbidden_claims_grounded()
    letter_lower = text.lower()
    for phrase in grounded_forbidden:
        if phrase.lower() in letter_lower:
            violations.append(Violation(
                rule="forbidden_claim",
                severity="hard",
                evidence=f"запрещённая фраза '{phrase}' (нет в резюме)",
                fix_hint=f"Убери '{phrase}' — этого нет в опыте кандидата.",
            ))

    # 3a. Forbidden patterns — regex (grounded against the resume).
    # Catches per-digit hallucinations (X% efficiency, X млн пользователей,
    # X-минутный созвон, после 17:00, etc.) that vary by digit and so can't
    # be enumerated as static substrings.
    grounded_regexes = []
    if hasattr(facts, "forbidden_regexes_grounded"):
        grounded_regexes = facts.forbidden_regexes_grounded()
    for pattern in grounded_regexes:
        match = pattern.search(text)
        if match:
            matched_text = match.group(0)
            violations.append(Violation(
                rule="forbidden_pattern",
                severity="hard",
                evidence=f"запрещённый шаблон '{matched_text}' (нет в резюме)",
                fix_hint=(
                    f"Убери '{matched_text}' — это выдуманный факт "
                    f"(процент эффективности / масштаб аудитории / конкретное время), "
                    f"которого нет в резюме."
                ),
            ))

    # 4. No years-of-experience framing in the opener (first 2 sentences).
    sentences = _split_sentences(text)
    opener = " ".join(sentences[:2]) if sentences else ""
    if _YEARS_RE.search(opener):
        match = _YEARS_RE.search(opener)
        violations.append(Violation(
            rule="no_years_in_opener",
            severity="hard",
            evidence=f"годы опыта в опенере: '{match.group(0) if match else ''}'",
            fix_hint="Начни с конкретного достижения/факта о проекте, без 'X лет опыта'.",
        ))

    # 5. Tech tokens — informational only (we record what's used, no hard fail in universal mode).
    combined_allowed: Set[str] = set(BASE_ALLOWED_TECH) | set(facts.allowed_tech)
    used_tech = _extract_tech(text, combined_allowed)

    passed = not any(v.severity == "hard" for v in violations)

    return ValidationResult(
        passed=passed,
        violations=violations,
        word_count=word_count,
        used_numbers=used_numbers,
        used_tech=used_tech,
    )


# --- Semantic validation ------------------------------------------------------


def _coerce_violation(raw: Any) -> Optional[Violation]:
    """Build a Violation from a raw dict returned by the LLM."""
    if not isinstance(raw, dict):
        return None
    rule = str(raw.get("rule") or "").strip()
    if not rule:
        return None
    return Violation(
        rule=rule,
        severity=str(raw.get("severity") or "soft"),
        evidence=str(raw.get("evidence") or ""),
        fix_hint=str(raw.get("fix_hint") or ""),
    )


async def validate_semantic(
    llm: LLMClient,
    letter: str,
    analyzer_json: Dict[str, Any],
    allowed_numbers: List[str],
) -> ValidationResult:
    """Run the LLM-based semantic validator. Returns a ValidationResult.

    On any error (LLM failure, malformed JSON), returns passed=True with an
    empty violations list — the deterministic validator already caught the
    hard rules, and we don't want a flaky semantic pass to block the letter.
    """

    user_prompt = build_validator_user(letter, analyzer_json, list(allowed_numbers))

    try:
        raw = await llm.complete_json(
            system=VALIDATOR_SYSTEM,
            user=user_prompt,
            temperature=0.0,
            max_tokens=600,
        )
    except Exception as exc:
        logger.warning("Semantic validator LLM call failed: %s", exc)
        return ValidationResult(passed=True, violations=[], word_count=len(letter.split()))

    # `raw` may be a dict or a JSON string depending on the client.
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("Semantic validator returned non-JSON: %s", exc)
            return ValidationResult(passed=True, violations=[], word_count=len(letter.split()))

    if not isinstance(raw, dict):
        logger.warning("Semantic validator returned unexpected type: %s", type(raw).__name__)
        return ValidationResult(passed=True, violations=[], word_count=len(letter.split()))

    passed_flag = bool(raw.get("passed", False))
    raw_violations = raw.get("violations") or []
    violations: List[Violation] = []
    for item in raw_violations:
        v = _coerce_violation(item)
        if v is not None:
            violations.append(v)

    # If the model said passed=true but emitted violations, trust the violations list.
    if violations:
        passed_flag = False

    return ValidationResult(
        passed=passed_flag,
        violations=violations,
        word_count=len(letter.split()),
    )


__all__ = [
    "BASE_ALLOWED_TECH",
    "Violation",
    "ValidationResult",
    "validate_deterministic",
    "validate_semantic",
]
