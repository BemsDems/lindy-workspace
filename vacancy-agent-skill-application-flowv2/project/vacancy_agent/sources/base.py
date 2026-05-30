from __future__ import annotations

from abc import ABC, abstractmethod

from vacancy_agent.schemas import SearchParams, Vacancy, VacancySource


class BaseSource(ABC):
    def __init__(self, source: VacancySource):
        self.source = source
        self.source_id = source.id
        self.name = source.name
        self.base_url = source.url

    @abstractmethod
    async def search(self, params: SearchParams) -> list[Vacancy]:
        pass

    @abstractmethod
    async def fetch_vacancy_urls(self, params: SearchParams) -> list[str]:
        pass

    @abstractmethod
    async def fetch_vacancy_details(self, url: str) -> Vacancy | None:
        pass
