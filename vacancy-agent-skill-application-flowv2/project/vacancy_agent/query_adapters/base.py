from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from vacancy_agent.schemas import SearchParams


@dataclass(frozen=True)
class QueryRouteResult:
    """Result of applying a query route.

    `final_url` is the URL the scraper should treat as the active search results page.
    """

    final_url: str


class QueryAdapter(Protocol):
    def can_handle(self, url: str) -> bool: ...

    async def route(self, page, base_url: str, params: SearchParams) -> QueryRouteResult:
        """Ensure the page is navigated to the correct results URL for the query.

        Implementations may rewrite URL query params or use UI actions.
        Must be read-only (no apply flows).
        """

        ...
