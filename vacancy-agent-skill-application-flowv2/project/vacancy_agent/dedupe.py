from vacancy_agent.logger import log
from vacancy_agent.schemas import Vacancy
from vacancy_agent.utils.text import normalize_space


def vacancy_key(vacancy: Vacancy) -> tuple[str, str, str]:
    return (
        normalize_space(vacancy.title).lower(),
        normalize_space(vacancy.company).lower(),
        normalize_space(vacancy.location).lower(),
    )


def remove_duplicates(vacancies: list[Vacancy]) -> list[Vacancy]:
    seen_urls: set[str] = set()
    seen_keys: set[tuple[str, str, str]] = set()
    unique: list[Vacancy] = []

    for vacancy in vacancies:
        url = vacancy.url.strip()
        key = vacancy_key(vacancy)

        if url in seen_urls:
            continue
        if key in seen_keys:
            continue

        seen_urls.add(url)
        seen_keys.add(key)
        unique.append(vacancy)

    removed = len(vacancies) - len(unique)
    if removed:
        log.info(f"Removed duplicates: {removed}")

    return unique
