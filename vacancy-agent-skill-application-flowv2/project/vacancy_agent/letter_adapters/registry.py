from __future__ import annotations

from pathlib import Path

from vacancy_agent.config import PROJECT_ROOT, settings
from vacancy_agent.letter_adapters.base import LetterAdapter
from vacancy_agent.letter_adapters.cover_letter_gen import CoverLetterGenAdapter
from vacancy_agent.letter_adapters.simple import SimpleTemplateLetterAdapter


def _candidate_cover_letter_gen_paths() -> list[Path]:
    paths: list[Path] = []

    if settings.cover_letter_gen_path:
        paths.append(Path(settings.cover_letter_gen_path))
    else:
        paths.append(Path("/Users/pipyao/.openclaw/workspace-coding/skills/cover-letter-genv2v2"))

    return paths


def get_letter_adapter() -> LetterAdapter:
    """Return the configured cover-letter generator adapter.

    Modes:
    - COVER_LETTER_PROVIDER=cover_letter_gen: require external generator.
    - COVER_LETTER_PROVIDER=simple: force local fallback generator.
    - COVER_LETTER_PROVIDER=auto: prefer cover-letter-gen, fallback to simple.
    """

    provider = (settings.cover_letter_provider or "auto").strip().lower()

    if provider == "simple":
        return SimpleTemplateLetterAdapter()

    for skill_path in _candidate_cover_letter_gen_paths():
        adapter = CoverLetterGenAdapter(
            skill_path=skill_path,
            resume_path=Path(settings.cover_letter_gen_resume) if settings.cover_letter_gen_resume else None,
            settings_path=Path(settings.cover_letter_gen_settings) if settings.cover_letter_gen_settings else None,
            append_signature=settings.cover_letter_append_signature,
        )
        if adapter.is_available:
            return adapter

    checked = "\n".join(f"- {p}" for p in _candidate_cover_letter_gen_paths())
    raise FileNotFoundError(
        "cover-letter-genv2v2 not found. "
        "Set COVER_LETTER_GEN_PATH to /Users/pipyao/.openclaw/workspace-coding/skills/cover-letter-genv2v2. "
        "Checked:\n" + checked
    )


def active_letter_provider_name() -> str:
    return get_letter_adapter().name
