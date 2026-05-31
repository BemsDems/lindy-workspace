"""Canonical facts extracted deterministically from a Profile.

This is the SINGLE SOURCE OF TRUTH for what the LLM is allowed to claim.

Rationale: in v1 the Analyzer LLM produced `allowed_numbers` itself, which
made anti-hallucination self-referential — if the Analyzer hallucinated a
number, the Writer was then free to use it. In v2, the Analyzer no longer
*produces* a whitelist; it only *selects* facts/numbers from a whitelist
that this module built from the resume YAML.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Pattern, Set

from .models import Profile, Project


# Default smell-phrases — common hallucinations seen in cover letters.
# A claim is flagged iff it appears in the letter AND nowhere in the resume.
#
# This list catches EXACT substrings only. For pattern-based catches (numeric
# percentages, audience scales, time-of-day fabrications), see
# `DEFAULT_FORBIDDEN_REGEXES` below.
DEFAULT_FORBIDDEN_CLAIMS: List[str] = [
    # Domain hallucinations
    "финтех",
    "финтех-проекты",
    "финтех-платформ",
    "банковские транзакции",
    "международные платежи",
    "платёжные сервисы",
    "платежные сервисы",
    "high-load",
    "highload",
    "production-нагрузка",
    "крупнейшая платёжная система",
    "крупнейшей платёжной системы",
    "крупнейшая платежная система",
    "крупнейшей платежной системы",
    "платёжная система Узбекистана",
    "платежная система Узбекистана",
    # Audience-scale hallucinations (exact phrase substring)
    "сотни пользователей",
    "тысячи пользователей",
    "миллионы пользователей",
    "сотни тысяч пользователей",
    "1,3 млн",
    "1.3 млн",
    "1,1 млн",
    "1.1 млн",
    "млн человек",
    "млн пользователей",
    "миллион пользователей",
    "миллионы пользователей",
]


# Pattern-based forbidden claims — catch hallucinations that vary by digit/word.
#
# Each pattern is checked against the FINAL LETTER. If it matches AND the same
# pattern does NOT match anywhere in the resume, the letter is rejected.
#
# This is the "grounded" check: if the candidate genuinely wrote "30% улучшение"
# in their resume, then a letter that says "30% улучшение" is fine. The pattern
# is only treated as forbidden when the letter introduces a number/claim that
# has no support anywhere in the resume.
DEFAULT_FORBIDDEN_REGEX_SOURCES: List[str] = [
    # X% with efficiency / improvement / speedup language
    # "повысил производительность на 30%", "ускорил на 25%", "снизил crash rate на 15%"
    r"\d+\s*%\s*(?:улучшени|быстре|снижени|роста|росту|оптимизаци|производительност|"
    r"конверси|ускорени|сокращени|уменьшени|увеличени)",
    # Inverse word-order: "на 30% быстрее/лучше/меньше"
    r"на\s+\d+\s*%\s*(?:быстре|улучши|снизи|поднял|вырос|сократ|уменьши|увеличи|"
    r"улучшил|оптимизирова)",
    # Audience scale: "X млн пользователей" with various digit variations
    r"\d+([.,]\d+)?\s*(?:млн|миллион(?:а|ов)?)\s+(?:пользовател|клиент|"
    r"установ|компани|человек|посетител)",
    # Audience scale: "X тыс. установок/пользователей/клиентов"
    r"\d+([.,]\d+)?\s*(?:тыс\.?|тысяч)\s+(?:пользовател|клиент|установ|компани|посетител)",
    # Small-talk fabrications: specific call durations
    r"\d+\s*-?\s*минутн(?:ый|ого|ом)\s*созвон",
    r"созвон(?:а|е)?\s+на\s+\d+\s*мин",
    # Small-talk fabrications: specific times-of-day
    r"после\s+1[7-9]\s*[:.]?\s*\d{0,2}",
    r"после\s+2[0-3]\s*[:.]?\s*\d{0,2}",
    # Small-talk fabrications: specific weekdays / parts of week as availability
    r"в\s+будн(?:и|ие|ие\s+дни|ие\s+вечером)",
    r"в\s+(?:понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)\s+вечером",
    r"в\s+выходные\s+вечером",
]


def _compile_forbidden_regexes(sources: List[str]) -> List[Pattern[str]]:
    return [re.compile(pattern, re.IGNORECASE) for pattern in sources]


DEFAULT_FORBIDDEN_REGEXES: List[Pattern[str]] = _compile_forbidden_regexes(
    DEFAULT_FORBIDDEN_REGEX_SOURCES
)


@dataclass
class ProjectFacts:
    """Per-project canonical facts."""

    name: str
    company: str
    industry: str
    description: str
    tech_stack: List[str] = field(default_factory=list)
    achievements: List[str] = field(default_factory=list)
    allowed_numbers: List[str] = field(default_factory=list)


@dataclass
class CanonicalFacts:
    """All deterministic facts about the candidate.

    Built once per Profile at pipeline init; passed read-only into the
    Analyzer (as context) and the validator (as ground truth).
    """

    candidate_name: str
    experience_years: int
    experience_months: int
    summary: str
    profile_text_lower: str          # for forbidden_claim grounding
    allowed_numbers: List[str]       # global whitelist (years + every number across all projects)
    allowed_tech: Set[str]           # tech terms ever mentioned (case-insensitive)
    allowed_project_names: Set[str]
    allowed_company_names: Set[str]
    projects: Dict[str, ProjectFacts] = field(default_factory=dict)
    forbidden_claims: List[str] = field(default_factory=list)
    forbidden_regexes: List[Pattern[str]] = field(default_factory=list)

    def project(self, name: str) -> ProjectFacts | None:
        # Case-insensitive lookup; the LLM may produce slightly different casing.
        for key, facts in self.projects.items():
            if key.lower() == name.lower():
                return facts
        return None

    def forbidden_claims_grounded(self) -> List[str]:
        """Subset of `forbidden_claims` that do NOT appear in the resume.

        If "финтех" appears in the resume itself, it's not forbidden — the
        candidate IS in fintech.
        """
        return [
            phrase for phrase in self.forbidden_claims
            if phrase.lower() not in self.profile_text_lower
        ]

    def forbidden_regexes_grounded(self) -> List[Pattern[str]]:
        """Subset of `forbidden_regexes` that do NOT match the resume.

        If the candidate's resume genuinely contains '30% улучшение', the same
        claim in a letter is legitimate — the regex catches only the case where
        the LLM invented a percentage/scale/timeslot that has no resume support.
        """
        return [
            pattern for pattern in self.forbidden_regexes
            if not pattern.search(self.profile_text_lower)
        ]


# Number tokens: capture digits with optional thousands-style space/comma
# separators AND dotted version strings ("3.0.2", "3.29.0"). The validator
# uses the same shape, so whitelist tokens line up with what the letter contains.
# Examples this matches: "3", "11 381", "1 448", "32 840", "1,300", "3+",
# "3.0.2", "3.29.0", "10+", "2 682".
# v2 (post-stale-whitelist fix): allow up to 12 chars of internal separators
# so multi-thousand numbers like "32 840" / "11 381" match as ONE token.
_NUMBER_RE = re.compile(r"(?<!\w)(\d[\d\s.,]{0,12}\d|\d)\+?(?!\w)")


def extract_canonical_facts(
    profile: Profile,
    *,
    forbidden_claims: List[str] | None = None,
    forbidden_regexes: List[Pattern[str]] | None = None,
) -> CanonicalFacts:
    """Build a `CanonicalFacts` object from a Profile.

    All extraction is regex/string-level — no LLM involved.
    """
    all_numbers: List[str] = []
    all_tech: Set[str] = set()
    all_projects: Set[str] = set()
    all_companies: Set[str] = set()
    project_facts: Dict[str, ProjectFacts] = {}
    text_chunks: List[str] = [profile.summary or ""]

    if profile.experience_years:
        all_numbers.append(str(profile.experience_years))

    for skill in profile.skills_primary + profile.skills_secondary:
        if skill.strip():
            all_tech.add(skill.strip())

    for position in profile.positions:
        if position.company.strip():
            all_companies.add(position.company.strip())
        for project in position.projects:
            facts = _project_facts_from(project, position.company, position.industry)
            project_facts[project.name] = facts
            if project.name.strip():
                all_projects.add(project.name.strip())
            all_tech.update(facts.tech_stack)
            for n in facts.allowed_numbers:
                if n not in all_numbers:
                    all_numbers.append(n)
            text_chunks.append(project.description or "")
            text_chunks.extend(project.achievements)

    text_chunks.extend(all_projects)
    text_chunks.extend(all_companies)
    profile_text = " ".join(c for c in text_chunks if c)

    return CanonicalFacts(
        candidate_name=profile.name,
        experience_years=profile.experience_years,
        experience_months=profile.experience_months,
        summary=profile.summary,
        profile_text_lower=profile_text.lower(),
        allowed_numbers=_dedup(all_numbers),
        allowed_tech=all_tech,
        allowed_project_names=all_projects,
        allowed_company_names=all_companies,
        projects=project_facts,
        forbidden_claims=list(forbidden_claims) if forbidden_claims is not None else list(DEFAULT_FORBIDDEN_CLAIMS),
        forbidden_regexes=list(forbidden_regexes) if forbidden_regexes is not None else list(DEFAULT_FORBIDDEN_REGEXES),
    )


def _project_facts_from(project: Project, company: str, industry: str) -> ProjectFacts:
    numbers: List[str] = []
    text = " ".join([project.description or "", *project.achievements])
    for raw in _NUMBER_RE.findall(text):
        normalized = re.sub(r"\s+", "", raw)
        if normalized and normalized not in numbers:
            numbers.append(normalized)
    return ProjectFacts(
        name=project.name,
        company=company,
        industry=industry,
        description=project.description,
        tech_stack=list(project.tech_stack),
        achievements=list(project.achievements),
        allowed_numbers=numbers,
    )


def _dedup(items: List[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
