from __future__ import annotations

import json
from typing import Any


ANALYZER_SYSTEM = """\
Ты аналитик сопроводительных писем для Flutter-разработчика.
Твоя задача — выбрать 1 самый релевантный проект из резюме и 3–5 его достижений,
которые максимально подходят под вакансию.

ВХОД
- Вакансия: title, company, description, requirements.
- Кандидат: summary, projects, achievements, tech_stack.
- PROJECT_RANKING_HINTS: детерминированная подсказка по релевантности проектов.

ВЫХОД СТРОГО В JSON
{
"selected_project": "название проекта из резюме",
"selected_achievements": ["достижение 1", "достижение 2"],
"selected_numbers": ["число 1", "число 2"],
"hook_phrase": "одна короткая фраза (5–12 слов) на русском, которая формулирует САМОЕ СИЛЬНОЕ требование вакансии — то, на что кандидат должен прямо ответить в первом абзаце письма. Это не цитата из вакансии, а её парафраз в форме «нужен X с опытом Y».",
"vacancy_domain": "одно или два слова, описывающие домен/индустрию вакансии. Допустимы русские и английские термины. Примеры: 'EdTech', 'финтех', 'медицина', 'логистика', 'B2B SaaS', 'игры', 'медиа', 'e-commerce', 'кибербезопасность', 'видео и DRM', 'еда и доставка'. Если домен явно не указан в вакансии — 'общий продукт'.",
"confidence": 0.0,
"confidence_reason": "почему выбран этот проект"
}

ПРАВИЛА ВЫБОРА ПРОЕКТА
1. Выбирай проект по совпадению с вакансией, а не по самым сильным цифрам.
2. Учитывай домен вакансии, технологии, обязанности и требования.
3. Если вакансия содержит Full Stack, API, backend, серверную логику, B2B, корпоративный домен, интеграции, gRPC, REST или сложную бизнес-логику, выбирай проект с gRPC/REST, Clean Architecture, DI, B2B-модулями и интеграциями.
4. Если вакансия обычная Flutter/mobile без явного B2B/API/backend-фокуса, приоритетнее мобильный продуктовый проект с авторизацией, навигацией, релизом, Deep Links, UI и пользовательскими сценариями.
5. Если вакансия про видео, DRM, offline mode, медиаконтент или подписки, выбирай проект с медиа, подписками, каналами, контентом или офлайн-сценариями.
6. Если вакансия про роли, права, авторизацию, профили, внешних провайдеров входа или OTP, выбирай проект с auth/profile/roles.
7. Если вакансия про доставку, еду, корзину, заказ, самовывоз, бонусы или cashback, выбирай проект сервиса доставки еды.
8. Не выбирай B2B/ERP/маркировку только потому, что там сильные метрики.
9. Не выбирай проект социальной сети только из-за Flutter, если вакансия не связана с соцсетями, контентом, ролями, профилями, подписками или медиа.
10. selected_numbers должны относиться только к selected_project.
11. selected_achievements должны относиться только к selected_project.
12. Нельзя смешивать достижения из разных проектов.
13. Если PROJECT_RANKING_HINTS показывает большой отрыв по score, учитывай это как сильный сигнал.
14. Если не уверен, выбирай проект с наиболее близкими задачами, а не проект с самыми красивыми цифрами.
15. Если вакансия содержит слова «преподаватель», «спикер», «ментор», «тренер», «лектор», «evangelist», «advocate», «обучение», «курс», «EdTech», «митап», «воркшоп», «наставник» или «куратор» — выбирай проект, в достижениях которого есть менторинг, code review, обучение команды, проведение митапов, наставничество или публичные выступления. В selected_achievements включай именно эти достижения, а НЕ метрики DAU/MAU, не технические фичи и не бизнес-показатели. Если таких достижений нет ни в одном проекте — выбирай проект с наибольшим количеством косвенных признаков (документирование, онбординг, интервьюирование) и ставь confidence не выше 0.5.

ВЫБОР HOOK_PHRASE
- Выбери одну фразу из вакансии, которая требует подтверждения опыта.
- Не выдумывай hook_phrase.
- Если явной фразы нет, сформулируй коротко основной фокус вакансии в форме «нужен X с опытом Y».
- Для teaching/mentor/speaker вакансий hook_phrase должна отражать роль (например: «нужен Flutter-разработчик с опытом менторинга и публичных выступлений»), а не технический стек.

ВЫБОР VACANCY_DOMAIN
- Извлеки 1–2 слова, описывающие индустрию или продуктовую область вакансии.
- Используй конкретный домен (например, 'EdTech', 'финтех', 'медицина', 'логистика', 'B2B SaaS', 'игры', 'медиа', 'e-commerce', 'кибербезопасность', 'видео и DRM', 'еда и доставка', 'HR-tech', 'PropTech', 'путешествия', 'госсектор', 'спорт', 'недвижимость').
- Если в вакансии явно указана отрасль компании или продукта — бери её.
- Если вакансия generic Flutter/mobile без явного домена — ставь 'общий продукт'.
- Не выдумывай домен; если сомневаешься между двумя — пиши оба через '/' (например, 'EdTech/обучение').

ВАЖНО
- Верни только JSON.
- Без markdown.
- Без комментариев.
"""


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


def project_to_dict(project_name: str, project: object) -> dict[str, Any]:
    return {
        "name": project_name,
        "company": _get_field(project, "company"),
        "industry": _get_field(project, "industry"),
        "description": _get_field(project, "description"),
        "tech_stack": _get_field(project, "tech_stack"),
        "achievements": _get_field(project, "achievements"),
    }


def vacancy_to_dict(vacancy: object) -> dict[str, Any]:
    return {
        "id": _get_field(vacancy, "id"),
        "title": _get_field(vacancy, "title"),
        "company": _get_field(vacancy, "company"),
        "url": _get_field(vacancy, "url"),
        "description": _get_field(vacancy, "description"),
        "requirements": _get_field(vacancy, "requirements"),
    }


def canonical_facts_to_dict(facts: object) -> dict[str, Any]:
    projects = _get_field(facts, "projects", {}) or {}

    if isinstance(projects, dict):
        projects_payload = [
            project_to_dict(project_name, project)
            for project_name, project in projects.items()
        ]
    else:
        projects_payload = []

    return {
        "candidate_name": _get_field(facts, "candidate_name"),
        "experience_years": _get_field(facts, "experience_years"),
        "experience_months": _get_field(facts, "experience_months"),
        "summary": _get_field(facts, "summary"),
        "allowed_numbers": list(_get_field(facts, "allowed_numbers", []) or []),
        "allowed_tech": sorted(list(_get_field(facts, "allowed_tech", []) or [])),
        "allowed_project_names": sorted(list(_get_field(facts, "allowed_project_names", []) or [])),
        "allowed_company_names": sorted(list(_get_field(facts, "allowed_company_names", []) or [])),
        "projects": projects_payload,
    }


def build_analyzer_user(
    vacancy: object,
    facts: object,
    project_ranking_hint: str | None = None,
) -> str:
    vacancy_payload = vacancy_to_dict(vacancy)
    candidate_payload = canonical_facts_to_dict(facts)

    prompt = (
        "VACANCY:\n"
        f"{json.dumps(vacancy_payload, ensure_ascii=False, indent=2)}\n\n"
        "CANDIDATE:\n"
        f"{json.dumps(candidate_payload, ensure_ascii=False, indent=2)}"
    )

    if project_ranking_hint:
        prompt += (
            "\n\n"
            "PROJECT_RANKING_HINTS:\n"
            "Ниже детерминированная подсказка по релевантности проектов.\n"
            "Это не финальное решение, но если score сильно выше, проект обычно подходит лучше.\n"
            "Не выбирай проект только из-за сильных цифр, если домен и задачи вакансии с ним не совпадают.\n\n"
            f"{project_ranking_hint}"
        )

    return prompt


def build_analyzer_user_prompt(
    vacancy: object,
    facts: object,
    project_ranking_hint: str | None = None,
) -> str:
    return build_analyzer_user(
        vacancy=vacancy,
        facts=facts,
        project_ranking_hint=project_ranking_hint,
    )


def render_canonical_facts_block(facts: object) -> str:
    """Backward-compatible helper for older imports."""
    return json.dumps(
        canonical_facts_to_dict(facts),
        ensure_ascii=False,
        indent=2,
    )


def render_vacancy_block(vacancy: object) -> str:
    """Backward-compatible helper for older imports."""
    return json.dumps(
        vacancy_to_dict(vacancy),
        ensure_ascii=False,
        indent=2,
    )


def render_project_block(project_name: str, project: object) -> str:
    """Backward-compatible helper for older imports."""
    return json.dumps(
        project_to_dict(project_name, project),
        ensure_ascii=False,
        indent=2,
    )


def render_projects_block(facts: object) -> str:
    """Backward-compatible helper for older imports."""
    data = canonical_facts_to_dict(facts)
    return json.dumps(
        data.get("projects", []),
        ensure_ascii=False,
        indent=2,
    )


__all__ = [
    "ANALYZER_SYSTEM",
    "build_analyzer_user",
    "build_analyzer_user_prompt",
    "canonical_facts_to_dict",
    "vacancy_to_dict",
    "project_to_dict",
    "render_project_block",
    "render_vacancy_block",
    "render_projects_block",
    "render_canonical_facts_block",
]
