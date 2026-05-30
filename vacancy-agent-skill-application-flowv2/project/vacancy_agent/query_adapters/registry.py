from __future__ import annotations

from urllib.parse import urlparse

from .hh import HHQueryAdapter
from .hirify import HirifyQueryAdapter


_HH = HHQueryAdapter()
_HIRIFY = HirifyQueryAdapter()


def get_query_adapter(url: str):
    """Return a domain-specific adapter for query routing, if available."""
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return None

    if host.endswith("hh.ru") or host.endswith("hh.kz"):
        return _HH

    if host.endswith("hirify.me"):
        return _HIRIFY

    return None
