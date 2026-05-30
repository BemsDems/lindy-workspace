from __future__ import annotations

from playwright.async_api import Page

from vacancy_agent.apply_adapters.base import ApplyResult
from vacancy_agent.apply_adapters.registry import get_apply_adapter, unsupported_result
from vacancy_agent.browser import browser_manager
from vacancy_agent.schemas import Vacancy


class ApplyService:
    """Platform-neutral application service.

    The service chooses a platform adapter by vacancy URL and runs the apply
    flow. New job boards should be added by implementing an adapter and
    registering it in `vacancy_agent.apply_adapters.registry`.
    """

    async def apply(
        self,
        vacancy: Vacancy,
        cover_letter: str,
        *,
        dry_run: bool = True,
        page: Page | None = None,
    ) -> ApplyResult:
        adapter = get_apply_adapter(vacancy)
        if not adapter:
            return unsupported_result(vacancy)

        if page is not None:
            return await adapter.apply(page, vacancy, cover_letter, dry_run=dry_run)

        shared_page = await browser_manager.get_shared_page()
        return await adapter.apply(shared_page, vacancy, cover_letter, dry_run=dry_run)


apply_service = ApplyService()
