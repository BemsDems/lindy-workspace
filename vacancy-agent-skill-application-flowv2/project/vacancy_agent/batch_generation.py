from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from textwrap import shorten

from vacancy_agent.application_flow import ApplicationFlow
from vacancy_agent.config import LOGS_DIR
from vacancy_agent.letter_generation import generate_cover_letter_for_vacancy
from vacancy_agent.schemas import ApplicationDraft, CandidateProfile, Vacancy, VacancyStatus
from vacancy_agent.storage import storage
from vacancy_agent.utils.ids import make_id


@dataclass(slots=True)
class BatchLetterResult:
    vacancy_id: str
    ok: bool
    status: str
    message: str | None = None


async def pre_generate_cover_letters(
    vacancy_ids: list[str],
    *,
    parallel: int = 3,
    force: bool = False,
    debug_letters: bool = False,
    debug_file: Path | None = None,
) -> list[BatchLetterResult]:
    candidate = storage.load_candidate_profile()

    if not candidate:
        raise ValueError("Профиль кандидата не найден. Выполни: init-profile")

    semaphore = asyncio.Semaphore(max(1, parallel))
    debug_path = _prepare_debug_file(debug_file) if debug_letters else None
    debug_lock = asyncio.Lock()

    async def generate_one(vacancy_id: str) -> tuple[BatchLetterResult, ApplicationDraft | None, Vacancy | None]:
        async with semaphore:
            return await _generate_one(
                vacancy_id,
                candidate,
                force=force,
                debug_file=debug_path,
                debug_lock=debug_lock,
            )

    tasks = [generate_one(vacancy_id) for vacancy_id in vacancy_ids]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[BatchLetterResult] = []
    drafts_to_save: list[ApplicationDraft] = []
    vacancies_to_mark: list[Vacancy] = []

    for vacancy_id, raw in zip(vacancy_ids, raw_results):
        if isinstance(raw, Exception):
            results.append(
                BatchLetterResult(
                    vacancy_id=vacancy_id,
                    ok=False,
                    status="error",
                    message=str(raw),
                )
            )
            continue

        result, draft, vacancy = raw
        results.append(result)

        if draft:
            drafts_to_save.append(draft)

        if vacancy:
            vacancies_to_mark.append(vacancy)

    for draft in drafts_to_save:
        storage.upsert_application(draft)

    for vacancy in vacancies_to_mark:
        storage.update_vacancy_status(vacancy.id, VacancyStatus.DRAFT)

    return results


async def _generate_one(
    vacancy_id: str,
    candidate: CandidateProfile,
    *,
    force: bool,
    debug_file: Path | None = None,
    debug_lock: asyncio.Lock | None = None,
) -> tuple[BatchLetterResult, ApplicationDraft | None, Vacancy | None]:
    vacancy = storage.find_vacancy(vacancy_id)

    if not vacancy:
        return (
            BatchLetterResult(
                vacancy_id=vacancy_id,
                ok=False,
                status="not_found",
                message="Вакансия не найдена",
            ),
            None,
            None,
        )

    existing = _latest_draft_for_vacancy(vacancy.id)

    if existing and existing.cover_letter.strip() and not force:
        return (
            BatchLetterResult(
                vacancy_id=vacancy.id,
                ok=True,
                status="already_has_draft",
                message="Черновик уже есть, генерация пропущена",
            ),
            None,
            None,
        )

    generation = await generate_cover_letter_for_vacancy(vacancy, candidate)

    valid: bool | None = None
    validation_error: str | None = None

    if generation.cover_letter:
        valid, validation_error = ApplicationFlow.validate_cover_letter(
            generation.cover_letter,
            candidate,
        )

    if debug_file:
        await _append_debug_record(
            debug_file,
            debug_lock,
            vacancy=vacancy,
            generation=generation,
            valid=valid,
            validation_error=validation_error,
        )

    if not generation.cover_letter:
        return (
            BatchLetterResult(
                vacancy_id=vacancy.id,
                ok=False,
                status="generation_failed",
                message=f"Генератор не вернул текст: {generation.error or generation.risk_notes}",
            ),
            None,
            None,
        )

    if not valid:
        return (
            BatchLetterResult(
                vacancy_id=vacancy.id,
                ok=False,
                status="validation_failed",
                message=validation_error,
            ),
            None,
            None,
        )

    warning = None

    if generation.risk_notes:
        warning = "; ".join(generation.risk_notes)

    draft = ApplicationDraft(
        id=make_id(vacancy.id),
        vacancy_id=vacancy.id,
        vacancy_url=vacancy.url,
        cover_letter=generation.cover_letter.strip(),
        status="draft",
        updated_at=datetime.now(),
    )

    return (
        BatchLetterResult(
            vacancy_id=vacancy.id,
            ok=True,
            status="draft_generated",
            message=warning,
        ),
        draft,
        vacancy,
    )


def _latest_draft_for_vacancy(vacancy_id: str) -> ApplicationDraft | None:
    drafts = [
        item
        for item in storage.load_applications()
        if item.vacancy_id == vacancy_id
    ]

    if not drafts:
        return None

    return sorted(drafts, key=lambda item: item.updated_at, reverse=True)[0]


def _prepare_debug_file(debug_file: Path | None = None) -> Path:
    path = debug_file or LOGS_DIR / f"cover_letter_debug_{datetime.now():%Y%m%d_%H%M%S}.md"
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Cover letter debug report\n\n"
        f"Created at: {datetime.now().isoformat(timespec='seconds')}\n\n"
        "Каждая секция содержит вакансию, метаданные генератора и итоговое письмо.\n",
        encoding="utf-8",
    )
    return path


async def _append_debug_record(
    debug_file: Path,
    debug_lock: asyncio.Lock | None,
    *,
    vacancy: Vacancy,
    generation,
    valid: bool | None,
    validation_error: str | None,
) -> None:
    content = _format_debug_record(
        vacancy=vacancy,
        generation=generation,
        valid=valid,
        validation_error=validation_error,
    )

    if debug_lock:
        async with debug_lock:
            with debug_file.open("a", encoding="utf-8") as file:
                file.write(content)
        return

    with debug_file.open("a", encoding="utf-8") as file:
        file.write(content)


def _format_debug_record(
    *,
    vacancy: Vacancy,
    generation,
    valid: bool | None,
    validation_error: str | None,
) -> str:
    metadata = generation.metadata or {}

    requirements = "\n".join(
        f"- {item}" for item in (vacancy.requirements or [])[:25]
    ) or "—"

    responsibilities = "\n".join(
        f"- {item}" for item in (vacancy.responsibilities or [])[:25]
    ) or "—"

    tags = ", ".join(vacancy.tags or []) or "—"
    description = _clip_text(vacancy.description or "", limit=4500)
    letter = (generation.cover_letter or "").strip() or "—"

    risk_notes = "; ".join(generation.risk_notes or []) or "—"

    used_tech = ", ".join(
        metadata.get("used_tech") or generation.matched_requirements or []
    ) or "—"

    used_numbers = ", ".join(
        str(item) for item in (metadata.get("used_numbers") or [])
    ) or "—"

    violations = ", ".join(
        str(item) for item in (metadata.get("violations") or [])
    ) or "—"

    return f"""


---

## {vacancy.company or '—'} — {vacancy.title or '—'}

### Вакансия

- ID: `{vacancy.id}`
- URL: {vacancy.url or '—'}
- Компания: {vacancy.company or '—'}
- Должность: {vacancy.title or '—'}
- Локация: {vacancy.location or '—'}
- Формат: {getattr(vacancy.work_format, 'value', vacancy.work_format) or '—'}
- Зарплата: {vacancy.salary or '—'}
- Опыт: {vacancy.experience or '—'}
- Английский: {vacancy.english_level or '—'}
- Теги: {tags}

### Требования

{requirements}

### Обязанности

{responsibilities}

### Описание вакансии

{description or '—'}

### Метаданные генерации

- Provider: {generation.provider}
- Adapter passed: {generation.passed}
- Generator passed: {metadata.get('generator_passed', '—')}
- Validation passed: {valid if valid is not None else '—'}
- Validation error: {validation_error or '—'}
- Confidence: {metadata.get('confidence', '—')}
- Confidence reason: {metadata.get('confidence_reason', '—')}
- Selected project: {metadata.get('selected_project', '—')}
- Word count: {metadata.get('word_count', '—')}
- Attempts: {metadata.get('attempts', '—')}
- Used tech: {used_tech}
- Used numbers: {used_numbers}
- Violations: {violations}
- Risk notes: {risk_notes}
- Error: {generation.error or metadata.get('error') or '—'}

### Итоговое письмо

~~~text
{letter}
~~~
"""


def _clip_text(value: str, *, limit: int) -> str:
    value = " ".join(value.split())

    if len(value) <= limit:
        return value

    return shorten(value, width=limit, placeholder="...")
