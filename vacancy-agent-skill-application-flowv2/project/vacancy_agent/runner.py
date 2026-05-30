from __future__ import annotations

from vacancy_agent.browser import browser_manager
from vacancy_agent.config import settings
from vacancy_agent.dedupe import remove_duplicates
from vacancy_agent.logger import log
from vacancy_agent.schemas import SearchParams, Vacancy, VacancySource
from vacancy_agent.sources.factory import create_source
from vacancy_agent.storage import storage
from vacancy_agent.utils.ids import make_id


class VacancyRunner:
    async def search_urls(self, urls: list[str], params: SearchParams) -> list[Vacancy]:
        sources = [
            VacancySource(
                id=make_id(url, length=10),
                name=f"url:{index + 1}",
                url=url,
                enabled=True,
            )
            for index, url in enumerate(urls)
        ]
        return await self.search_sources(sources, params)

    async def search_sources(self, sources: list[VacancySource], params: SearchParams) -> list[Vacancy]:
        collected: list[Vacancy] = []

        try:
            await browser_manager.start()

            for source in sources:
                if not source.enabled:
                    log.info(f"Skipping disabled source: {source.name}")
                    continue

                try:
                    log.info(f"Processing source: {source.name}")
                    scraper = create_source(source)
                    vacancies = await scraper.search(params)
                    collected.extend(vacancies)
                except Exception as exc:
                    log.error(f"Source failed {source.name}: {exc}")
                    continue

            unique = remove_duplicates(collected)
            merged = storage.merge_vacancies(unique)

            log.info(f"Search finished: collected={len(collected)}, unique={len(unique)}, saved_total={len(merged)}")
            return unique

        finally:
            await browser_manager.close()


runner = VacancyRunner()
