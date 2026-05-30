import re
from urllib.parse import urljoin


def normalize_space(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def clean_multiline(text: str | None) -> str:
    if not text:
        return ""
    lines = [normalize_space(line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def absolutize_url(base_url: str, href: str) -> str:
    return urljoin(base_url, href)


def contains_any(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)
