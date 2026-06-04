from __future__ import annotations

from pathlib import Path

from vacancy_agent.config import PROJECT_ROOT, settings
from vacancy_agent.letter_adapters.base import LetterAdapter
from vacancy_agent.letter_adapters.cover_letter_gen import CoverLetterGenAdapter
from vacancy_agent.letter_adapters.simple import SimpleTemplateLetterAdapter


# Location of the cover-letter-genv2v2 skill bundled in this repository.
# PROJECT_ROOT points at .../vacancy-agent-skill-application-flowv2/project, so the
# repository root is two levels up and the skill lives at <repo-root>/cover-letter-genv2v2.
# Using a repo-relative path keeps generation in sync with the version-controlled
# skill instead of a stale, machine-specific copy.
_REPO_ROOT = PROJECT_ROOT.parent.parent
_BUNDLED_SKILL_PATH = _REPO_ROOT / "cover-letter-genv2v2"

# Legacy absolute path for older local checkouts; kept only as a last-resort fallback.
_LEGACY_SKILL_PATH = Path(
    "/Users/pipyao/.openclaw/workspace-coding/skills/cover-letter-genv2v2"
)


def _candidate_cover_letter_gen_paths() -> list[Path]:
    paths: list[Path] = []

    # 1. Explicit override via COVER_LETTER_GEN_PATH always wins.
    if settings.cover_letter_gen_path:
        paths.append(Path(settings.cover_letter_gen_path))

    # 2. The skill bundled in this repository (stays in sync via git).
    paths.append(_BUNDLED_SKILL_PATH)

    # 3. Legacy absolute path, kept as a last-resort fallback.
    paths.append(_LEGACY_SKILL_PATH)

    # Deduplicate while preserving order.
    unique_paths: list[Path] = []
    for path in paths:
        if path not in unique_paths:
            unique_paths.append(path)

    return unique_paths


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
        "By default it is expected at <repo-root>/cover-letter-genv2v2; "
        "override the location with COVER_LETTER_GEN_PATH. "
        "Checked:\n" + checked
    )


def active_letter_provider_name() -> str:
    return get_letter_adapter().name
