from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from playwright.async_api import Page

from vacancy_agent.schemas import Vacancy


class ApplyStatus(str, Enum):
    """Unified result statuses for all application platforms."""

    SUCCESS = "success"
    DRY_RUN_SUCCESS = "dry_run_success"
    ALREADY_APPLIED = "already_applied"
    ARCHIVED = "archived"

    UNSUPPORTED_PLATFORM = "unsupported_platform"
    LOGIN_REQUIRED = "login_required"
    CAPTCHA_REQUIRED = "captcha_required"
    QUESTIONNAIRE_REQUIRED = "questionnaire_required"
    TEST_REQUIRED = "test_required"

    APPLY_BUTTON_NOT_FOUND = "apply_button_not_found"
    COVER_LETTER_FIELD_NOT_FOUND = "cover_letter_field_not_found"
    SUBMIT_BUTTON_NOT_FOUND = "submit_button_not_found"
    VALIDATION_FAILED = "validation_failed"
    SUBMIT_FAILED = "submit_failed"
    TIMEOUT = "timeout"
    UNKNOWN_ERROR = "unknown_error"


@dataclass(slots=True)
class ApplyResult:
    """Platform-neutral apply result returned by any adapter."""

    status: ApplyStatus
    vacancy_url: str
    platform: str
    message: str | None = None

    @property
    def is_success(self) -> bool:
        return self.status in {
            ApplyStatus.SUCCESS,
            ApplyStatus.DRY_RUN_SUCCESS,
            ApplyStatus.ALREADY_APPLIED,
        }

    @property
    def was_sent(self) -> bool:
        return self.status in {ApplyStatus.SUCCESS, ApplyStatus.ALREADY_APPLIED}

    @property
    def needs_manual_action(self) -> bool:
        return self.status in {
            ApplyStatus.LOGIN_REQUIRED,
            ApplyStatus.CAPTCHA_REQUIRED,
            ApplyStatus.QUESTIONNAIRE_REQUIRED,
            ApplyStatus.TEST_REQUIRED,
        }

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "status": self.status.value,
            "vacancy_url": self.vacancy_url,
            "platform": self.platform,
            "message": self.message,
            "is_success": self.is_success,
            "was_sent": self.was_sent,
            "needs_manual_action": self.needs_manual_action,
        }


class ApplyAdapter(Protocol):
    """Interface for site-specific application/response flows.

    Each job board should implement this protocol. The rest of the agent only
    calls `apply(...)` through the registry/service and does not know about
    platform-specific selectors or dialogs.
    """

    platform: str

    def can_handle(self, vacancy: Vacancy) -> bool:
        """Return True if this adapter can apply to the given vacancy."""

        ...

    async def apply(
        self,
        page: Page,
        vacancy: Vacancy,
        cover_letter: str,
        *,
        dry_run: bool = True,
    ) -> ApplyResult:
        """Run the platform-specific apply flow."""

        ...
