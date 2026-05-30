from __future__ import annotations

import asyncio
import random
from asyncio import Lock
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from vacancy_agent.config import settings
from vacancy_agent.logger import log


class BrowserManager:
    """Playwright browser manager.

    Supports two modes:
    1️⃣ **Local mode** – launches a fresh Chromium instance.
    2️⃣ **CDP mode** – connects to an existing Chrome/Edge via a CDP endpoint
       (e.g. ``ws://127.0.0.1:9222/devtools/browser/<id>``).

    The manager now keeps a **single reusable ``Page``** (``_shared_page``).
    All navigation in the vacancy‑agent uses this page, so the UI shows only
    one tab – we simply change its URL when we need to open a new vacancy.
    """

    def __init__(self) -> None:
        # Playwright objects
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None

        # Shared page (created lazily)
        self._shared_page: Optional[Page] = None

        # CDP configuration
        self.cdp_url: Optional[str] = settings.browser_cdp_url

        # Internal state
        self._lock: Lock = Lock()
        self._cdp_mode: bool = False
        self._owns_context: bool = False

    def configure(self, cdp_url: Optional[str] = None) -> None:
        """Configure browser connection before start()."""
        if cdp_url:
            self.cdp_url = cdp_url

    async def start(self) -> None:
        async with self._lock:
            if self.browser and self.context:
                return

            self.playwright = await async_playwright().start()

            if self.cdp_url:
                await self._connect_over_cdp(self.cdp_url)
                return

            await self._launch_local_browser()

    async def _launch_local_browser(self) -> None:
        assert self.playwright is not None

        self._cdp_mode = False
        self._owns_context = True

        self.browser = await self.playwright.chromium.launch(
            headless=settings.browser_headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        self.context = await self.browser.new_context(
            viewport={"width": 1440, "height": 1000},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        await self.context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        log.info("Browser started in local mode")

    def _normalize_cdp_url(self, cdp_url: str) -> str:
        # Allow passing either:
        # - http://127.0.0.1:9222
        # - ws://127.0.0.1:9222/devtools/browser/<id>
        # Playwright's connect_over_cdp expects an HTTP endpoint.
        parsed = urlparse(cdp_url)
        if parsed.scheme in {"ws", "wss"}:
            scheme = "https" if parsed.scheme == "wss" else "http"
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 9222
            return f"{scheme}://{host}:{port}"
        return cdp_url

    async def _connect_over_cdp(self, cdp_url: str) -> None:
        assert self.playwright is not None

        self._cdp_mode = True

        cdp_url = self._normalize_cdp_url(cdp_url)
        self.browser = await self.playwright.chromium.connect_over_cdp(cdp_url)

        if self.browser.contexts:
            self.context = self.browser.contexts[0]
            self._owns_context = False
            log.info(f"Connected to existing browser context over CDP: {cdp_url}")
        else:
            self.context = await self.browser.new_context(
                viewport={"width": 1440, "height": 1000},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            self._owns_context = True
            log.info(f"Connected over CDP and created new context: {cdp_url}")

    async def close(self) -> None:
        async with self._lock:
            try:
                if self._shared_page:
                    try:
                        await self._shared_page.close()
                    except Exception:
                        pass

                if self.context and self._owns_context:
                    try:
                        await self.context.close()
                    except Exception:
                        pass

                if self.browser and not self._cdp_mode:
                    try:
                        await self.browser.close()
                    except Exception:
                        pass

                if self.playwright:
                    try:
                        await self.playwright.stop()
                    except Exception:
                        pass

            finally:
                self._shared_page = None
                self.context = None
                self.browser = None
                self.playwright = None
                self._cdp_mode = False
                self._owns_context = False

                log.info("Browser manager closed")

    @asynccontextmanager
    async def new_page(self) -> AsyncIterator[Page]:
        """Legacy helper – creates a temporary page.
        New code should use :meth:`get_shared_page` to avoid spawning many tabs.
        """
        if not self.context:
            await self.start()

        assert self.context is not None
        page = await self.context.new_page()
        page.set_default_timeout(settings.browser_timeout_ms)

        try:
            yield page
        finally:
            await page.close()

    async def get_shared_page(self) -> Page:
        """Return (and lazily create) a single reusable page.
        The page is kept for the whole CLI run and is closed in ``close()``.
        """
        if not self.context:
            await self.start()
        if self._shared_page is None:
            self._shared_page = await self.context.new_page()
            self._shared_page.set_default_timeout(settings.browser_timeout_ms)
        return self._shared_page

    async def get_page_content(self, url: str, wait_until: str = "domcontentloaded") -> str:
        """Navigate the shared page to ``url`` and return its HTML.
        This method now re‑uses the single tab, preventing UI clutter.
        """
        page = await self.get_shared_page()
        await page.goto(url, wait_until=wait_until, timeout=settings.browser_timeout_ms)
        await self.random_delay()
        return await page.content()

    async def random_delay(self) -> None:
        await asyncio.sleep(random.uniform(settings.browser_delay_min, settings.browser_delay_max))


browser_manager = BrowserManager()
