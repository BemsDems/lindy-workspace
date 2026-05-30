from __future__ import annotations

import asyncio

from vacancy_agent.letter_adapters import LetterGenerationResult, get_letter_adapter
from vacancy_agent.schemas import CandidateProfile, CoverLetterResponse, Vacancy


async def generate_cover_letter_for_vacancy(
    vacancy: Vacancy,
    candidate: CandidateProfile | None = None,
) -> LetterGenerationResult:
    """Generate a cover letter through the configured adapter.

    By default this calls the external cover-letter-gen skill when it is
    available. The simple local template is only a fallback/offline option.
    """

    adapter = get_letter_adapter()
    return await adapter.generate(vacancy=vacancy, candidate=candidate)


def generate_cover_letter_for_vacancy_sync(
    vacancy: Vacancy,
    candidate: CandidateProfile | None = None,
) -> LetterGenerationResult:
    return asyncio.run(generate_cover_letter_for_vacancy(vacancy, candidate))


def build_cover_letter_response_sync(
    vacancy: Vacancy,
    candidate: CandidateProfile | None = None,
) -> CoverLetterResponse:
    return generate_cover_letter_for_vacancy_sync(vacancy, candidate).to_cover_letter_response()
