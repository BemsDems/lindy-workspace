"""Load Profile from a YAML resume file."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml

from .models import Language, Position, Profile, Project


def _string_list(value: Any) -> List[str]:
    """Coerce a YAML list to a list of strings.

    Defence against unquoted colons in YAML list items: a line like
    ``- Foo: bar`` inside a list parses as ``{"Foo": "bar"}`` (a dict),
    not as a string. Downstream code does ``" ".join(items)`` which then
    raises ``TypeError: sequence item N: expected str instance, dict found``.

    This helper accepts whatever shape PyYAML produced and returns a
    flat ``list[str]``:
      - str -> kept as-is
      - dict -> flattened to ``"key: value"`` strings (one per pair)
      - list/tuple -> recursively flattened
      - None -> dropped
      - anything else -> ``str(item)``
    """
    if value is None:
        return []
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return [str(value)]

    result: List[str] = []
    for item in value:
        if item is None:
            continue
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            for k, v in item.items():
                if v is None:
                    result.append(str(k))
                else:
                    result.append(f"{k}: {v}")
        elif isinstance(item, (list, tuple)):
            result.extend(_string_list(item))
        else:
            result.append(str(item))
    return result


def load_profile(path: str | Path) -> Profile:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    personal = raw.get("personal") or {}
    desired = raw.get("desired") or {}
    skills = raw.get("skills") or {}
    experience = raw.get("experience") or {}
    languages_raw = raw.get("languages") or []

    return Profile(
        name=str(personal.get("name", "")),
        location=str(personal.get("location", "")),
        relocation=bool(personal.get("relocation", False)),
        summary=str(raw.get("summary", "")),
        experience_years=int(experience.get("total_years", 0) or 0),
        experience_months=int(experience.get("total_months", 0) or 0),
        desired_title=str(desired.get("title", "")),
        desired_work_format=_string_list(desired.get("work_format")),
        skills_primary=_string_list(skills.get("primary")),
        skills_secondary=_string_list(skills.get("secondary")),
        skills_soft=_string_list(skills.get("soft")),
        positions=[_position_from_dict(p) for p in experience.get("positions") or []],
        languages=[_language_from_dict(l) for l in languages_raw],
    )


def _position_from_dict(data: Dict[str, Any]) -> Position:
    return Position(
        title=str(data.get("title", "")),
        company=str(data.get("company", "")),
        industry=str(data.get("industry", "")),
        period_start=str(data.get("period_start", "")),
        period_end=str(data.get("period_end", "")),
        duration_months=int(data.get("duration_months", 0) or 0),
        current=bool(data.get("current", False)),
        projects=[_project_from_dict(p) for p in data.get("projects") or []],
    )


def _project_from_dict(data: Dict[str, Any]) -> Project:
    return Project(
        name=str(data.get("name", "")),
        description=str(data.get("description", "")),
        role=str(data.get("role", "")),
        tech_stack=_string_list(data.get("tech_stack")),
        achievements=_string_list(data.get("achievements")),
    )


def _language_from_dict(data: Dict[str, Any]) -> Language:
    return Language(
        name=str(data.get("name", "")),
        level=str(data.get("level", "")),
        level_code=str(data.get("level_code", "")),
    )
