from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from vacancy_agent.logger import log
from vacancy_agent.schemas import SearchParams

from .base import QueryAdapter, QueryRouteResult


def _is_hirify_domain(netloc: str) -> bool:
    host = (netloc or "").lower()
    return host.endswith("hirify.me")


@dataclass
class HirifyQueryAdapter(QueryAdapter):
    """hirify.me query routing via UI.

    Hirify exposes a search input with placeholder "название вакансии или компании".
    We fill it with params.query and click the "Искать" button.
    """

    def can_handle(self, url: str) -> bool:
        try:
            return _is_hirify_domain(urlparse(url).netloc)
        except Exception:
            return False

    async def route(self, page, base_url: str, params: SearchParams) -> QueryRouteResult:
        await page.goto(base_url, wait_until="domcontentloaded")
        # Give the SPA time to hydrate; otherwise button clicks may be ignored.
        await page.wait_for_timeout(3000)

        if not params.query:
            return QueryRouteResult(final_url=page.url)

        # Input: "название вакансии или компании"
        input_loc = page.locator('input.search-input[type="text"]').first
        if await input_loc.count() == 0:
            input_loc = page.locator('input[placeholder="название вакансии или компании"]').first
        if await input_loc.count() == 0:
            raise RuntimeError("[hirify] search input not found")

        await input_loc.fill(params.query)

        # Button: "Искать"
        btn_loc = page.locator("button.search-button").first
        if await btn_loc.count() == 0:
            btn_loc = page.locator("xpath=//button[normalize-space()='Искать']").first
        if await btn_loc.count() == 0:
            raise RuntimeError("[hirify] search button not found")

        await btn_loc.click()
        # Hirify navigation is client-side; wait until the query appears in location.
        try:
            await page.wait_for_function("() => window.location.search.includes('search=')", timeout=5000)
        except Exception:
            # Fallback: small delay to allow SPA routing.
            await page.wait_for_timeout(1500)

        final_url = await page.evaluate("() => window.location.href")
        log.info(f"[hirify] Routed query '{params.query}' -> {final_url}")
        return QueryRouteResult(final_url=final_url)
