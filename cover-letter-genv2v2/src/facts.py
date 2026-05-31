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
from typing import Dict, List, Set

from .models import Profile, Project


# Default smell-phrases — common hallucinations seen in cover letters.
# A claim is flagged iff it appears in the letter AND nowhere in the resume.
DEFAULT_FORBIDDEN_CLAIMS: List[str] = [
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
    "сотни пользователей",
    "тысячи пользователей",
    "миллионы пользователей",
    "сотни тысяч пользователей",
    "крупнейшая платёжная система",
    "крупнейшей платёжной системы",
    "крупнейшая платежная система",
    "крупнейшей платежной системы",
    "платёжная система Узбекистана",
    "платежная система Узбекистана",
    "1,3 млн",
    "1.3 млн",
    "млн человек",
    "млн пользователей",
    "миллион пользователей",
    "миллионы пользователей",
]


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


# Number tokens: capture digits with optional thousands-style space/comma
# separators AND dotted version strings ("3.0.2", "3.29.0"). The validator
# uses the same shape, so whitelist tokens line up with what the letter contains.
# Examples this matches: "3", "11 000", "1,300", "3+", "3.0.2", "3.29.0", "10+".
_NUMBER_RE = re.compile(r"(?<!\w)(\d[\d\s.,]{0,8}\d|\d)\+?(?!\w)")


def extract_canonical_facts(
    profile: Profile,
    *,
    forbidden_claims: List[str] | None = None,
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
