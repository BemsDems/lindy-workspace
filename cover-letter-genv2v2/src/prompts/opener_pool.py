"""Curated opener phrases for cover letter first sentences.

Rationale: in v1 the Writer was told "don't reuse these openers" (negative
constraint). When the model exhausts obvious phrasings it starts inventing
awkward ones. In v2 we instead show it 1-2 *acceptable* openers from a
curated pool and ask it to pick or adapt — positive constraint.

`select_openers(experience_years, used_starts, n=2)` returns `n` openers
that haven't been used recently. Tracking is owned by the pipeline.
"""

from __future__ import annotations

import random
from typing import List


# Each entry is a sentence template with a single `{years}` slot.
# Wording is varied enough that picking 2-3 in a batch reads naturally.
OPENER_TEMPLATES: List[str] = [
    "{years}+ года разработки кроссплатформенных приложений на Flutter.",
    "{years}+ года разработки B2B-систем на Flutter.",
    "Опыт {years}+ лет в создании Flutter-приложений с Clean Architecture.",
    "Более {years} лет работаю с Flutter — от прототипов до production-релизов.",
    "{years}+ года коммерческой Flutter-разработки.",
    "Занимаюсь Flutter-разработкой {years}+ года, в основном в продуктовых командах.",
    "{years}+ года разрабатываю мобильные продукты на Flutter в production.",
    "Опыт {years}+ лет на Flutter в команде с собственным дизайн-системным слоем.",
]


def select_openers(experience_years: int, used_starts: List[str], *, n: int = 2) -> List[str]:
    """Pick `n` openers, preferring ones whose template hasn't been used yet."""
    years = max(experience_years, 1)
    rendered = [tpl.format(years=years) for tpl in OPENER_TEMPLATES]

    used_lower = {s.strip().lower() for s in used_starts if s}
    fresh = [r for r in rendered if r.lower() not in used_lower]
    pool = fresh if fresh else rendered

    rng = random.Random(len(used_starts))  # deterministic per batch position
    rng.shuffle(pool)
    return pool[:n]
