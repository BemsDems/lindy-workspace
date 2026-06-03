from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict

from .facts import CanonicalFacts
from .llm_client import LLMClient
from .models import Vacancy
from .prompts.analyzer import ANALYZER_SYSTEM, build_analyzer_user

logger = logging.getLogger(__name__)


def _safe_text(value: object) -> str:
    if value is None:
        return ""

    if isinstance(value, (list, tuple, set)):
        return " ".join(_safe_text(x) for x in value)

    if isinstance(value, dict):
        return " ".join(f"{k} {_safe_text(v)}" for k, v in value.items())

    return str(value)


def _get_field(obj: object, name: str, default: object = "") -> object:
    if isinstance(obj, dict):
        return obj.get(name, default)

    return getattr(obj, name, default)


def _vacancy_text(vacancy: Vacancy) -> str:
    return " ".join(
        [
            _safe_text(_get_field(vacancy, "title")),
            _safe_text(_get_field(vacancy, "company")),
            _safe_text(_get_field(vacancy, "description")),
            _safe_text(_get_field(vacancy, "requirements")),
        ]
    ).lower()


def _project_text(project: object) -> str:
    return " ".join(
        [
            _safe_text(_get_field(project, "name")),
            _safe_text(_get_field(project, "company")),
            _safe_text(_get_field(project, "industry")),
            _safe_text(_get_field(project, "description")),
            _safe_text(_get_field(project, "tech_stack")),
            _safe_text(_get_field(project, "achievements")),
        ]
    ).lower()


# Role-context vocabularies for role-aware project scoring.
_TEACHING_VACANCY_TERMS = (
    "преподавател",
    "спикер",
    "ментор",
    "тренер",
    "лектор",
    "evangelist",
    "advocate",
    "teach",
    "mentor",
    "обуч",
    "курс",
    "edtech",
    "лекц",
    "воркшоп",
    "митап",
    "meetup",
    "выступлени",
    "наставник",
    "куратор",
)

_TEACHING_PROJECT_TERMS = (
    "преподавал",
    "ментор",
    "ментори",
    "обучал",
    "обучен",
    "проводил курс",
    "проводил лекц",
    "проводил воркшоп",
    "митап",
    "meetup",
    "code review",
    "ревью кода",
    "наставник",
    "наставнич",
    "куратор",
    "выступал",
    "выступлен",
    "доклад",
    "speaker",
    "speech",
    "документировал",
    "документац",
    "tech-talk",
    "tech talk",
    "интервьюер",
    "проводил собес",
    "адаптац",
    "онбординг",
    "обучающ",
    "обучил",
)

_LEAD_VACANCY_TERMS = (
    "тимлид",
    "тим-лид",
    "teamlead",
    "team lead",
    "архитектор",
    "architect",
    "senior+",
    "principal",
    "head of",
    "руководител",
    "ведущ",
    "staff engineer",
)

_LEAD_PROJECT_TERMS = (
    "архитектур",
    "ревью кода",
    "code review",
    "наставн",
    "ментор",
    "руководи",
    "вёл команду",
    "вел команду",
    "лидировал",
    "распределял задач",
    "процесс",
    "decomposition",
    "decompos",
    "архитектурн",
)


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _score_project_for_vacancy(vacancy: Vacancy, project: object) -> int:
    vacancy_text = _vacancy_text(vacancy)
    project_text = _project_text(project)

    score = 0

    for term in [
        "flutter",
        "dart",
        "bloc",
        "cubit",
        "clean architecture",
        "dio",
        "rest",
        "grpc",
        "firebase",
        "sentry",
        "sqlite",
        "drift",
        "secure storage",
        "jwt",
        "deep links",
        "branch sdk",
        "auto route",
        "webview",
        "yandex mapkit",
    ]:
        if term in vacancy_text and term in project_text:
            score += 3

    b2b_vacancy = any(
        x in vacancy_text
        for x in [
            "full stack",
            "fullstack",
            "api",
            "backend",
            "бэкенд",
            "сервер",
            "интеграц",
            "grpc",
            "rest",
            "b2b",
            "бизнес-логик",
            "корпоратив",
            "личный кабинет",
            "база данных",
        ]
    )
    b2b_project = any(
        x in project_text
        for x in [
            "b2b",
            "честный знак",
            "маркировк",
            "grpc",
            "rest",
            "бизнес-формат",
            "корпоратив",
            "api",
        ]
    )
    if b2b_vacancy and b2b_project:
        score += 12

    media_vacancy = any(
        x in vacancy_text
        for x in [
            "видео",
            "video",
            "drm",
            "плеер",
            "player",
            "offline",
            "офлайн",
            "контент",
            "подписк",
            "медиа",
        ]
    )
    media_project = any(
        x in project_text
        for x in [
            "социальн",
            "пост",
            "канал",
            "подписк",
            "медиа",
            "контент",
            "offline",
            "офлайн",
            "retry",
        ]
    )
    if media_vacancy and media_project:
        score += 12

    food_vacancy = any(
        x in vacancy_text
        for x in [
            "доставк",
            "еда",
            "food",
            "заказ",
            "корзин",
            "самовывоз",
            "бонус",
            "cashback",
        ]
    )
    food_project = any(
        x in project_text
        for x in [
            "доставк",
            "еда",
            "food",
            "заказ",
            "корзин",
            "самовывоз",
            "бонус",
            "cashback",
        ]
    )
    if food_vacancy and food_project:
        score += 12

    auth_vacancy = any(
        x in vacancy_text
        for x in [
            "авторизац",
            "auth",
            "profile",
            "профил",
            "otp",
            "роль",
            "роли",
            "права",
            "vk id",
            "yandex id",
        ]
    )
    auth_project = any(
        x in project_text
        for x in [
            "авторизац",
            "auth",
            "profile",
            "профил",
            "otp",
            "роль",
            "роли",
            "права",
            "vk id",
            "yandex id",
        ]
    )
    if auth_vacancy and auth_project:
        score += 8

    # desktop / kiosk / embedded
    desktop_vacancy = any(
        x in vacancy_text
        for x in ["desktop", "windows", "macos", "linux", "kiosk", "терминал", "embedded", "windows ce"]
    )
    desktop_project = any(
        x in project_text
        for x in ["desktop", "windows", "kiosk", "терминал", "embedded"]
    )
    if desktop_vacancy and desktop_project:
        score += 12

    # realestate / proptech
    realestate_vacancy = any(
        x in vacancy_text
        for x in ["недвижим", "девелопер", "квартир", "жил", "ипотек", "застройщик", "proptech"]
    )
    realestate_project = any(
        x in project_text
        for x in ["недвижим", "квартир", "жил", "ипотек", "застройщик"]
    )
    if realestate_vacancy and realestate_project:
        score += 12

    # gRPC / protobuf / specific tech distinguishers
    grpc_vacancy = any(x in vacancy_text for x in ["grpc", "protobuf", "protoc"])
    grpc_project = any(x in project_text for x in ["grpc", "protobuf", "protoc"])
    if grpc_vacancy and grpc_project:
        score += 10

    # === ROLE-AWARE SCORING ===
    # Teaching / speaker / mentor / advocate vacancies need teaching-flavoured projects.
    teaching_vacancy = _has_any(vacancy_text, _TEACHING_VACANCY_TERMS)
    if teaching_vacancy:
        teaching_project = _has_any(project_text, _TEACHING_PROJECT_TERMS)
        if teaching_project:
            score += 14
        else:
            # Pure-dev project with zero teaching signal is a bad fit for teaching role.
            score -= 8

    # Team lead / architect vacancies need leadership-flavoured projects.
    lead_vacancy = _has_any(vacancy_text, _LEAD_VACANCY_TERMS)
    if lead_vacancy:
        lead_project = _has_any(project_text, _LEAD_PROJECT_TERMS)
        if lead_project:
            score += 10
        else:
            score -= 4

    generic_flutter_vacancy = (
        "flutter" in vacancy_text
        and not b2b_vacancy
        and not media_vacancy
        and not food_vacancy
        and not auth_vacancy
        and not desktop_vacancy
        and not realestate_vacancy
        and not teaching_vacancy
        and not lead_vacancy
    )
    if generic_flutter_vacancy and b2b_project:
        score -= 6

    social_project = any(
        x in project_text
        for x in ["социальн", "сообществ", "канал", "пост", "подписк"]
    )
    if social_project and not media_vacancy and not auth_vacancy and not teaching_vacancy:
        score -= 5

    return score


def _build_project_ranking_hint(vacancy: Vacancy, facts: CanonicalFacts) -> str:
    scored: list[tuple[int, str, str, str]] = []

    for project_name, project in facts.projects.items():
        score = _score_project_for_vacancy(vacancy, project)
        industry = _safe_text(_get_field(project, "industry"))
        description = _safe_text(_get_field(project, "description"))

        scored.append((score, project_name, industry, description))

    scored.sort(reverse=True, key=lambda item: item[0])

    lines = []
    for score, name, industry, description in scored[:5]:
        lines.append(
            f"- {name}: score={score}; industry={industry}; description={description}"
        )

    return "\n".join(lines)


def _normalize_number(value: object) -> str:
    text = str(value).lower().replace(",", ".")
    text = re.sub(r"\s+", "", text)
    return text


def _extract_numbers(text: str) -> set[str]:
    raw_numbers = re.findall(
        r"\d+(?:[.,]\d+)?\s*(?:%|\+|млн|тыс|тысяч|месяц|месяцев|год|года|лет)?",
        text.lower(),
    )

    return {
        _normalize_number(number)
        for number in raw_numbers
        if number.strip()
    }


def _project_has_number(project: object, number: object) -> bool:
    normalized = _normalize_number(number)
    project_numbers = _extract_numbers(_project_text(project))

    return normalized in project_numbers


def _project_achievements(project: object) -> list[str]:
    achievements = _get_field(project, "achievements", []) or []

    if isinstance(achievements, list):
        return [str(item) for item in achievements if str(item).strip()]

    if isinstance(achievements, str):
        return [achievements]

    return []


def _sanitize_analyzer_result(
    result: Dict[str, Any],
    vacancy: Vacancy,
    facts: CanonicalFacts,
) -> Dict[str, Any]:
    from .project_selector import get_default_selector

    selector = get_default_selector()
    llm_selected = str(result.get("selected_project") or "") or None

    chosen, reason = selector.select(vacancy, facts, llm_selected)
    result["selected_project"] = chosen
    if reason:
        result["confidence_reason"] = reason

    project = facts.projects.get(chosen)

    if project is None:
        logger.warning(
            "Sanitizer: selector returned project '%s' but it is not in facts.projects",
            chosen,
        )
        return result

    clean_numbers = []
    for number in result.get("selected_numbers") or []:
        if _project_has_number(project, number):
            clean_numbers.append(number)

    result["selected_numbers"] = clean_numbers

    project_text = _project_text(project)

    clean_achievements = []
    for achievement in result.get("selected_achievements") or []:
        achievement_text = str(achievement).strip()

        if not achievement_text:
            continue

        words = [
            word
            for word in re.findall(r"[a-zа-яё0-9]+", achievement_text.lower())
            if len(word) >= 4
        ]

        hits = sum(1 for word in words if word in project_text)

        if hits >= 3:
            clean_achievements.append(achievement_text)

    if clean_achievements:
        result["selected_achievements"] = clean_achievements
    else:
        result["selected_achievements"] = _project_achievements(project)[:4]

    return result


def _strip_json_markdown(text: str) -> str:
    cleaned = text.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    return cleaned.strip()


async def analyze(
    llm: LLMClient,
    vacancy: Vacancy,
    facts: CanonicalFacts,
) -> Dict[str, Any]:
    project_ranking_hint = _build_project_ranking_hint(vacancy, facts)

    user_prompt = build_analyzer_user(
        vacancy=vacancy,
        facts=facts,
        project_ranking_hint=project_ranking_hint,
    )

    response = await llm.generate(
        system_prompt=ANALYZER_SYSTEM,
        user_prompt=user_prompt,
        temperature=0.2,
        max_tokens=1500,
        json_mode=True,
    )

    try:
        parsed = json.loads(_strip_json_markdown(response))
    except json.JSONDecodeError as exc:
        logger.error("Analyzer returned invalid JSON: %s", response)
        raise RuntimeError(f"Analyzer failed: invalid JSON: {exc}") from exc

    parsed = _sanitize_analyzer_result(parsed, vacancy, facts)

    if not isinstance(parsed.get("selected_project"), str):
        raise RuntimeError("Analyzer failed: missing or invalid selected_project")

    if not isinstance(parsed.get("selected_achievements"), list):
        raise RuntimeError("Analyzer failed: missing or invalid selected_achievements")

    if not isinstance(parsed.get("selected_numbers"), list):
        raise RuntimeError("Analyzer failed: missing or invalid selected_numbers")

    if not isinstance(parsed.get("hook_phrase"), str) or not parsed.get("hook_phrase"):
        if vacancy.requirements:
            parsed["hook_phrase"] = vacancy.requirements[0][:120]
        else:
            parsed["hook_phrase"] = vacancy.title

    if not isinstance(parsed.get("confidence"), (int, float)):
        parsed["confidence"] = 0.5

    if not isinstance(parsed.get("confidence_reason"), str):
        parsed["confidence_reason"] = "Причина выбора проекта не указана."

    # P5: role-mismatch guard. If the vacancy is clearly a teaching / speaker /
    # mentor / advocate role but NONE of the candidate's projects carry any
    # teaching signal, the analyzer's project pick is a forced fit. Flag it via
    # role_mismatch=True so the pipeline can skip generation instead of emitting
    # a letter that claims teaching relevance the resume can't support, and
    # hard-cap confidence so downstream low-confidence handling also triggers.
    vacancy_is_teaching = _has_any(_vacancy_text(vacancy), _TEACHING_VACANCY_TERMS)
    if vacancy_is_teaching:
        any_teaching_project = any(
            _has_any(_project_text(project), _TEACHING_PROJECT_TERMS)
            for project in facts.projects.values()
        )
        if not any_teaching_project:
            parsed["role_mismatch"] = True
            try:
                current_conf = float(parsed.get("confidence", 0.5))
            except (TypeError, ValueError):
                current_conf = 0.5
            parsed["confidence"] = min(current_conf, 0.15)
            logger.info(
                "P5 role-mismatch: teaching/speaker vacancy but no teaching-"
                "flavoured project in resume; capped confidence to %.2f",
                parsed["confidence"],
            )

    return parsed
