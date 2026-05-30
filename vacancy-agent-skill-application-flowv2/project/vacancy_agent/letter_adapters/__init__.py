from vacancy_agent.letter_adapters.base import LetterAdapter, LetterGenerationResult
from vacancy_agent.letter_adapters.cover_letter_gen import CoverLetterGenAdapter
from vacancy_agent.letter_adapters.registry import active_letter_provider_name, get_letter_adapter
from vacancy_agent.letter_adapters.simple import SimpleTemplateLetterAdapter

__all__ = [
    "LetterAdapter",
    "LetterGenerationResult",
    "CoverLetterGenAdapter",
    "SimpleTemplateLetterAdapter",
    "get_letter_adapter",
    "active_letter_provider_name",
]
