from vacancy_agent.schemas import SourceType, VacancySource
from vacancy_agent.sources.base import BaseSource
from vacancy_agent.sources.generic import GenericPlaywrightSource


def create_source(source: VacancySource) -> BaseSource:
    if source.type == SourceType.PLAYWRIGHT:
        return GenericPlaywrightSource(source)
    if source.type == SourceType.REQUESTS:
        # Для MVP requests-source можно добавить следующим шагом.
        return GenericPlaywrightSource(source)
    raise ValueError(f"Unsupported source type: {source.type}")
