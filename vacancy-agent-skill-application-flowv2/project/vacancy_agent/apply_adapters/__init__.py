from vacancy_agent.apply_adapters.base import ApplyAdapter, ApplyResult, ApplyStatus
from vacancy_agent.apply_adapters.registry import get_apply_adapter, registered_platforms

__all__ = [
    "ApplyAdapter",
    "ApplyResult",
    "ApplyStatus",
    "get_apply_adapter",
    "registered_platforms",
]
