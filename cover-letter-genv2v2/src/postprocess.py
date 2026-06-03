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


def _word_count(text: str) -> int:
    """Count words the same way the validator/clamp do (\\w+ tokens).

    Exposed as a small shared helper so tests and other modules rely on a
    single definition of \"word\" instead of each re-deriving it from _WORD_RE.
    """
    return len(_WORD_RE.findall(text or ""))


_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")

# Version strings like "3.22.2" occasionally come back from the LLM with stray
# spaces after the dots ("3. 22. 2"), which both looks broken and desyncs the
# number from its allowed-numbers whitelist. Collapse the internal whitespace
# of any THREE-part numeric version back to canonical "X.Y.Z". Restricting the
# pattern to three dotted numeric groups keeps it from merging real sentence
# boundaries such as "...5 модулей. 3 из них..." (no third dotted group there).
_SPACED_VERSION_RE = re.compile(r"\b(\d+)\.\s*(\d+)\.\s*(\d+)\b")


def normalize_version_numbers(text: str) -> str:
    """Canonicalize spaced-out 3-part version numbers: "3. 22. 2" -> "3.22.2".

    No-op for already-clean versions ("3.22.2") and for anything that is not a
    three-part dotted numeric run, so ordinary prose and 2-part decimals
    ("99.5%") are left untouched.
    """
    if not text:
        return text
    return _SPACED_VERSION_RE.sub(r"\1.\2.\3", text)


def _split_sentences(paragraph: str) -> List[str]:
    """Split into sentences keeping their trailing punctuation.

    Decimals like '99.5' and version strings like '3.29.0' are masked first
    so the '.' isn't treated as a sentence boundary, then restored.
    The masking runs iteratively until stable to handle X.Y.Z patterns.
    """
    masked = paragraph
    for _ in range(8):  # cap iterations defensively; 8 covers any realistic version depth
        new_masked = _DECIMAL_RE.sub(rf"\1{_DECIMAL_SENTINEL}\2", masked)
        if new_masked == masked:
            break
        masked = new_masked
    parts = re.findall(r"[^.!?]*[.!?]+|\S[^.!?]*$", masked)
    sentences = [p.replace(_DECIMAL_SENTINEL, ".").strip() for p in parts if p.strip()]
    logger.debug("_split_sentences: %d sentences from %d-char paragraph", len(sentences), len(paragraph))
    return sentences


def _is_stack_dump_sentence(sentence: str) -> bool:
    if _STACK_LEAD_RE.search(sentence):
        return True
    if _VERB_LEAD_RE.search(sentence) and _STACK_LIST_RE.search(sentence):
        return True
    if _STACK_LIST_RE.search(sentence) and len(_WORD_RE.findall(sentence)) <= 12:
        return True
    return False


def strip_meta_and_signature(text: str) -> str:
    """Remove meta preambles ('Вот моё письмо:') and trailing signatures."""
    lines = text.splitlines()
    cleaned: List[str] = []
    for line in lines:
        if _META_FULL_LINE_RE.match(line):
            continue
        line = _META_LEAD_RE.sub("", line)
        cleaned.append(line)
    out = "\n".join(cleaned)
    out = _SIGNATURE_RE.sub("", out)
    return out.strip()


def strip_years_opener(text: str) -> str:
    """Deterministic guard against the canned 'X+ лет/года' opener.

    The LLM keeps falling back to 'X+ года коммерческой Flutter-разработки' as
    the first sentence despite explicit FINALIZER_SYSTEM rules and the
    years-free opener_pool. This is the last-line defense: if the first
    real sentence (after an optional greeting) starts with a digit + лет/года,
    we drop that sentence entirely.

    Behavior:
      - Operates only on the FIRST paragraph.
      - Skips an optional leading greeting ('Здравствуйте,', 'Добрый день,', etc.)
        and re-attaches it after the strip.
      - Only removes if the years-opener pattern matches the FIRST sentence
        of the first paragraph (we don't touch mid-letter mentions like
        '...за 3 года в Food One...').
      - Rollback guard: if removal would leave the first paragraph with
        zero sentences AND the letter has only one paragraph, we keep
        the original sentence (no other content to fall back to).
    """
    paragraphs = _PARAGRAPH_SPLIT_RE.split(text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    if not paragraphs:
        return text

    first_para = paragraphs[0]
    greeting = ""
    body = first_para
    greeting_match = _GREETING_RE.match(first_para)
    if greeting_match:
        greeting = first_para[: greeting_match.end()].rstrip() + " "
        body = first_para[greeting_match.end():].lstrip()

    sentences = _split_sentences(body)
    if not sentences:
        return text

    if not _YEARS_OPENER_RE.match(sentences[0]):
        return text

    removed_sentence = sentences[0]
    remaining = sentences[1:]

    if not remaining and len(paragraphs) <= 1:
        logger.debug(
            "strip_years_opener: rolled back removal — opener is the only sentence "
            "in a single-paragraph letter: %r",
            removed_sentence[:80],
        )
        return text

    logger.info(
        "strip_years_opener: removed canned years-experience opener: %r",
        removed_sentence[:120],
    )

    if remaining:
        new_first_para = (greeting + " ".join(remaining)).strip()
        paragraphs[0] = new_first_para
    else:
        if greeting.strip():
            paragraphs[1] = (greeting + paragraphs[1]).strip()
        paragraphs.pop(0)

    return "\n\n".join(paragraphs)


def strip_stack_dump(text: str) -> str:
    """Drop sentences that look like a bare stack enumeration.

    PARAGRAPH-AWARE: processes each paragraph in isolation and re-joins
    with the original \n\n separators. Never collapses multi-paragraph
    letters into a single block.
    """
    paragraphs = _PARAGRAPH_SPLIT_RE.split(text)
    out_paragraphs: List[str] = []
    for paragraph in paragraphs:
        stripped = paragraph.strip()
        if not stripped:
            continue
        sents = _split_sentences(stripped)
        kept = [s for s in sents if not _is_stack_dump_sentence(s)]
        if kept:
            out_paragraphs.append(" ".join(kept))
    logger.debug("strip_stack_dump: %d paragraph(s) retained from input", len(out_paragraphs))
    return "\n\n".join(out_paragraphs)


def strip_weak_ending(text: str) -> str:
    """Remove a trailing weak/filler sentence ('Готов применить…').

    Only matches the narrow cliché patterns in _WEAK_ENDING_PATTERNS, so
    live finals like 'Готов показать архитектуру…' or 'Готов привести
    метрики по проекту…' are preserved.

    PARAGRAPH-AWARE: operates only on the last paragraph and re-joins
    paragraphs with \n\n separators preserved.

    Rollback guard: if removing the weak ending would leave the last
    paragraph with fewer than 2 sentences AND the letter has fewer than
    3 paragraphs total, the removal is rolled back. This protects
    against truncating already-short letters down to a stub.
    """
    paragraphs = _PARAGRAPH_SPLIT_RE.split(text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    if not paragraphs:
        return text
    last = paragraphs[-1]
    sents_original = _split_sentences(last)
    sents = list(sents_original)
    removed = 0
    while sents and _WEAK_ENDING_RE.match(sents[-1]):
        sents.pop()
        removed += 1
    if removed > 0 and len(sents) < 2 and len(paragraphs) < 3:
        logger.debug(
            "strip_weak_ending: rolled back removal of %d sentence(s) — would leave "
            "last paragraph with %d sentence(s) in a %d-paragraph letter",
            removed, len(sents), len(paragraphs),
        )
        sents = sents_original
        removed = 0
    if removed > 0:
        logger.debug("strip_weak_ending: removed %d trailing weak sentence(s)", removed)
    paragraphs[-1] = " ".join(sents)
    paragraphs = [p for p in paragraphs if p.strip()]
    return "\n\n".join(paragraphs)


def normalize_punctuation(text: str) -> str:
    """Normalize punctuation INSIDE each paragraph; never touch \n\n boundaries."""
    paragraphs = _PARAGRAPH_SPLIT_RE.split(text)
    cleaned: List[str] = []
    for paragraph in paragraphs:
        stripped = paragraph.strip()
        if not stripped:
            continue
        item = re.sub(r"\s+([,.;:!?])", r"\1", stripped)
        item = re.sub(r"([,.;:!?])([^\s\d])", r"\1 \2", item)
        item = re.sub(r"([!?.]){2,}", r"\1", item)
        item = re.sub(r",(\s*,)+", ",", item)  # collapse repeated commas
        item = re.sub(r"\s*\(\s*\)\s*", " ", item)  # drop empty () left by fragment removal
        item = re.sub(r"—(?:\s*—)+", "—", item)  # collapse repeated em-dashes
        item = re.sub(r"\s*—\s*([.,;:!?])", r"\1", item)  # drop dangling em-dash before punctuation
        item = re.sub(r"\s+—\s+", " — ", item)
        item = item.strip()
        cleaned.append(item)
    return "\n\n".join(cleaned)


def normalize_whitespace(text: str) -> str:
    """Collapse horizontal whitespace and any 3+ newline runs down to \n\n."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)  # trim spaces around newlines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def postprocess_letter(text: str) -> str:
    """Full deterministic clean. Order matters: version numbers first (so the
    rest of the pipeline and the validator see canonical "X.Y.Z" rather than a
    spaced "3. 22. 2"), then meta/sig, then the years-opener guard (must run
    BEFORE stack-dump and weak-ending so they operate on the corrected
    opener), then stack-dump, then weak ending, then punctuation artefacts,
    then whitespace.

    All stages are PARAGRAPH-AWARE: they preserve \n\n boundaries so the
    final letter keeps its visual structure (greeting | body | second
    paragraph) rather than getting collapsed into one block.
    """
    if not text:
        return text
    initial_len = len(text)
    text = normalize_version_numbers(text)
    text = strip_meta_and_signature(text)
    text = strip_years_opener(text)
    text = strip_stack_dump(text)
    text = strip_weak_ending(text)
    text = normalize_punctuation(text)
    text = normalize_whitespace(text)
    logger.debug(
        "postprocess_letter: %d chars -> %d chars (delta %+d)",
        initial_len, len(text), len(text) - initial_len,
    )
    return text


def enforce_max_words(text: str, max_words: int) -> str:
    """Hard clamp the letter to at most `max_words` words (P4).

    Paragraph- and sentence-aware: keeps whole sentences (and whole
    paragraphs) until adding the next sentence would exceed `max_words`.
    This is the deterministic backstop for the soft length guidance in the
    prompt / validator — the LLM occasionally overshoots the word budget,
    and a letter that's too long reads worse than one trimmed at a clean
    sentence boundary.

    Behavior:
      - Counts words with the same _WORD_RE used elsewhere, so the count
        matches the validator's notion of "words".
      - Never splits a sentence mid-way: the first kept sentence is always
        retained whole, even if it alone exceeds the budget, so we never
        emit a dangling fragment or an empty letter.
      - Preserves \n\n paragraph boundaries for the kept content.
      - No-op when the text is already within budget or max_words <= 0.
    """
    if not text or max_words <= 0:
        return text
    if len(_WORD_RE.findall(text)) <= max_words:
        return text

    paragraphs = _PARAGRAPH_SPLIT_RE.split(text)
    kept_paragraphs: List[str] = []
    running = 0
    done = False
    for paragraph in paragraphs:
        if done:
            break
        stripped = paragraph.strip()
        if not stripped:
            continue
        kept_sentences: List[str] = []
        for sentence in _split_sentences(stripped):
            words_here = len(_WORD_RE.findall(sentence))
            if (kept_sentences or kept_paragraphs) and running + words_here > max_words:
                done = True
                break
            kept_sentences.append(sentence)
            running += words_here
        if kept_sentences:
            kept_paragraphs.append(" ".join(kept_sentences))
    if not kept_paragraphs:
        return text
    clamped = "\n\n".join(kept_paragraphs)
    logger.debug(
        "enforce_max_words: clamped letter to %d/%d words",
        len(_WORD_RE.findall(clamped)), max_words,
    )
    return clamped


__all__ = [
    "postprocess_letter",
    "enforce_max_words",
    "normalize_version_numbers",
    "strip_meta_and_signature",
    "strip_years_opener",
    "strip_stack_dump",
    "strip_weak_ending",
    "normalize_punctuation",
    "normalize_whitespace",
]
