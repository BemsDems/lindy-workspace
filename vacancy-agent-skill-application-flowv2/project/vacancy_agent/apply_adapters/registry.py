from __future__ import annotations

from vacancy_agent.apply_adapters.base import ApplyAdapter, ApplyResult, ApplyStatus
from vacancy_agent.apply_adapters.hh import HHApplyAdapter
from vacancy_agent.schemas import Vacancy


_ADAPTERS: list[ApplyAdapter] = [
    HHApplyAdapter(),
]


def get_apply_adapter(vacancy: Vacancy) -> ApplyAdapter | None:
    """Return a platform-specific apply adapter for a vacancy."""

    for adapter in _ADAPTERS:
        try:
            if adapter.can_handle(vacancy):
                return adapter
        except Exception:
            continue
    return None


def unsupported_result(vacancy: Vacancy) -> ApplyResult:
    return ApplyResult(
        status=ApplyStatus.UNSUPPORTED_PLATFORM,
        vacancy_url=vacancy.url,
        platform="unknown",
        message="Для этой платформы пока нет apply-адаптера.",
    )


def registered_platforms() -> list[str]:
    return [adapter.platform for adapter in _ADAPTERS]
