from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from vacancy_agent.apply_adapters.base import ApplyAdapter, ApplyResult, ApplyStatus
from vacancy_agent.config import settings
from vacancy_agent.logger import log
from vacancy_agent.schemas import Vacancy


def _is_hh_domain(netloc: str) -> bool:
    host = (netloc or "").lower()
    return host.endswith("hh.ru") or host.endswith("hh.kz")

HH_TRANSIENT_NAVIGATION_ERRORS = (
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_CLOSED",
    "ERR_TIMED_OUT",
    "ERR_HTTP2_PROTOCOL_ERROR",
    "ERR_NETWORK_CHANGED",
    "Navigation timeout",
)


def _is_transient_navigation_error(exc: Exception) -> bool:
    text = str(exc)
    return any(marker in text for marker in HH_TRANSIENT_NAVIGATION_ERRORS)


def _canonical_hh_vacancy_url(vacancy_url: str) -> str:
    parsed = urlparse(vacancy_url)
    host = (parsed.netloc or "").lower()

    if host.endswith("hh.ru"):
        return parsed._replace(netloc="hh.ru", query="", fragment="").geturl()

    if host.endswith("hh.kz"):
        return parsed._replace(netloc="hh.kz", query="", fragment="").geturl()

    return vacancy_url


async def _goto_hh_vacancy_with_retries(
    page: Page,
    vacancy_url: str,
    *,
    attempts: int | None = None,
) -> None:
    total_attempts = attempts or max(settings.max_retries + 1, 2)

    candidate_urls = [vacancy_url]
    canonical_url = _canonical_hh_vacancy_url(vacancy_url)

    if canonical_url != vacancy_url:
        candidate_urls.append(canonical_url)

    last_error: Exception | None = None

    for current_url in candidate_urls:
        for attempt in range(1, total_attempts + 1):
            try:
                await page.goto(
                    current_url,
                    wait_until="domcontentloaded",
                    timeout=settings.browser_timeout_ms,
                )

                if current_url != vacancy_url:
                    log.info(f"[hh] Opened canonical HH URL instead of regional URL: {current_url}")

                return

            except PlaywrightTimeoutError as exc:
                last_error = exc

            except PlaywrightError as exc:
                last_error = exc

                if not _is_transient_navigation_error(exc):
                    raise

            if attempt < total_attempts:
                log.warning(
                    f"[hh] Navigation failed, retrying {attempt}/{total_attempts}: "
                    f"{current_url} — {last_error}"
                )

                try:
                    await page.goto("about:blank", wait_until="domcontentloaded", timeout=5000)
                except Exception:
                    pass

                await asyncio.sleep(min(1.5 * attempt, 5.0))

    if last_error:
        raise last_error

    raise RuntimeError(f"Не удалось открыть HH vacancy URL: {vacancy_url}")


class HHApplySelectors:
    """Stable HH selectors collected from data-qa attributes.

    Keep all HH-specific DOM knowledge here, not in manager/agent code.
    """

    APPLY_BUTTON = '[data-qa="vacancy-response-link-top"]'

    ARCHIVED_MARKERS = [
        '[data-qa="vacancy-archive-description"]',
        'text=Вакансия в архиве',
        'text=больше не принимает отклики',
    ]

    RELOCATION_WARNING_TITLE = '[data-qa="relocation-warning-title"]'
    RELOCATION_CONFIRM_BUTTON = '[data-qa="relocation-warning-confirm"]'
    RELOCATION_ABORT_BUTTON = '[data-qa="relocation-warning-abort"]'

    COVER_LETTER_TEXTAREA = 'textarea[name="text"]'
    COVER_LETTER_FORM = '[data-qa="vacancy-response-letter-informer"] form'
    SUBMIT_BUTTON = '[data-qa="vacancy-response-letter-submit"]'

    SUCCESS_CARD_TEXT = 'text=Резюме доставлено'
    CHAT_BUTTON = '[data-qa="vacancy-response-link-view-topic"]'

    FORM_ERROR = '[data-qa="form-helper-error"]'

    QUESTIONNAIRE_MARKERS = [
        '[data-qa="vacancy-response-questionary"]',
        '[data-qa*="questionary"]',
        '[data-qa*="questionnaire"]',
        'text=Ответьте на вопросы',
        'text=Работодатель просит ответить',
        'text=Вопросы работодателя',
    ]

    TEST_MARKERS = [
        '[data-qa*="test"]',
        '[data-qa*="assessment"]',
        'text=Тестовое задание',
        'text=Пройти тест',
        'text=Выполнить тест',
    ]

    LOGIN_MARKERS = [
        'text=Войти',
        'text=Авторизация',
        'text=Войдите',
        '[data-qa="account-login"]',
    ]

    CAPTCHA_MARKERS = [
        'text=Подтвердите, что вы не робот',
        'text=captcha',
        'iframe[src*="captcha"]',
        '[data-qa*="captcha"]',
    ]


def validate_cover_letter_basic(text: str) -> tuple[bool, str | None]:
    """Technical guard before inserting text into a job-board form.

    This is intentionally not a semantic/factual validator. It only prevents
    empty strings and obvious template leftovers from reaching the website.
    """

    normalized = text.strip()

    if not normalized:
        return False, "Сопроводительное письмо пустое."
    if len(normalized) < 80:
        return False, "Сопроводительное письмо слишком короткое."
    if len(normalized) > 5000:
        return False, "Сопроводительное письмо слишком длинное."

    forbidden_fragments = ["{{", "}}", "[company]", "[vacancy]", "TODO", "FIXME"]
    lower = normalized.lower()
    for fragment in forbidden_fragments:
        if fragment.lower() in lower:
            return False, f"В письме остался шаблонный фрагмент: {fragment}"

    return True, None


async def human_pause(min_seconds: float = 0.4, max_seconds: float = 1.2) -> None:
    """Floating wait for UI stability after clicks/renders.

    It is used to avoid racing client-side rendering, not to bypass security
    systems.
    """

    await asyncio.sleep(random.uniform(min_seconds, max_seconds))


async def is_visible(page: Page, selector: str, timeout: int = 1000) -> bool:
    try:
        await page.locator(selector).first.wait_for(state="visible", timeout=timeout)
        return True
    except PlaywrightTimeoutError:
        return False


async def any_visible(page: Page, selectors: list[str], timeout: int = 700) -> bool:
    for selector in selectors:
        if await is_visible(page, selector, timeout=timeout):
            return True
    return False


async def safe_click(page: Page, selector: str, timeout: int = 5000) -> bool:
    try:
        locator = page.locator(selector).first
        await locator.wait_for(state="visible", timeout=timeout)
        await locator.click()
        return True
    except PlaywrightTimeoutError:
        return False


@dataclass(slots=True)
class HHApplyAdapter(ApplyAdapter):
    """HH.ru / HH.kz application adapter."""

    platform: str = "hh"

    def can_handle(self, vacancy: Vacancy) -> bool:
        try:
            return _is_hh_domain(urlparse(vacancy.url).netloc)
        except Exception:
            return False

    async def apply(
        self,
        page: Page,
        vacancy: Vacancy,
        cover_letter: str,
        *,
        dry_run: bool = True,
    ) -> ApplyResult:
        return await self._apply_to_hh_vacancy(
            page=page,
            vacancy_url=vacancy.url,
            cover_letter=cover_letter,
            dry_run=dry_run,
        )

    async def _wait_for_cover_letter_field(self, page: Page, timeout: int = 10000) -> bool:
        try:
            await page.locator(HHApplySelectors.COVER_LETTER_TEXTAREA).first.wait_for(
                state="visible",
                timeout=timeout,
            )
            return True
        except PlaywrightTimeoutError:
            return False

    async def _wait_for_success(self, page: Page, timeout: int = 10000) -> bool:
        try:
            await page.locator(HHApplySelectors.SUCCESS_CARD_TEXT).first.wait_for(
                state="visible",
                timeout=timeout,
            )
            return True
        except PlaywrightTimeoutError:
            return await is_visible(page, HHApplySelectors.CHAT_BUTTON, timeout=1000)

    async def _detect_archived(self, page: Page, vacancy_url: str) -> ApplyResult | None:
        if await any_visible(page, HHApplySelectors.ARCHIVED_MARKERS, timeout=800):
            return ApplyResult(
                status=ApplyStatus.ARCHIVED,
                vacancy_url=vacancy_url,
                platform=self.platform,
                message=(
                    "Вакансия находится в архиве. "
                    "Работодатель больше не принимает отклики на эту вакансию."
                ),
            )

        return None

    async def _detect_blockers(self, page: Page, vacancy_url: str) -> ApplyResult | None:
        if await any_visible(page, HHApplySelectors.LOGIN_MARKERS):
            return ApplyResult(
                status=ApplyStatus.LOGIN_REQUIRED,
                vacancy_url=vacancy_url,
                platform=self.platform,
                message="HH просит авторизоваться.",
            )

        if await any_visible(page, HHApplySelectors.CAPTCHA_MARKERS):
            return ApplyResult(
                status=ApplyStatus.CAPTCHA_REQUIRED,
                vacancy_url=vacancy_url,
                platform=self.platform,
                message="HH показал капчу или антибот-проверку.",
            )

        return None

    async def _detect_questionnaire_or_test(self, page: Page, vacancy_url: str) -> ApplyResult | None:
        if await any_visible(page, HHApplySelectors.TEST_MARKERS, timeout=800):
            return ApplyResult(
                status=ApplyStatus.TEST_REQUIRED,
                vacancy_url=vacancy_url,
                platform=self.platform,
                message="При отклике обнаружен тест или тестовое задание. Нужна ручная обработка.",
            )

        if await any_visible(page, HHApplySelectors.QUESTIONNAIRE_MARKERS, timeout=800):
            return ApplyResult(
                status=ApplyStatus.QUESTIONNAIRE_REQUIRED,
                vacancy_url=vacancy_url,
                platform=self.platform,
                message="При отклике обнаружены обязательные вопросы работодателя. Нужна ручная обработка.",
            )

        return None

    async def _detect_pre_click_interruption(self, page: Page, vacancy_url: str) -> ApplyResult | None:
        archived = await self._detect_archived(page, vacancy_url)
        if archived:
            return archived

        blocker = await self._detect_blockers(page, vacancy_url)
        if blocker:
            return blocker

        if await self._wait_for_success(page, timeout=1200):
            return ApplyResult(
                status=ApplyStatus.ALREADY_APPLIED,
                vacancy_url=vacancy_url,
                platform=self.platform,
                message="На странице уже отображается статус успешного отклика.",
            )

        return None

    async def _detect_post_click_interruption(self, page: Page, vacancy_url: str) -> ApplyResult | None:
        blocker = await self._detect_blockers(page, vacancy_url)
        if blocker:
            return blocker

        questionnaire_or_test = await self._detect_questionnaire_or_test(page, vacancy_url)
        if questionnaire_or_test:
            return questionnaire_or_test

        return None

    async def _detect_any_interruption(self, page: Page, vacancy_url: str) -> ApplyResult | None:
        pre_click = await self._detect_pre_click_interruption(page, vacancy_url)
        if pre_click:
            return pre_click

        post_click = await self._detect_post_click_interruption(page, vacancy_url)
        if post_click:
            return post_click

        return None

    async def _apply_to_hh_vacancy(
        self,
        page: Page,
        vacancy_url: str,
        cover_letter: str,
        *,
        dry_run: bool = True,
    ) -> ApplyResult:
        """Run the HH apply flow with an already approved cover letter."""

        valid, validation_error = validate_cover_letter_basic(cover_letter)
        if not valid:
            return ApplyResult(
                status=ApplyStatus.VALIDATION_FAILED,
                vacancy_url=vacancy_url,
                platform=self.platform,
                message=validation_error,
            )

        try:
            page.set_default_timeout(settings.browser_timeout_ms)
            await _goto_hh_vacancy_with_retries(page, vacancy_url)

            interruption = await self._detect_pre_click_interruption(page, vacancy_url)
            if interruption:
                return interruption

            apply_clicked = await safe_click(page, HHApplySelectors.APPLY_BUTTON, timeout=7000)
            if not apply_clicked:
                return ApplyResult(
                    status=ApplyStatus.APPLY_BUTTON_NOT_FOUND,
                    vacancy_url=vacancy_url,
                    platform=self.platform,
                    message="Не найдена первая кнопка «Откликнуться».",
                )

            await human_pause(0.7, 1.5)

            relocation_warning_visible = await is_visible(
                page,
                HHApplySelectors.RELOCATION_WARNING_TITLE,
                timeout=5000,
            )

            if relocation_warning_visible:
                log.info(f"[hh] Detected relocation warning for vacancy: {vacancy_url}")
                confirm_clicked = await safe_click(
                    page,
                    HHApplySelectors.RELOCATION_CONFIRM_BUTTON,
                    timeout=7000,
                )
                if not confirm_clicked:
                    return ApplyResult(
                        status=ApplyStatus.SUBMIT_FAILED,
                        vacancy_url=vacancy_url,
                        platform=self.platform,
                        message="Появилось предупреждение другой страны, но кнопка «Все равно откликнуться» не найдена.",
                    )

                await human_pause(1.0, 2.2)

            interruption = await self._detect_post_click_interruption(page, vacancy_url)
            if interruption:
                return interruption

            field_found = await self._wait_for_cover_letter_field(page, timeout=10000)
            if not field_found:
                interruption = await self._detect_any_interruption(page, vacancy_url)
                if interruption:
                    return interruption
                return ApplyResult(
                    status=ApplyStatus.COVER_LETTER_FIELD_NOT_FOUND,
                    vacancy_url=vacancy_url,
                    platform=self.platform,
                    message="Поле сопроводительного письма не появилось.",
                )

            textarea = page.locator(HHApplySelectors.COVER_LETTER_TEXTAREA).first
            clean_letter = cover_letter.strip()
            await textarea.fill("")
            await textarea.fill(clean_letter)

            inserted_value = await textarea.input_value()
            if inserted_value.strip() != clean_letter:
                return ApplyResult(
                    status=ApplyStatus.VALIDATION_FAILED,
                    vacancy_url=vacancy_url,
                    platform=self.platform,
                    message="Текст письма не был корректно вставлен в textarea.",
                )

            submit_button = page.locator(HHApplySelectors.SUBMIT_BUTTON).first
            try:
                await submit_button.wait_for(state="visible", timeout=5000)
            except PlaywrightTimeoutError:
                return ApplyResult(
                    status=ApplyStatus.SUBMIT_BUTTON_NOT_FOUND,
                    vacancy_url=vacancy_url,
                    platform=self.platform,
                    message="Финальная кнопка «Отправить» не найдена.",
                )

            interruption = await self._detect_questionnaire_or_test(page, vacancy_url)
            if interruption:
                return interruption

            if dry_run:
                return ApplyResult(
                    status=ApplyStatus.DRY_RUN_SUCCESS,
                    vacancy_url=vacancy_url,
                    platform=self.platform,
                    message="dry_run: письмо вставлено, финальная отправка не выполнялась.",
                )

            await submit_button.click()
            await human_pause(0.8, 1.7)

            interruption = await self._detect_any_interruption(page, vacancy_url)
            if interruption:
                return interruption

            if await is_visible(page, HHApplySelectors.FORM_ERROR, timeout=1500):
                error_text = await page.locator(HHApplySelectors.FORM_ERROR).first.inner_text()
                return ApplyResult(
                    status=ApplyStatus.SUBMIT_FAILED,
                    vacancy_url=vacancy_url,
                    platform=self.platform,
                    message=f"HH вернул ошибку формы: {error_text}",
                )

            if await self._wait_for_success(page, timeout=12000):
                return ApplyResult(
                    status=ApplyStatus.SUCCESS,
                    vacancy_url=vacancy_url,
                    platform=self.platform,
                    message="Отклик успешно отправлен. Появился статус «Резюме доставлено».",
                )

            return ApplyResult(
                status=ApplyStatus.SUBMIT_FAILED,
                vacancy_url=vacancy_url,
                platform=self.platform,
                message="После нажатия «Отправить» не появилось подтверждение успешного отклика.",
            )

        except PlaywrightTimeoutError as exc:
            return ApplyResult(
                status=ApplyStatus.TIMEOUT,
                vacancy_url=vacancy_url,
                platform=self.platform,
                message=f"Не удалось открыть или обработать страницу HH после повторных попыток: {exc}",
            )

        except PlaywrightError as exc:
            if _is_transient_navigation_error(exc):
                log.warning(f"[hh] Transient HH navigation error after retries: {exc}")
                return ApplyResult(
                    status=ApplyStatus.TIMEOUT,
                    vacancy_url=vacancy_url,
                    platform=self.platform,
                    message=f"Временная сетевая ошибка HH после повторных попыток: {exc}",
                )

            log.warning(f"[hh] Playwright apply flow error: {exc}")
            return ApplyResult(
                status=ApplyStatus.UNKNOWN_ERROR,
                vacancy_url=vacancy_url,
                platform=self.platform,
                message=str(exc),
            )

        except Exception as exc:  # noqa: BLE001
            log.exception("[hh] Unexpected apply flow error")
            return ApplyResult(
                status=ApplyStatus.UNKNOWN_ERROR,
                vacancy_url=vacancy_url,
                platform=self.platform,
                message=str(exc),
            )
