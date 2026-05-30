from __future__ import annotations
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse, parse_qsl, urlencode, urlunparse
import re
from bs4 import BeautifulSoup
from vacancy_agent.browser import browser_manager
from playwright.async_api import Page
from vacancy_agent.config import settings
from vacancy_agent.extractors.vacancy_extractor import VacancyExtractor
from vacancy_agent.logger import log
from vacancy_agent.schemas import SearchParams, Vacancy, VacancyStatus
from vacancy_agent.sources.base import BaseSource
from vacancy_agent.query_adapters.registry import get_query_adapter
from vacancy_agent.utils.text import absolutize_url

_HH_VACANCY_PATH_RE = re.compile(r"^/vacancy/\d+(?:/.*)?$")
_HIRIFY_JOB_PATH_RE = re.compile(r"^/jobs/\d+(?:-.*)?$")

def _is_hh_domain(netloc: str) -> bool:
    host = (netloc or "").lower()
    return host.endswith("hh.ru") or host.endswith("hh.kz")

def _is_hirify_domain(netloc: str) -> bool:
    host = (netloc or "").lower()
    return host.endswith("hirify.me")

def _is_hirify_job_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return _is_hirify_domain(p.netloc) and bool(_HIRIFY_JOB_PATH_RE.match(p.path or ""))
    except Exception:
        return False

def _is_hh_vacancy_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return _is_hh_domain(p.netloc) and bool(_HH_VACANCY_PATH_RE.match(p.path or ""))
    except Exception:
        return False

def _looks_like_hh_apply_or_employer(url: str) -> bool:
    """Guardrail: never open HH apply/employer pages during scraping."""
    try:
        p = urlparse(url)
        if not _is_hh_domain(p.netloc):
            return False
        path = (p.path or "").lower()
        if path.startswith("/employer/"):
            return True
        if path.startswith("/applicant/"):
            return True
        # Some apply URLs may include this substring.
        if "vacancy_response" in (p.query or ""):
            return True
        return False
    except Exception:
        return False

class GenericPlaywrightSource(BaseSource):
    """Generic source that uses Playwright to scrape vacancies."""

    def __init__(self, source: VacancySource):
        super().__init__(source)
        self.base_url = source.url
        self._serp_card_meta_by_url: dict[str, dict[str, str]] = {}
        self._serp_card_meta_by_url: dict[str, dict[str, str]] = {}

    async def _extract_card_text(self, card, selector: str) -> str | None:
        try:
            locator = card.locator(selector)
            if await locator.count() < 1:
                return None

            text = await locator.first.inner_text(timeout=1000)
            text = re.sub(r"\s+", " ", text or "").strip()
            return text or None
        except Exception:
            return None

    async def _extract_card_texts(self, card, selector: str) -> list[str]:
        try:
            locator = card.locator(selector)
            count = await locator.count()

            values: list[str] = []
            for index in range(count):
                text = await locator.nth(index).inner_text(timeout=1000)
                text = re.sub(r"\s+", " ", text or "").strip()
                if text and text not in values:
                    values.append(text)

            return values
        except Exception:
            return []

    async def _extract_hh_serp_location(self, card) -> str | None:
        address = await self._extract_card_text(
            card,
            "[data-qa='vacancy-serp__vacancy-address']",
        )

        metro_stations = await self._extract_card_texts(
            card,
            "[data-qa='address-metro-station-name']",
        )

        parts: list[str] = []

        if address:
            parts.append(address)

        # В некоторых карточках метро уже входит в address:
        # "Ташкент, Юнусабадская линия, метро Бадамзар".
        # В московских карточках часто address = "Москва", а метро отдельным span.
        address_lower = (address or "").lower()
        for station in metro_stations:
            if not station:
                continue

            station_lower = station.lower()
            if station_lower in address_lower:
                continue

            if "метро" in address_lower or "м." in address_lower:
                continue

            parts.append(f"м. {station}")

        location = ", ".join(parts)
        location = re.sub(r"\s+", " ", location).strip(" ,")

        return location or None

    def _extract_hh_area(self, url: str) -> str | None:
        try:
            query = dict(parse_qsl(urlparse(url).query))
            return query.get("area")
        except Exception:
            return None

    def _country_from_source(self) -> str | None:
        configured = self.source.settings.get("country")
        if configured:
            return str(configured).strip() or None

        area = self._extract_hh_area(self.source.url)

        # area=1 — Москва, страна Россия.
        # area=113 — вся Россия, если решишь использовать именно её.
        if area in {"1", "113"}:
            return "Россия"

        # area=16 — Беларусь.
        if area == "16":
            return "Беларусь"

        return None

    def _infer_country_from_location(self, location: str | None) -> str | None:
        if not location:
            return None

        text = location.lower()

        russia_markers = [
            "москва",
            "санкт-петербург",
            "нальчик",
            "краснодар",
            "казань",
            "екатеринбург",
            "новосибирск",
            "нижний новгород",
            "ростов",
            "самара",
            "воронеж",
            "пермь",
            "уфа",
            "челябинск",
            "красноярск",
            "сочи",
            "россия",
        ]

        belarus_markers = [
            "беларус",
            "минск",
            "гомель",
            "брест",
            "гродно",
            "витебск",
            "могилев",
            "могилёв",
        ]

        kazakhstan_markers = [
            "казахстан",
            "алматы",
            "астана",
            "караганда",
            "шымкент",
            "актобе",
            "павлодар",
        ]

        uzbekistan_markers = [
            "узбекистан",
            "ташкент",
            "самарканд",
            "бухара",
        ]

        if any(marker in text for marker in russia_markers):
            return "Россия"

        if any(marker in text for marker in belarus_markers):
            return "Беларусь"

        if any(marker in text for marker in kazakhstan_markers):
            return "Казахстан"

        if any(marker in text for marker in uzbekistan_markers):
            return "Узбекистан"

        return None

    async def _extract_card_text(self, card, selector: str) -> str | None:
        try:
            locator = card.locator(selector)
            if await locator.count() < 1:
                return None

            text = await locator.first.inner_text(timeout=1000)
            text = re.sub(r"\s+", " ", text or "").strip()
            return text or None
        except Exception:
            return None

    async def _check_site_authenticated(self) -> bool:
        """Check if the site requires authentication."""
        try:
            page = await browser_manager.get_shared_page()
            await page.goto(self.base_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            if _is_hh_domain(urlparse(self.base_url).netloc):
                # Check for a login link and a profile/account link to decide authentication.
                login_link = await page.query_selector("a[href*='account/login']")
                profile_link = await page.query_selector("a[href*='account/']")
                if login_link and not profile_link:
                    # No profile element → user likely not logged in.
                    log.warning(f"[{self.name}] HH login link detected – user not authenticated")
                    return False
                if login_link and profile_link:
                    log.info(f"[{self.name}] HH login link present but profile detected – user is authenticated")

            if _is_hirify_domain(urlparse(self.base_url).netloc):
                # User requirement: the login control is a button with class "login-button" and text "Войти".
                login_btn = await page.query_selector("button.login-button")
                user_elem = await page.query_selector(".user-name, .profile-info, .navbar-user")
                if login_btn and not user_elem:
                    # No user‑name element → not logged in.
                    log.warning(f"[{self.name}] Hirify login button detected – user not authenticated")
                    return False
                if login_btn and user_elem:
                    log.info(f"[{self.name}] Hirify login button present but user element found – user is authenticated")

            return True
        except Exception as exc:
            log.error(f"[{self.name}] Auth check failed: {exc}")
            return True  # Don't hard-block on transient issues

    async def fetch_vacancy_urls(self, params: SearchParams) -> list[str]:
        """Fetch vacancy URLs from the search results, handling pagination up to `params.max_pages`."""
        links: set[str] = set()
        # Helper to process a single page and collect links
        async def _process_page(page, base_url: str):
            # Wait for vacancy cards appropriate to the site
            try:
                if _is_hh_domain(urlparse(base_url).netloc):
                    await page.wait_for_selector("[data-qa='vacancy-serp__vacancy']", timeout=15000)
                else:
                    await page.wait_for_selector('.vacancy-card', timeout=15000)
            except Exception:
                log.info(f"[{self.name}] No vacancy cards found on {base_url}")
                return

            if _is_hh_domain(urlparse(base_url).netloc):
                vacancy_cards = await page.locator("[data-qa='vacancy-serp__vacancy']").all()
            else:
                vacancy_cards = await page.locator('.vacancy-card').all()
            print(f"[DEBUG] Found {len(vacancy_cards)} vacancy cards on {base_url}")
            for i, card in enumerate(vacancy_cards):
                try:
                    href = await card.locator("a[href]").first.get_attribute("href")
                    if not href:
                        continue
                    full_url = absolutize_url(self.base_url.rstrip('/'), href)
                    # Skip non‑vacancy HH URLs (apply/employer pages)
                    if _looks_like_hh_apply_or_employer(full_url):
                        log.info(f"[{self.name}] Skip non‑vacancy HH URL: {full_url}")
                        continue
                    # HH strictness: only /vacancy/<id>
                    if _is_hh_domain(urlparse(base_url).netloc) and not _is_hh_vacancy_url(full_url):
                        continue
                    # Hirify strictness: only /jobs/<id>-…
                    if _is_hirify_domain(urlparse(base_url).netloc) and not _is_hirify_job_url(full_url):
                        print(f"[DEBUG] Hirify URL filtered out: {full_url}")
                        continue
                    # Skip premium "plus" cards
                    try:
                        cls = await card.get_attribute("class")
                        if cls and " plus" in f" {cls} ":
                            continue
                        if await card.query_selector("span.blurred-company"):
                            continue
                    except Exception:
                        pass
                    links.add(full_url)

                    if _is_hh_domain(urlparse(base_url).netloc):
                        location = await self._extract_hh_serp_location(card)
                        country = self._country_from_source() or self._infer_country_from_location(location)

                        meta: dict[str, str] = {}

                        if location:
                            meta["location"] = location

                        if country:
                            meta["country"] = country

                        if meta:
                            self._serp_card_meta_by_url[full_url] = meta

                    print(f"[DEBUG] Added link: {full_url}")
                except Exception as e:
                    print(f"[DEBUG] Error processing card {i}: {e}")
                    continue

        # Create a fresh page for pagination handling
        async with browser_manager.new_page() as page:
            # Initial navigation (may involve query adapter)
            search_url = self.base_url
            already_navigated = False
            if params.query:
                adapter = get_query_adapter(self.base_url)
                if adapter:
                    route_result = await adapter.route(page, self.base_url, params)
                    search_url = route_result.final_url
                    already_navigated = True
            if not already_navigated:
                await page.goto(search_url, wait_until="domcontentloaded")
            else:
                await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(3000)
            # Process the first page
            await _process_page(page, search_url)

            # Simple pagination loop – works for HH (page=?page=) and Hirify (page parameter or next button)
            for page_index in range(2, params.max_pages + 1):
                next_url = None
                # Try to construct a URL with explicit page if the site uses it
                parsed = urlparse(search_url)
                query_dict = dict(parse_qsl(parsed.query))
                query_dict["page"] = str(page_index)
                new_query = urlencode(query_dict)
                next_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
                try:
                    await page.goto(next_url, wait_until="domcontentloaded")
                    await page.wait_for_timeout(3000)
                    await _process_page(page, next_url)
                except Exception as e:
                    log.info(f"[{self.name}] Pagination stopped at page {page_index - 1}: {e}")
                    break
        return list(links)

    async def fetch_vacancy_details(self, url: str, page: Page | None = None) -> Vacancy | None:
        """Fetch detailed vacancy data."""
        try:
            if page is None:
                async with browser_manager.new_page() as page:
                    await page.goto(url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms)
                    await page.wait_for_timeout(4000)
                    html = await page.content()
            else:
                await page.goto(url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms)
                await page.wait_for_timeout(4000)
                html = await page.content()

            extractor = VacancyExtractor(
                url=url,
                html=html,
                source_id=self.source.id,
                source_name=self.source.name,
            )

            vacancy = extractor.extract_vacancy()

            configured_country = self.source.settings.get("country")
            if vacancy and not vacancy.country and configured_country:
                vacancy.country = str(configured_country).strip()

            card_meta = self._serp_card_meta_by_url.get(url, {})
            card_location = card_meta.get("location")
            card_country = card_meta.get("country")

            if vacancy and not vacancy.location and card_location:
                vacancy.location = card_location

            if not getattr(vacancy, "country", None) and card_country:
                vacancy.country = card_country

            return vacancy
        except Exception as exc:
            log.error(f"[{self.name}] Failed to extract from {url}: {exc}")
            return None

    async def search(self, params: SearchParams) -> list[Vacancy]:
        """Search for vacancies."""
        # Check authentication before scanning for supported sources
        if not await self._check_site_authenticated():
            log.error(f"[{self.name}] Stopping scan — authentication required")
            return []

        vacancy_urls = await self.fetch_vacancy_urls(params)
        log.info(f"[{self.name}] Found {len(vacancy_urls)} vacancy links")

        vacancies: list[Vacancy] = []

        # Reuse a single tab for all detail pages to avoid visible
        # open/close flicker for each vacancy (works for both HH and Hirify).
        async with browser_manager.new_page() as page:
            for index, url in enumerate(vacancy_urls, start=1):
                if len(vacancies) >= params.max_vacancies:
                    break

                # Skip parsing if vacancy already exists in the store
                from vacancy_agent.storage import storage as _storage
                vacancy_id = url.split('/')[-1].split('?')[0]
                if _storage.find_vacancy(vacancy_id):
                    log.info(f"[{self.name}] Vacancy already in DB, skipping: {url}")
                    continue

                # Fetch details using the shared page
                vacancy = await self.fetch_vacancy_details(url, page=page)
                if vacancy:
                    vacancies.append(vacancy)

        return vacancies