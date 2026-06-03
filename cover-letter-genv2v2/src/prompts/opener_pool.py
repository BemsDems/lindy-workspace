"""Curated opener phrases for cover letter first sentences.

v3: switched from years-based templates (e.g. "{years}+ года Flutter-разработки.")
to achievement-based hooks. Years-in-opener was the dominant template the LLM
fell back to under VACANCY_CONTEXT pressure, producing the canned
"3+ года коммерческой Flutter-разработки" opener across unrelated vacancies.

The new pool drives the opener from the selected project: its top achievement,
company name (English names preserved), industry, or tech stack. If no project
is selected (low-confidence / universal mode) we fall back to industry-neutral
hooks that still avoid "X+ years" framing.

v4 (P3): the chosen achievement is no longer hard-coded to achievements[0].
`select_openers` now receives the vacancy text and picks the achievement most
relevant to it (with a deterministic per-batch rotation fallback), so the first
sentence and angle vary from vacancy to vacancy instead of repeating the same
gRPC opener.

API: `select_openers(facts, selected_project, used_starts, n=2, vacancy_text="",
rotate=0)` returns up to `n` opener templates. Tracking of recently-used openers
is owned by the pipeline.
"""

from __future__ import annotations

import random
import re
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # avoid a runtime import cycle with facts.py
    from ..facts import CanonicalFacts, ProjectFacts


# ---------------------------------------------------------------------------
# Project-driven templates (used when a project was selected by the analyzer).
# Each template references project facts. The Writer is instructed to USE
# THE MEANING of one opener as the first sentence after the greeting, not to
# quote it verbatim — so the LLM can adapt wording to the vacancy context.
# ---------------------------------------------------------------------------

# Slots:
#   {company}       — English company / project owner name, kept verbatim
#   {industry}      — e.g. "Food Delivery", "B2B SaaS", "PropTech"
#   {top_ach}       — first achievement from the selected project (already
#                     a complete sentence in the resume)
#   {top_tech}      — comma-joined top 2-3 tech items from the project
#   {project_name}  — project codename (used sparingly — kept English)
_PROJECT_TEMPLATES: List[str] = [
    "В {company} {top_ach_lc}",
    "На проекте {project_name} ({industry}) {top_ach_lc}",
    "Делал {industry}-приложение на Flutter в {company}: {top_ach_lc}",
    "В {company} ({industry}) — {top_tech} — {top_ach_lc}",
    "Последний проект — {project_name} в {company}, {industry}: {top_ach_lc}",
    "В {company} работал над {industry}-продуктом на Flutter, {top_tech}.",
]

# Used when the selected project has no usable top achievement — we still
# anchor on the project rather than on "X years".
_PROJECT_FALLBACK_TEMPLATES: List[str] = [
    "Последний проект — {project_name} в {company}, {industry}, Flutter + {top_tech}.",
    "В {company} вёл Flutter-разработку {industry}-продукта на {top_tech}.",
    "Работал над {industry}-продуктом в {company}, стек: Flutter, {top_tech}.",
]

# Used when no project was selected (low-confidence or universal mode).
# Still avoids "X+ years" framing — anchors on role / specialization.
_GENERIC_TEMPLATES: List[str] = [
    "Flutter-разработчик с опытом production-релизов в App Store и Google Play.",
    "Пишу Flutter-приложения в продуктовых командах — от MVP до релиза.",
    "Делал кроссплатформенные мобильные продукты на Flutter с Clean Architecture.",
    "Работаю с Flutter в проде: BLoC/Cubit, REST/gRPC, CI/CD, релизы в сторы.",
]


def _lower_first(text: str) -> str:
    """Lowercase only the first letter (used to splice an achievement into mid-sentence).

    The resume stores achievements as full sentences starting with a capital
    (e.g. "Migrated the Flutter SDK..."). When we splice them after "В Food One"
    they should read "в Food One migrated the Flutter SDK..." — but we keep
    English achievement bodies as-is and only adjust the leading character.
    """
    cleaned = text.strip().rstrip(".")
    if not cleaned:
        return cleaned
    return cleaned[0].lower() + cleaned[1:]


def _format_top_tech(tech_stack: List[str]) -> str:
    """Pick 2-3 representative tech items, comma-joined."""
    items = [str(t).strip() for t in tech_stack if str(t).strip()]
    if not items:
        return "Flutter, Dart"
    return ", ".join(items[:3])


def _pick_achievement(
    project: "ProjectFacts",
    vacancy_text: str,
    rotate: int,
) -> str:
    """Choose the achievement most relevant to the vacancy.

    Scores each achievement by how many of its 4+ character words appear in the
    vacancy text; ties prefer the earlier achievement. If no achievement shares
    any word with the vacancy, rotate deterministically by batch position so
    different vacancies don't all open on achievements[0].
    """
    achs = [a.strip() for a in (project.achievements or []) if a and a.strip()]
    if not achs:
        return ""
    vt = (vacancy_text or "").lower()

    def score(a: str) -> int:
        words = {w for w in re.findall(r"[a-zа-яё0-9]+", a.lower()) if len(w) >= 4}
        return sum(1 for w in words if w in vt)

    best = max(range(len(achs)), key=lambda i: (score(achs[i]), -i))
    if score(achs[best]) == 0:
        best = rotate % len(achs)
    return achs[best]


def _render_project_template(
    template: str,
    *,
    project: "ProjectFacts",
    top_ach: Optional[str] = None,
) -> Optional[str]:
    """Fill a project template. Returns None if any required slot is empty."""
    company = (project.company or "").strip()
    industry = (project.industry or "").strip()
    project_name = (project.name or "").strip()
    if top_ach is None:
        top_ach = project.achievements[0].strip() if project.achievements else ""
    else:
        top_ach = (top_ach or "").strip()
    top_tech = _format_top_tech(list(project.tech_stack))

    needs_company = "{company}" in template
    needs_industry = "{industry}" in template
    needs_project_name = "{project_name}" in template
    needs_top_ach = "{top_ach_lc}" in template

    if needs_company and not company:
        return None
    if needs_industry and not industry:
        return None
    if needs_project_name and not project_name:
        return None
    if needs_top_ach and not top_ach:
        return None

    return template.format(
        company=company,
        industry=industry,
        project_name=project_name,
        top_ach_lc=_lower_first(top_ach),
        top_tech=top_tech,
    )


def select_openers(
    facts: "CanonicalFacts",
    selected_project: str,
    used_starts: List[str],
    *,
    n: int = 2,
    vacancy_text: str = "",
    rotate: int = 0,
) -> List[str]:
    """Pick `n` opener candidates.

    Strategy:
      1. If `selected_project` resolves and has a top achievement → use
         project templates that splice the vacancy-relevant achievement into
         the first sentence (see `_pick_achievement`).
      2. If the project resolves but has no achievement → use project-fallback
         templates that anchor on the project name + tech.
      3. If no project resolves → use generic role-anchored templates.

    In all cases we avoid years-of-experience framing.

    `vacancy_text` biases which achievement is spliced; `rotate` provides a
    deterministic fallback when no achievement matches the vacancy.
    `used_starts` is consulted to prefer openers whose rendered form hasn't
    been used recently in this batch.
    """
    project = facts.project(selected_project) if selected_project else None

    pool: List[str] = []

    if project is not None:
        chosen_ach = _pick_achievement(project, vacancy_text, rotate)
        if project.achievements:
            for tpl in _PROJECT_TEMPLATES:
                rendered = _render_project_template(tpl, project=project, top_ach=chosen_ach)
                if rendered:
                    pool.append(rendered)
        if not pool:
            for tpl in _PROJECT_FALLBACK_TEMPLATES:
                rendered = _render_project_template(tpl, project=project)
                if rendered:
                    pool.append(rendered)

    if not pool:
        pool = list(_GENERIC_TEMPLATES)

    used_lower = {s.strip().lower() for s in used_starts if s}
    fresh = [r for r in pool if r.lower() not in used_lower]
    final_pool = fresh if fresh else pool

    rng = random.Random(len(used_starts))  # deterministic per batch position
    rng.shuffle(final_pool)
    return final_pool[:n]
