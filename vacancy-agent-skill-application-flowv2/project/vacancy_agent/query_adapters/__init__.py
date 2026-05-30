"""Domain-specific query routing adapters.

Adapters translate a generic `SearchParams(query=...)` into site-specific navigation:
- URL rewrite (query-param based)
- UI interaction (fill search input + submit)

HH adapters are CDP/Playwright DOM-first and keep the scraper read-only.
"""
