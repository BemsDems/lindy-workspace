"""Load Profile from a YAML resume file."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from .models import Language, Position, Profile, Project


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
        desired_work_format=list(desired.get("work_format") or []),
        skills_primary=list(skills.get("primary") or []),
        skills_secondary=list(skills.get("secondary") or []),
        skills_soft=list(skills.get("soft") or []),
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
        tech_stack=list(data.get("tech_stack") or []),
        achievements=list(data.get("achievements") or []),
    )


def _language_from_dict(data: Dict[str, Any]) -> Language:
    return Language(
        name=str(data.get("name", "")),
        level=str(data.get("level", "")),
        level_code=str(data.get("level_code", "")),
    )
