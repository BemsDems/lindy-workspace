from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from vacancy_agent.logger import log
from vacancy_agent.schemas import SearchParams

from .base import QueryAdapter, QueryRouteResult


_HH_SEARCH_PATH_RE = re.compile(r"^/search/vacancy(?:/.*)?$")


def _is_hh_domain(netloc: str) -> bool:
    host = (netloc or "").lower()
    return host.endswith("hh.ru") or host.endswith("hh.kz")


def _is_hh_search_url(url: str) -> bool:
    p = urlparse(url)
    return _is_hh_domain(p.netloc) and bool(_HH_SEARCH_PATH_RE.match(p.path or ""))


def _rewrite_hh_search_url(url: str, query_text: str) -> str:
    """Rewrite HH search URL to use `text=<query_text>` and preserve area restriction."""
    p = urlparse(url)
    q = dict(parse_qsl(p.query))
    q["text"] = query_text

    # Важно: area не удаляем.
    # Если источник был area=1 / area=16 / area=113, фильтр должен сохраниться.
    return urlunparse(p._replace(query=urlencode(q)))


@dataclass
class HHQueryAdapter(QueryAdapter):
    """HH.ru / HH.kz query routing.

    Strategy: URL-first, UI-fallback.
    """

    def can_handle(self, url: str) -> bool:
        try:
            return _is_hh_domain(urlparse(url).netloc)
        except Exception:
            return False

    async def route(self, page, base_url: str, params: SearchParams) -> QueryRouteResult:
        # Optional HH account action: if user opted-in, and we're on the HH start page,
        # click "Поднять" (resume boost) when available.
        if params.allow_hh_actions:
            try:
                from urllib.parse import urlparse

                p0 = urlparse(base_url)
                if (p0.netloc or "").lower() in ("hh.ru", "www.hh.ru") and (p0.path or "") in ("", "/"):
                    await page.goto(base_url, wait_until="domcontentloaded")
                    # HH: try to find a clickable element that has an *id* and contains text "Поднять".
                    # On some layouts HH may not provide an id at all; in that case we fall back.
                    # NOTE: In the HH markup we've observed, the boost card often has NO `id` attribute.
                    # Instead, it contains a stable data-qa marker:
                    #   data-qa="applicant-index-nba-action_update-resumes"
                    # So we prefer data-qa first, then fall back to role/text.

                    qa = page.locator('[data-qa="applicant-index-nba-action_update-resumes"]').first
                    btn_by_qa = qa.locator('xpath=ancestor-or-self::*[@role="button"][1]').first

                    leaf = page.locator('text=/Поднять/i').first
                    btn_fallback = leaf.locator('xpath=ancestor-or-self::*[@role="button"][1]').first

                    btn = btn_by_qa
                    if await btn_by_qa.count() == 0:
                        btn = btn_fallback

                    if await btn.is_visible() and await btn.is_enabled():
                        await btn.click()

                        # Determine whether the click actually did something by waiting for the toast.
                        # HH uses role=alert for the notification.
                        try:
                            await page.wait_for_selector('[role="alert"]', timeout=4000)
                        except Exception:
                            pass

                        alerts = page.locator('[role="alert"]')
                        if await alerts.count() > 0:
                            msg = (await alerts.first.inner_text()).strip()
                            if msg:
                                log.info(f"[hh] Boost status: {msg}")
                        else:
                            log.info("[hh] Clicked 'Поднять' (no alert captured)")
                    else:
                        log.info("[hh] 'Поднять' not clickable (hidden/disabled)")
            except Exception as exc:
                # Never fail the scrape due to the optional action.
                log.info(f"[hh] Skip optional action 'Поднять': {exc}")

        if not params.query:
            # no query: just open base_url
            await page.goto(base_url, wait_until="domcontentloaded")
            return QueryRouteResult(final_url=page.url)

        # 1) URL-first for existing search URLs
        if _is_hh_search_url(base_url):
            target = _rewrite_hh_search_url(base_url, params.query)
            await page.goto(target, wait_until="domcontentloaded")
            return QueryRouteResult(final_url=page.url)

        # 2) UI-fallback from home/landing
        await page.goto(base_url, wait_until="domcontentloaded")

        # Guardrail: keep this flow read-only.
        # Do NOT click UI elements like "Поднять" (resume boost) or other applicant actions.
        # We only type into the vacancy search input and submit via Enter.
        try:
            await page.wait_for_selector("input[data-qa='search-input']", timeout=5000)
            await page.fill("input[data-qa='search-input']", params.query)
            await page.keyboard.press("Enter")
            # HH may navigate but not always reach a full 'load' event; wait for URL to change.
            await page.wait_for_timeout(1500)
        except Exception as exc:
            # Continue with whatever we have; link guardrails will prevent unsafe navigation.
            log.error(f"[hh] UI query routing failed: {exc}")
            return QueryRouteResult(final_url=page.url)

        # After UI search we should be on /search/vacancy
        if _is_hh_search_url(page.url):
            rewritten = _rewrite_hh_search_url(page.url, params.query)
            if rewritten != page.url:
                await page.goto(rewritten, wait_until="domcontentloaded")

        return QueryRouteResult(final_url=page.url)
