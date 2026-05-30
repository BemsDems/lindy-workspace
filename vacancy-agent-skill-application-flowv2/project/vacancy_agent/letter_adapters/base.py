from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from vacancy_agent.schemas import CandidateProfile, CoverLetterResponse, Vacancy


@dataclass(slots=True)
class LetterGenerationResult:
    """Normalized result returned by any cover-letter generator adapter."""

    cover_letter: str
    provider: str
    passed: bool = True
    matched_requirements: list[str] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_cover_letter_response(self) -> CoverLetterResponse:
        return CoverLetterResponse(
            cover_letter=self.cover_letter,
            matched_requirements=self.matched_requirements,
            missing_requirements=self.missing_requirements,
            risk_notes=self.risk_notes,
        )


class LetterAdapter(ABC):
    """Adapter interface for cover-letter generation.

    The vacancy-agent does not own the generation algorithm. It asks an adapter
    for a draft, then handles human approval/editing and platform submission.
    """

    name: str

    @abstractmethod
    async def generate(
        self,
        *,
        vacancy: Vacancy,
        candidate: CandidateProfile | None = None,
    ) -> LetterGenerationResult:
        raise NotImplementedError
