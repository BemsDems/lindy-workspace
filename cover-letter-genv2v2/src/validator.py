"""Pass 3: validator.

Two layers:

1. **Deterministic checks** (this module, pure Python): length, paragraph
   count, forbidden phrases, anglicism patterns, library names, numeric
   whitelist, *forbidden_claim* (grounded against the resume),
   *unknown_tech_term* (any PascalCase tech token must come from the
   resume's tech stack).

2. **Semantic checks** (LLM, optional): hook-not-addressed, advice-to-
   company, weak ending, invented domain. Runs only if deterministic
   checks pass.

In v2 the validator takes a full `CanonicalFacts` instead of just an
allowed_numbers list — this gives it access to forbidden_claims and the
tech whitelist.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .facts import CanonicalFacts
from .llm_client import LLMClient
from .prompts.validator import VALIDATOR_SYSTEM, build_validator_user


logger = logging.getLogger(__name__)


FORBIDDEN_PHRASES: List[str] = [
    "Готов применить",
    "Готов применять",
    "Готов включиться",
    "Готов приступить",
    "Буду рад обсудить",
    "Буду рад",
    "Хотел бы обсудить",
    "напрямую соответствует",
    "привычная задача",
    "благодаря",
    "в рамках",
    "легли в основу",
    "Вам нужен",
    "Вам нужна",
    "Вам требуется",
    "Вакансия предполагает",
    "Вакансия требует",
]


FORBIDDEN_LIBRARIES: List[str] = [
    "GetIt",
    "get_it",
    "Injectable",
    "Riverpod",
    "Provider",
    "MobX",
    "Dio",
    "Retrofit",
]


# Always-allowed English tokens (project-agnostic). Project-specific tech
# comes from `CanonicalFacts.allowed_tech` and is unioned with this at
# check time.
BASE_ALLOWED_TECH: set[str] = {
    "Flutter", "Dart", "BLoC", "Cubit", "gRPC", "JWT", "REST", "API",
    "Web", "iOS", "Android", "Firebase", "FCM", "Clean", "Architecture",
    "GraphQL", "SQL", "SDK", "OTP", "B2B", "ERP", "CI/CD", "Git",
    "WebView", "SQLite", "URL", "HTTP", "HTTPS", "UI", "UX",
    "production", "backend", "frontend", "mobile", "open", "source",
    "legacy", "deploy", "release", "build", "pipeline", "DI", "ORM",
    "auth", "profile", "unit", "retry", "flow", "crop", "avatar",
    "interceptor", "middleware", "token", "refresh", "widget", "state",
    "real", "time", "real-time", "OTP", "DTO",
}


@dataclass
class Violation:
    rule: str
    evidence: str
    fix_hint: str = ""
    severity: str = "hard"  # Добавлено (по умолчанию "hard")

    def to_dict(self) -> Dict[str, str]:
        return {
            "rule": self.rule,
            "evidence": self.evidence,
            "fix_hint": self.fix_hint,
            "severity": self.severity,
        }


@dataclass
class ValidationResult:
    passed: bool
    violations: List[Violation] = field(default_factory=list)
    word_count: int = 0
    used_numbers: List[str] = field(default_factory=list)
    used_tech: List[str] = field(default_factory=list)

    def format_feedback(self) -> str:
        if not self.violations:
            return ""
        lines = ["Нарушения:"]
        for v in self.violations:
            line = f"- [{v.rule}] {v.evidence}"
            if v.fix_hint:
                line += f" — {v.fix_hint}"
            lines.append(line)
        return "\n".join(lines)


def validate_deterministic(
    letter: str,
    *,
    facts: CanonicalFacts,
    allowed_numbers: Optional[Sequence[str]] = None,
    selected_achievements: Optional[Sequence[str]] = None,
    min_words: int = 100,
    max_words: int = 130,
    universal_mode: bool = False,
) -> ValidationResult:
    """Run cheap, regex-level checks. Returns all violations found.

    Args:
        letter: the generated text.
        facts: canonical facts for fact-level grounding (forbidden_claim,
            unknown_tech_term, allowed_company_names).
        allowed_numbers: subset of `facts.allowed_numbers` actually selected
            for this letter (the Analyzer's `selected_numbers`). Numbers in
            the letter that aren't in this subset are flagged. If omitted,
            falls back to `facts.allowed_numbers`.
        min_words / max_words: word-count band.
        universal_mode: relaxed format — one paragraph, shorter range
            (default 90-115 if standard band is left unchanged).
    """
    violations: List[Violation] = []
    text = letter.strip()
    allowed_numbers_list = list(allowed_numbers if allowed_numbers is not None else facts.allowed_numbers)
    achievement_numbers: set[str] = set()

    for achievement in selected_achievements or []:
        for number in _extract_numbers(achievement):
            if number in facts.allowed_numbers:
                achievement_numbers.add(number)

    # Universal-mode shortens the default word range, but caller can override.
    if universal_mode and (min_words, max_words) == (50, 100):
        min_words, max_words = 40, 85

    # 1. Length (допуск ±10%).
    words = _word_count(text)
    tol = max(8, round(max_words * 0.10))  # Допуск ±10% (минимум 8 слов)
    if words < min_words - tol:
        violations.append(Violation(
            rule="too_short",
            evidence=f"{words} слов (нужно {min_words}-{max_words} ±{tol})",
            fix_hint="Добавь ещё один факт из selected_achievements.",
        ))
    elif words > max_words + tol:
        violations.append(Violation(
            rule="too_long",
            evidence=f"{words} слов (нужно {min_words}-{max_words} ±{tol})",
            fix_hint="Сократи общие фразы, оставь только конкретику.",
        ))

    # 2. Paragraph count.
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if universal_mode:
        if len(paragraphs) > 2:
            violations.append(Violation(
                rule="wrong_paragraph_count",
                evidence=f"{len(paragraphs)} абзаца(ов) (нужно 1 или 2)",
                fix_hint="Объедини текст в 1 или 2 абзаца (universal mode).",
            ))
    else:
        # Разрешаем от 2 до 3 абзацев (гибко, как в идеальном примере)
        if not (2 <= len(paragraphs) <= 3):
            violations.append(Violation(
                rule="wrong_paragraph_count",
                evidence=f"{len(paragraphs)} абзаца(ов) (нужно от 2 до 3)",
                fix_hint="Раздели текст на 2 или 3 абзаца пустой строкой.",
            ))

    # 3. Forbidden phrases.
    lower = text.lower()
    for phrase in FORBIDDEN_PHRASES:
        if phrase.lower() in lower:
            violations.append(Violation(
                rule="forbidden_phrase",
                evidence=phrase.strip(),
                fix_hint=f"Удали или перефразируй фразу «{phrase.strip()}».",
            ))

    # 4. Forbidden library names.
    for lib in FORBIDDEN_LIBRARIES:
        if re.search(rf"\b{re.escape(lib)}\b", text):
            violations.append(Violation(
                rule="library_name",
                evidence=lib,
                fix_hint=f"Замени «{lib}» обобщённым термином (DI / HTTP-клиент / state management).",
            ))

    # 5. Years-of-experience in the first two sentences.
    # HR-style letters may start with an addressable vacancy hook,
    # then mention experience in the next sentence.
    if not universal_mode:
        opener_sentences = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=2)
        opener_window = " ".join(opener_sentences[:2]).strip()

        if opener_window and not _opener_has_years(opener_window):
            violations.append(Violation(
                rule="no_years_in_opener",
                evidence=opener_window[:120],
                fix_hint="В первые два предложения добавь число лет опыта, например «3+ года».",
            ))

    # 6. Numeric whitelist (subset selected by Analyzer).
    allowed_set = {n.strip() for n in allowed_numbers_list if n} | achievement_numbers
    found_numbers = _extract_numbers(text)
    used_numbers: List[str] = []
    for n in found_numbers:
        if n not in allowed_set:
            violations.append(Violation(
                rule="invented_number",
                evidence=n,
                fix_hint=f"Число «{n}» нет в selected_numbers — удали или замени.",
            ))
        elif n not in used_numbers:
            used_numbers.append(n)

    # 7. Minimum number of numeric facts.
    min_numbers = 1
    if len(used_numbers) < min_numbers:
        violations.append(Violation(
            rule="too_few_numbers",
            evidence=f"использовано {len(used_numbers)} чисел (нужно минимум {min_numbers})",
            fix_hint=f"Добавь ещё одну метрику из selected_numbers.",
        ))

    # 8. Anglicism heuristic (lowercase Latin tokens not in BASE_ALLOWED_TECH
    # ∪ project-level allowed_tech).
    angl_allowed_lower = {t.lower() for t in BASE_ALLOWED_TECH} | {t.lower() for t in facts.allowed_tech}
    for word in _find_anglicisms(text, allowed_lower=angl_allowed_lower):
        violations.append(Violation(
            rule="anglicism",
            evidence=word,
            fix_hint=f"Замени «{word}» русским эквивалентом (необязательно).",
            severity="soft",
        ))

    # 9. Unknown tech tokens.
    ach_tokens = set()
    if selected_achievements:
        for a in selected_achievements:
            ach_tokens.update(_find_tech_identifiers(a))
    
    tech_allowed = (
        BASE_ALLOWED_TECH
        | facts.allowed_tech
        | facts.allowed_project_names
        | facts.allowed_company_names
        | ach_tokens
    )
    tech_allowed_tokens = set()
    for entry in tech_allowed:
        for part in re.split(r"[\s/._-]+", entry):
            if part:
                tech_allowed_tokens.add(part.lower())
    
    used_tech: List[str] = []
    for token in _find_tech_identifiers(text):
        if token in tech_allowed:
            if token not in used_tech:
                used_tech.append(token)
            continue
        if any(token.lower() == a.lower() for a in tech_allowed):
            if token not in used_tech:
                used_tech.append(token)
            continue
        if token.lower() in tech_allowed_tokens:
            if token not in used_tech:
                used_tech.append(token)
            continue
        violations.append(Violation(
            rule="unknown_tech_term",
            evidence=token,
            fix_hint=f"«{token}» нет в allowed_tech/allowed_project_names — удали или замени на термин из списка.",
        ))

    # 10. Grounded forbidden claims (smell-phrases NOT present in the resume).
    grounded_forbidden = facts.forbidden_claims_grounded()
    for claim in grounded_forbidden:
        if claim.lower() in lower:
            violations.append(Violation(
                rule="forbidden_claim",
                evidence=claim,
                fix_hint=f"«{claim}» отсутствует в резюме — удали или замени на факт из selected_achievements.",
            ))

    hard_violations = [v for v in violations if v.severity != "soft"]

    return ValidationResult(
        passed=not hard_violations,
        violations=violations,
        word_count=words,
        used_numbers=used_numbers,
        used_tech=used_tech,
    )


async def validate_semantic(
    llm: LLMClient,
    letter: str,
    analyzer_json: Dict[str, Any],
    allowed_numbers: List[str],
) -> ValidationResult:
    """Run the LLM-based semantic validator. Returns its parsed result.

    On parse failure, returns a passing result with a logged warning — the
    deterministic layer is the source of truth.
    """
    user_prompt = build_validator_user(letter, analyzer_json, allowed_numbers)
    raw = await llm.generate(
        system_prompt=VALIDATOR_SYSTEM,
        user_prompt=user_prompt,
        temperature=0.0,
        max_tokens=600,
        json_mode=True,
    )
    parsed = _try_parse_json(raw)
    if parsed is None:
        logger.warning("Semantic validator returned unparseable JSON (treating as passed): %r", raw[:200])
        return ValidationResult(passed=True, violations=[], word_count=_word_count(letter))

    violations_raw = parsed.get("violations") or []
    violations: List[Violation] = []
    for v in violations_raw:
        if not isinstance(v, dict):
            continue
        violations.append(Violation(
            rule=str(v.get("rule", "semantic")),
            evidence=str(v.get("evidence", "")),
            fix_hint=str(v.get("fix_hint", "")),
        ))
    passed = bool(parsed.get("passed", not violations))
    return ValidationResult(
        passed=passed and not violations,
        violations=violations,
        word_count=_word_count(letter),
    )


_WORD_RE = re.compile(r"[\w’'-]+", re.UNICODE)

# A digit token at word boundaries, optionally followed by '+'.
# Matches "3", "3+", "11000" but NOT "2" inside "B2B".
# A digit token at word boundaries, optionally followed by '+'.
# Matches "3", "3+", "11000" but NOT "2" inside "B2B".
_OPENER_YEARS_RE = re.compile(r"(?:(?<=\s)|^)\d+\+?(?=\s|\b)")
_OPENER_YEARS_WORD_RE = re.compile(
    r"(?:три\s+года|тр[её]х\s+лет|более\s+тр[её]х\s+лет|за\s+три\s+года)",
    re.IGNORECASE,
)


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _first_sentence(text: str) -> str:
    match = re.search(r"^[^\.\!\?\n]+", text)
    return match.group(0) if match else ""


def _opener_has_years(first_sentence: str) -> bool:
    return bool(
        _OPENER_YEARS_RE.search(first_sentence)
        or _OPENER_YEARS_WORD_RE.search(first_sentence)
    )


_NUMBER_TOKEN_RE = re.compile(r"(?<!\w)(\d[\d\s.]{0,6}\d|\d+)([.,]\d+)?\+?(?!\w)")


def _extract_numbers(text: str) -> List[str]:
    out: List[str] = []
    for match in _NUMBER_TOKEN_RE.finditer(text):
        raw = match.group(1)
        decimal = match.group(2) if len(match.groups()) > 1 and match.group(2) else ""
        normalized = re.sub(r"\s+", "", raw)
        if decimal:
            normalized += decimal.replace(",", ".")
        if normalized:
            out.append(normalized)
    return out


_LATIN_WORD_RE = re.compile(r"\b[a-z][a-z]{3,}\b")


def _find_anglicisms(text: str, *, allowed_lower: set[str]) -> List[str]:
    found: List[str] = []
    for word in _LATIN_WORD_RE.findall(text):
        if word in allowed_lower:
            continue
        found.append(word)
    return found


# Capitalized / mixed-case Latin identifiers (Flutter, BLoC, OtherMark, GetIt).
# Excludes pure lowercase (those are handled by _find_anglicisms).
_TECH_IDENT_RE = re.compile(r"\b(?:[A-Z][A-Za-z0-9]{1,}|[A-Z]+(?:/[A-Z]+)?)\b")


def _find_tech_identifiers(text: str) -> List[str]:
    """Extract candidate tech tokens — capitalized or mixed-case Latin runs."""
    out: List[str] = []
    seen: set[str] = set()
    for token in _TECH_IDENT_RE.findall(text):
        # Skip 1-character all-uppercase tokens (sentence starts with "А" in cyrillic
        # don't match anyway; but defend against stray "I").
        if len(token) < 2:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _try_parse_json(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(text)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict):
        return None
    return parsed
