from __future__ import annotations

from vacancy_agent.cover_letter import build_cover_letter
from vacancy_agent.letter_adapters.base import LetterAdapter, LetterGenerationResult
from vacancy_agent.schemas import CandidateProfile, Vacancy


class SimpleTemplateLetterAdapter(LetterAdapter):
    """Fallback adapter: old local template generator.

    Keep this for tests/offline mode only. Production generation should use the
    external cover-letter-gen pipeline.
    """

    name = "simple_template"

    async def generate(
        self,
        *,
        vacancy: Vacancy,
        candidate: CandidateProfile | None = None,
    ) -> LetterGenerationResult:
        if candidate is None:
            raise ValueError("SimpleTemplateLetterAdapter requires candidate profile")
        response = build_cover_letter(vacancy, candidate)
        return LetterGenerationResult(
            cover_letter=response.cover_letter,
            provider=self.name,
            passed=True,
            matched_requirements=response.matched_requirements,
            missing_requirements=response.missing_requirements,
            risk_notes=response.risk_notes,
        )
