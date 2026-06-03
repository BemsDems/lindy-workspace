"""Domain locking: one selected project = one business domain.

The analyzer already filters NUMBERS to the selected project. This module adds
the missing piece — a DOMAIN lock. A letter written about, say, the B2B/marking
project (OtherMark) must not drift into food-delivery, real-estate, social-
network or planner language borrowed from the candidate's *other* projects.

Implementation note: we deliberately do NOT add a ``domain_tag`` field to the
resume YAML / models / config loader (that would require editing the loader,
which is not covered by tests here). Instead we keep a small, explicit
project-name -> domain map plus a per-domain list of signal words. The pipeline
looks up the selected project's domain and forbids the signal words of every
OTHER domain in that specific letter. This is a per-letter (ungrounded)
constraint, not a resume-grounding check: the candidate's other projects may
legitimately contain these words, but they must not leak into THIS letter.
"""

from __future__ import annotations

from typing import List


# Project name (exactly as it appears in config/resume.yaml) -> domain tag.
# Keep in sync with the project names in config/resume.yaml.
_PROJECT_DOMAINS = {
    "OtherMark": "b2b_marking",
    "DIOM": "social",
    "ЭталонИнвест": "realestate",
    "Food One": "food",
    "Integra": "planner",
}


# Signal words characteristic of each domain. Kept intentionally CONSERVATIVE
# (only distinctive terms) so the hard foreign-domain check below does not
# misfire on generic vocabulary. If a letter is about project X (domain D),
# the signal words of every domain != D are treated as foreign leakage.
_DOMAIN_SIGNALS = {
    "food": ["доставка еды", "ресторан", "самовывоз", "кэшбэк", "cashback"],
    "realestate": ["недвижим", "застройщик", "квартир", "ипотек", "риелтор", "девелопер"],
    "social": ["социальн", "сообществ", "мессенджер"],
    "b2b_marking": ["маркировк", "честный знак", "gtin", "оборот товаров"],
    "planner": ["заметк", "to-do", "ежедневник", "планировщик"],
}


def domain_for_project(project_name: str) -> str:
    """Return the domain tag for a project name, or '' if unknown."""
    if not project_name:
        return ""
    name = project_name.strip()
    if name in _PROJECT_DOMAINS:
        return _PROJECT_DOMAINS[name]
    lowered = name.lower()
    for key, tag in _PROJECT_DOMAINS.items():
        if key.lower() == lowered:
            return tag
    return ""


def foreign_domain_terms(selected_domain_tag: str) -> List[str]:
    """Signal words of every domain EXCEPT the selected project's domain.

    Returns an empty list when the domain is empty/unknown: without a known
    domain we cannot safely decide what is "foreign", and we must NOT forbid
    every domain (that would also forbid the project's own vocabulary).
    """
    if not selected_domain_tag or selected_domain_tag not in _DOMAIN_SIGNALS:
        return []
    terms: List[str] = []
    for tag, words in _DOMAIN_SIGNALS.items():
        if tag != selected_domain_tag:
            terms.extend(words)
    return terms


def foreign_domain_terms_for_project(project_name: str) -> List[str]:
    """Convenience wrapper: foreign-domain terms for a given project name."""
    return foreign_domain_terms(domain_for_project(project_name))


__all__ = [
    "domain_for_project",
    "foreign_domain_terms",
    "foreign_domain_terms_for_project",
]
