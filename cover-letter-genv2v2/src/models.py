"""Data models shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Project:
    name: str
    description: str
    role: str = ""
    tech_stack: List[str] = field(default_factory=list)
    achievements: List[str] = field(default_factory=list)


@dataclass
class Position:
    title: str
    company: str
    industry: str = ""
    period_start: str = ""
    period_end: str = ""
    duration_months: int = 0
    current: bool = False
    projects: List[Project] = field(default_factory=list)


@dataclass
class Language:
    name: str
    level: str = ""
    level_code: str = ""


@dataclass
class Profile:
    name: str
    location: str = ""
    relocation: bool = False
    summary: str = ""
    experience_years: int = 0
    experience_months: int = 0
    desired_title: str = ""
    desired_work_format: List[str] = field(default_factory=list)
    skills_primary: List[str] = field(default_factory=list)
    skills_secondary: List[str] = field(default_factory=list)
    skills_soft: List[str] = field(default_factory=list)
    positions: List[Position] = field(default_factory=list)
    languages: List[Language] = field(default_factory=list)

    @property
    def all_skills(self) -> List[str]:
        return self.skills_primary + self.skills_secondary + self.skills_soft

    @property
    def all_projects(self) -> List[Project]:
        out: List[Project] = []
        for p in self.positions:
            out.extend(p.projects)
        return out


@dataclass
class Vacancy:
    """Minimal subset of the vacancy schema used by the pipeline.

    Designed to absorb extra fields without breaking — only the named fields
    below are used; anything else is ignored at construction time.
    """

    id: str = ""
    title: str = ""
    company: str = ""
    url: str = ""
    description: Optional[str] = None
    requirements: List[str] = field(default_factory=list)
    work_format: Optional[str] = None
    location: Optional[str] = None
    salary: Optional[str] = None
    english_level: Optional[str] = None
    tags: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Vacancy":
        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            company=str(data.get("company", "")),
            url=str(data.get("url", "")),
            description=data.get("description"),
            requirements=list(data.get("requirements") or []),
            work_format=data.get("work_format"),
            location=data.get("location"),
            salary=data.get("salary"),
            english_level=data.get("english_level"),
            tags=list(data.get("tags") or []),
        )
