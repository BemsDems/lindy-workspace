"""Load vacancies from a JSON file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Optional

from .models import Vacancy


def load_vacancies(path: str | Path) -> List[Vacancy]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Expected a JSON list at {path}, got {type(raw).__name__}")
    return [Vacancy.from_dict(item) for item in raw if isinstance(item, dict)]


def filter_by_ids(vacancies: Iterable[Vacancy], ids: Iterable[str]) -> List[Vacancy]:
    id_set = {i.strip() for i in ids if i and i.strip()}
    return [v for v in vacancies if v.id in id_set]


def load_selected_ids(path: str | Path) -> Optional[List[str]]:
    p = Path(path)
    if not p.exists():
        return None
    return [
        line.strip()
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
