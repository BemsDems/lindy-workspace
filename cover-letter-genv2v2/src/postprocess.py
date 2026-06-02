from __future__ import annotations

import logging
import re
from typing import List

logger = logging.getLogger(__name__)

_DECIMAL_SENTINEL = "\x00DEC\x00"
_DECIMAL_RE = re.compile(r"(\d+)\.(\d+)")

# Patterns that match a WHOLE sentence (anchored at start, allowed to continue
# arbitrarily). Each pattern intentionally ends loose so it catches variants
# like «Буду рад обсудить, чем могу быть полезен вашей команде.»
_WEAK_ENDING_PATTERNS = [
    r"готов\s+применить",
    r"готов\s+обсудить\s+детали",
    r"готов\s+обсудить\s+задачи",
    r"готов\s+обсудить(?!\s+(?:архитектур|показать|рассказать|продемонстрировать|метрики|подробност[ия]))",
    r"готов\s+рассказать\s+подробнее",
    r"готов\s+поделиться\s+опытом",
    r"буду\s+рад\s+обсудить",
    r"был\s+бы\s+рад\s+обсудить",
    r"с\s+удовольствием\s+обсужу",
    r"с\s+радостью\s+обсужу",
    r"этот\s+опыт\s+поможет",
    r"этот\s+опыт\s+может\s+быть\s+полезен",
    r"этот\s+опыт\s+будет\s+полезен",
    r"смогу\s+быстро\s+включиться",
    r"быстро\s+влиться\s+в\s+команду",
    r"буду\s+полезен",
    r"могу\s+быть\s+полезен",
    r"хотел\s+бы\s+обсудить",
    r"хотел\s+бы\s+присоединиться",
    r"хочу\s+обсудить\s+на\s+собеседовании",
    r"чем\s+могу\s+быть\s+полезен",
    r"надеюсь\s+на\s+ответ",
    r"жду\s+вашего\s+ответа",
    r"спасибо\s+за\s+внимание",
]
_WEAK_ENDING_RE = re.compile(
    r"^\s*(?:" + "|".join(_WEAK_ENDING_PATTERNS) + r")",
    re.IGNORECASE,
)

_STACK_LEAD_RE = re.compile(
    r"^\s*(?:мой\s+стек|стек[:\-]|технологии[:\-]|использую\s+стек)",
    re.IGNORECASE,
)
_VERB_LEAD_RE = re.compile(r"^\s*(?:работаю|использую|пишу|применяю)\b", re.IGNORECASE)
_STACK_LIST_RE = re.compile(r"[A-Za-z][\w+.#-]*(?:\s*,\s*[A-Za-z][\w+.#-]*){2,}")
_RUN_CONNECTOR_RE = re.compile(r"\s+(?:и|а\s+также)\s+", re.IGNORECASE)

_META_LEAD_RE = re.compile(
    r"^\s*(?:вот\s+мой\s+ответ|вот\s+письмо|ниже\s+письмо|сопроводительное\s+письмо)[:\-]?",
    re.IGNORECASE,
)
_META_FULL_LINE_RE = re.compile(
    r"^\s*(?:как\s+ии|как\s+языковая\s+модель|я\s+—\s+ии).*$",
    re.IGNORECASE,
)
_SIGNATURE_RE = re.compile(
    r"\n\s*(?:с\s+уважением|искренне\s+ваш|best\s+regards)[\s,].*$",
    re.IGNORECASE | re.DOTALL,
)

# Years-of-experience opener: "3+ года ...", "3 лет ...", "5+ лет коммерческой ...",
# etc. Matches a sentence that STARTS with a digit, optional plus, then
# лет/года/год (case-insensitive). This is the deterministic guard against
# LLM bias toward the canned "3+ года Flutter-разработки" opener despite
# explicit prompt-level prohibitions in writer.FINALIZER_SYSTEM.
_YEARS_OPENER_RE = re.compile(
    r"^\s*\d+\s*\+?\s*(?:лет|года|год)\b",
    re.IGNORECASE,
)

_GREETING_RE = re.compile(
    r"^\s*(?:здравствуйте|добрый\s+день|добрый\s+вечер|доброе\s+утро|приветствую|hello|hi)[\s,!.\-—]*",
    re.IGNORECASE,
)

_WORD_RE = re.compile(r"\w+", re.UNICODE)

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


def _split_sentences(paragraph: str) -> List[str]:
    """Split into sentences keeping their trailing