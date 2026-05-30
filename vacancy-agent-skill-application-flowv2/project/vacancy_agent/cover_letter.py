from __future__ import annotations

from jinja2 import Template

from vacancy_agent.config import COVER_LETTER_TEMPLATE_FILE
from vacancy_agent.schemas import CandidateProfile, CoverLetterRequest, CoverLetterResponse, Vacancy
from vacancy_agent.utils.text import normalize_space


DEFAULT_TEMPLATE = '''Здравствуйте!

Меня зовут {{ candidate.name }}, я рассматриваю позицию {{ candidate.position }}.

Меня заинтересовала вакансия "{{ vacancy.title }}" в компании {{ vacancy.company }}.

{% if matched_requirements %}
С моим опытом хорошо совпадают следующие требования вакансии:
{% for req in matched_requirements %}
- {{ req }}
{% endfor %}
{% endif %}

{% if missing_requirements %}
По отдельным требованиям, которые не указаны в моём профиле, я готов дополнительно разобраться и уточнить детали на собеседовании.
{% endif %}

{{ candidate.experience_summary }}

Буду рад обсудить, чем могу быть полезен вашей команде.

С уважением,
{{ candidate.name }}
{% if candidate.contact_lines %}
{% for key, value in candidate.contact_lines.items() %}{{ key }}: {{ value }}
{% endfor %}
{% endif %}
'''


COMMON_REQUIREMENTS = [
    "Dart",
    "Flutter",
    "BLoC",
    "Cubit",
    "Clean Architecture",
    "MVVM",
    "MVC",
    "REST",
    "REST API",
    "gRPC",
    "Protobuf",
    "Firebase",
    "FCM",
    "JWT",
    "Secure Storage",
    "WebView",
    "Freezed",
    "Auto Route",
    "Deep Links",
    "SQLite",
    "Drift",
    "Sentry",
    "Dio",
    "GetIt",
    "Injectable",
    "Git",
    "GitHub",
    "GitLab",
    "Jira",
    "Figma",
    "Agile",
    "CI/CD",
    "SOLID",
    "DRY",
    "ООП",
    # Keep backend/common terms too, so other profiles still work.
    "Python",
    "Django",
    "FastAPI",
    "Flask",
    "PostgreSQL",
    "MySQL",
    "Redis",
    "Docker",
    "Kubernetes",
    "Linux",
    "GraphQL",
    "Celery",
    "RabbitMQ",
    "Kafka",
    "SQLAlchemy",
    "Pytest",
    "JavaScript",
    "TypeScript",
    "React",
    "Vue",
    "AWS",
]


def ensure_default_template() -> None:
    if not COVER_LETTER_TEMPLATE_FILE.exists():
        COVER_LETTER_TEMPLATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        COVER_LETTER_TEMPLATE_FILE.write_text(DEFAULT_TEMPLATE, encoding="utf-8")


def load_template() -> str:
    ensure_default_template()
    return COVER_LETTER_TEMPLATE_FILE.read_text(encoding="utf-8")


def generate_cover_letter(request: CoverLetterRequest) -> CoverLetterResponse:
    vacancy = request.vacancy
    candidate = request.candidate

    matched, missing = analyze_match(vacancy, candidate)
    template = Template(request.template)
    cover_letter = template.render(
        vacancy=vacancy,
        candidate=candidate,
        matched_requirements=matched,
        missing_requirements=missing,
    ).strip()

    risk_notes: list[str] = []
    if missing:
        risk_notes.append("Есть требования, которых нет в профиле кандидата. Перед отправкой проверь формулировки.")

    for restricted in candidate.restrictions:
        if restricted and restricted.lower() in cover_letter.lower():
            risk_notes.append(f"В письме встречается запрещённое упоминание: {restricted}")

    return CoverLetterResponse(
        cover_letter=cover_letter,
        matched_requirements=matched,
        missing_requirements=missing,
        risk_notes=risk_notes,
    )


def analyze_match(vacancy: Vacancy, candidate: CandidateProfile) -> tuple[list[str], list[str]]:
    vacancy_text = " ".join(
        [
            vacancy.title or "",
            vacancy.description or "",
            " ".join(vacancy.requirements),
            " ".join(vacancy.responsibilities),
            " ".join(vacancy.tags),
        ]
    ).lower()

    profile_items = candidate.skills_flat
    profile_text = " ".join(profile_items + candidate.project_summaries).lower()

    found_requirements = []
    for item in COMMON_REQUIREMENTS:
        if item.lower() in vacancy_text:
            found_requirements.append(item)

    # Include extracted lines from vacancy requirements if they mention candidate skills.
    for req in vacancy.requirements:
        req_norm = normalize_space(req)
        if req_norm and any(skill.lower() in req_norm.lower() for skill in profile_items):
            found_requirements.append(req_norm)

    matched = []
    missing = []

    for req in found_requirements:
        if any(skill.lower() in req.lower() or req.lower() in skill.lower() for skill in profile_items):
            matched.append(req)
        elif req.lower() in profile_text:
            matched.append(req)
        else:
            missing.append(req)

    # Deduplicate while preserving order.
    matched = list(dict.fromkeys(matched))
    missing = list(dict.fromkeys(missing))

    return matched[:10], missing[:10]


def build_cover_letter(vacancy: Vacancy, candidate: CandidateProfile) -> CoverLetterResponse:
    template = load_template()
    return generate_cover_letter(CoverLetterRequest(vacancy=vacancy, candidate=candidate, template=template))
