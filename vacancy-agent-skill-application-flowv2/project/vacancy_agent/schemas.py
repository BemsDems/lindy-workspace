from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class SourceType(str, Enum):
    PLAYWRIGHT = "playwright"
    REQUESTS = "requests"


class WorkFormat(str, Enum):
    REMOTE = "remote"
    OFFICE = "office"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"


class VacancyStatus(str, Enum):
    NEW = "new"
    VIEWED = "viewed"
    DRAFT = "draft"
    APPLIED = "applied"
    REJECTED = "rejected"
    INVITED = "invited"
    SKIPPED = "skipped"
    ERROR = "error"


class PageState(str, Enum):
    OK = "ok"
    NO_RESULTS = "no_results"
    LOGIN_REQUIRED = "login_required"
    CAPTCHA = "captcha"
    RATE_LIMIT = "rate_limit"
    BLOCKED = "blocked"
    UNKNOWN_LAYOUT = "unknown_layout"
    NETWORK_ERROR = "network_error"
    PARSE_ERROR = "parse_error"


class VacancySource(BaseModel):
    id: str
    name: str
    url: str
    type: SourceType = SourceType.PLAYWRIGHT
    enabled: bool = True
    settings: dict[str, Any] = Field(default_factory=dict)
    # Optional CSS selectors. They override generic extractor defaults.
    selectors: dict[str, str] = Field(default_factory=dict)


class Vacancy(BaseModel):
    id: str
    source_id: str
    source_name: str
    title: str
    company: str
    url: str
    salary: Optional[str] = None
    location: Optional[str] = None
    country: Optional[str] = None
    work_format: WorkFormat = WorkFormat.UNKNOWN
    work_format_raw: Optional[str] = None
    experience: Optional[str] = None
    experience_raw: Optional[str] = None
    employment_type: Optional[str] = None
    employment_type_raw: Optional[str] = None
    work_schedule: Optional[str] = None
    working_hours: Optional[str] = None
    english_level: Optional[str] = None
    description: Optional[str] = None
    requirements: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    contact_links: list[str] = Field(default_factory=list)
    contact_telegram: Optional[str] = None
    published_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: VacancyStatus = VacancyStatus.NEW
    seen_on_site: bool = False
    applied_by_us: bool = False
    login_required: bool = False
    raw_html: Optional[str] = None


class SearchParams(BaseModel):
    query: Optional[str] = None
    city: Optional[str] = None
    remote: bool = False
    max_pages: int = 1
    max_vacancies: int = 30
    # Safety: default is read-only browsing. Explicit opt-in is required for any account actions.
    allow_hh_actions: bool = False


class CandidateProfile(BaseModel):
    """Candidate profile loaded from `data/candidate_profile.yaml`.

    The canonical schema is the nested resume schema from `cover-letter-gen`
    (`personal`, `contacts`, `desired`, `summary`, `skills`, `experience`, ...).
    The model also tolerates the old flat demo schema so existing local files do
    not break immediately.
    """

    model_config = ConfigDict(extra="allow")

    # Canonical nested resume fields copied from cover-letter-gen/config/resume.yaml.
    personal: dict[str, Any] = Field(default_factory=dict)
    contacts: dict[str, Any] = Field(default_factory=dict)
    desired: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    skills: Any = Field(default_factory=dict)
    experience: Any = Field(default_factory=dict)
    education: list[dict[str, Any]] = Field(default_factory=list)
    languages: list[dict[str, Any]] = Field(default_factory=list)
    restrictions: list[str] = Field(default_factory=list)

    # Optional convenience fields that may be present in a lightweight profile.
    resume_url: Optional[str] = None
    preferred_work_format: Optional[str] = None
    desired_salary: Optional[str] = None

    def _extra_value(self, key: str, default: Any = None) -> Any:
        extra = self.model_extra or {}
        return extra.get(key, default)

    @property
    def name(self) -> str:
        return str(self.personal.get("name") or self._extra_value("name", "")).strip()

    @property
    def location(self) -> str:
        return str(self.personal.get("location") or self._extra_value("location", "")).strip()

    @property
    def position(self) -> str:
        return str(
            self.desired.get("title")
            or self._extra_value("position")
            or self._extra_value("desired_title")
            or ""
        ).strip()

    @property
    def work_formats(self) -> list[str]:
        value = self.desired.get("work_format") or self._extra_value("work_format") or self.preferred_work_format
        if isinstance(value, list):
            return [str(item) for item in value if item]
        if value:
            return [str(value)]
        return []

    @property
    def experience_years(self) -> int:
        if isinstance(self.experience, dict):
            return int(self.experience.get("total_years") or 0)
        return int(self._extra_value("experience_years", 0) or 0)

    @property
    def experience_months(self) -> int:
        if isinstance(self.experience, dict):
            return int(self.experience.get("total_months") or 0)
        return int(self._extra_value("experience_months", 0) or 0)

    @property
    def experience_summary(self) -> str:
        if isinstance(self.experience, str) and self.experience.strip():
            return self.experience.strip()

        parts: list[str] = []
        if self.summary:
            parts.append(self.summary.strip())

        if self.experience_years or self.experience_months:
            parts.append(
                f"Общий опыт: {self.experience_years} г. {self.experience_months} мес."
            )

        recent = self.recent_positions(limit=2)
        if recent:
            parts.append("Ключевой опыт: " + "; ".join(recent))

        return "\n".join(part for part in parts if part).strip()

    @property
    def skills_flat(self) -> list[str]:
        raw = self.skills
        out: list[str] = []
        if isinstance(raw, dict):
            for value in raw.values():
                if isinstance(value, list):
                    out.extend(str(item) for item in value if item)
                elif value:
                    out.append(str(value))
        elif isinstance(raw, list):
            out.extend(str(item) for item in raw if item)
        elif raw:
            out.append(str(raw))

        legacy_tech = self._extra_value("tech_stack", [])
        if isinstance(legacy_tech, list):
            out.extend(str(item) for item in legacy_tech if item)

        return list(dict.fromkeys(item.strip() for item in out if item and item.strip()))

    @property
    def tech_stack(self) -> list[str]:
        # Backward-compatible property for the old generator code.
        return self.skills_flat

    @property
    def strengths(self) -> list[str]:
        raw = self._extra_value("strengths", [])
        return [str(item) for item in raw] if isinstance(raw, list) else []

    @property
    def project_summaries(self) -> list[str]:
        if isinstance(self.experience, dict):
            out: list[str] = []
            for position in self.experience.get("positions") or []:
                company = position.get("company", "")
                for project in position.get("projects") or []:
                    name = project.get("name", "")
                    description = project.get("description", "")
                    achievements = project.get("achievements") or []
                    achievement = achievements[0] if achievements else ""
                    chunks = [chunk for chunk in [company, name, description, achievement] if chunk]
                    if chunks:
                        out.append(" — ".join(chunks))
            return out

        raw = self._extra_value("projects", [])
        return [str(item) for item in raw] if isinstance(raw, list) else []

    @property
    def contact_lines(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for key in ("email", "phone", "telegram", "telegram_url", "linkedin", "github"):
            value = self.contacts.get(key) or self._extra_value(key)
            if value:
                out[key] = str(value)
        if self.resume_url:
            out["resume_url"] = self.resume_url
        return out

    def recent_positions(self, limit: int = 3) -> list[str]:
        if not isinstance(self.experience, dict):
            return []
        out: list[str] = []
        for position in self.experience.get("positions") or []:
            title = position.get("title", "")
            company = position.get("company", "")
            period_start = position.get("period_start", "")
            period_end = position.get("period_end", "") or "н.в."
            label = " — ".join(chunk for chunk in [title, company] if chunk)
            if period_start or period_end:
                label = f"{label} ({period_start}–{period_end})" if label else f"{period_start}–{period_end}"
            if label:
                out.append(label)
        return out[:limit]


class CoverLetterRequest(BaseModel):
    vacancy: Vacancy
    candidate: CandidateProfile
    template: str


class CoverLetterResponse(BaseModel):
    cover_letter: str
    matched_requirements: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class ApplicationDraft(BaseModel):
    id: str
    vacancy_id: str
    vacancy_url: str
    cover_letter: str
    status: str = "draft"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes: Optional[str] = None
