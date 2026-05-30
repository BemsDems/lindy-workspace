"""Deterministic letter post-processing — model-agnostic form guarantees.

Rationale: prompts only *ask* the model to behave; weak models ignore them.
This module *enforces* the parts of the contract that are purely structural
(stack dumps, weak endings, signatures, stray meta-lines) so the output
form does not depend on how obedient the LLM is.

It runs BEFORE the validator: clean first, then validate, so the validator
doesn't burn a retry on something we would have removed anyway.

It only removes/trims — it never invents content. Anything semantic
(causal endings, natural tone) stays the model's job; the validator can
still reject those, but form-level junk never reaches it.
"""

from __future__ import annotations

import re
from typing import List


# Sentence-initial markers that introduce a tech-stack dump.
# "стек:" is an unambiguous dump lead — drop the whole sentence on match.
_STACK_LEAD_RE = re.compile(
    r"(?:^|(?<=[.!?]\s))\s*"
    r"(?:стек|технический\s+стек|тех\.?\s*стек|стек\s+технологий)\s*:?\s*",
    re.IGNORECASE,
)

# Verb leads ("работал с", "использовал") only count as a dump when followed
# by an actual list (handled via _STACK_LIST_RE below), so inline mentions
# like "использовал gRPC-интеграцию" are preserved.
_VERB_LEAD_RE = re.compile(
    r"(?:^|(?<=[.!?]\s))\s*"
    r"(?:работал(?:а)?\s+с|использовал(?:а)?(?:\s+следующие)?(?:\s+технологии)?)\s*:?\s*",
    re.IGNORECASE,
)

# A run that looks like a comma/slash-separated list of >=5 capitalized
# tech tokens — a stack dump even without a "Стек:" lead. Kept as a fast
# pre-check; the density detector below is the robust path.
_TECH_TOKEN = r"[A-Z][A-Za-z0-9./+]*(?:\s[A-Z][A-Za-z0-9./+]*)?"
_STACK_LIST_RE = re.compile(
    rf"(?:{_TECH_TOKEN})(?:\s*[,/]\s*(?:{_TECH_TOKEN})){{4,}}\.?"
)

# Known tech vocabulary (reused from the validator — single source of truth)
# plus a few multi-word/compound forms common in these letters. Used to count
# how "tech-dense" a sentence is, which catches dumps that the comma-list regex
# misses (slashes, spaces, "и" before the last item, e.g.
# "Clean Architecture, BLoC/Cubit, Dio, REST API, gRPC, Protobuf и Drift").
try:  # avoid hard import cycle at module load
    from .validator import BASE_ALLOWED_TECH as _VALIDATOR_TECH
except Exception:  # pragma: no cover
    _VALIDATOR_TECH = set()

_EXTRA_TECH = {
    "Dio", "Drift", "Sentry", "Protobuf", "Freezed", "Branch", "MapKit",
    "Injectable", "GetIt", "Isar", "Hive", "Riverpod", "Provider",
}
_KNOWN_TECH_LOWER = {t.lower() for t in (_VALIDATOR_TECH | _EXTRA_TECH)}

# Multi-word tech terms that should count as a single tech token in a run.
_MULTIWORD_TECH = [
    "clean architecture", "rest api", "secure storage", "auto route",
    "deep links", "branch sdk", "yandex mapkit", "ci/cd",
]

# Connectors allowed *between* tech tokens inside a stack run.
_RUN_CONNECTOR_RE = re.compile(r"^\s*(?:,|/|;|—|-|\bи\b|\bи\s+|\bдо\b|\bот\b)\s*", re.IGNORECASE)


def _longest_tech_run(sentence: str) -> int:
    """Length of the longest chain of tech tokens separated only by connectors.

    A stack dump reads "A, B, C, D и E" — tech tokens glued by commas/slashes/
    "и". Real prose breaks the chain with verbs/nouns ("интегрировал Deep Links
    и Branch SDK для навигации"), so its longest run stays short (<=2-3).
    """
    s = sentence
    # Mask multi-word tech as single tokens so "Clean Architecture" counts once.
    low = s.lower()
    for mw in _MULTIWORD_TECH:
        idx = low.find(mw)
        while idx != -1:
            s = s[:idx] + ("X" * len(mw)) + s[idx + len(mw):]
            low = s.lower()
            idx = low.find(mw, idx + len(mw))
    tokens = re.findall(r"[^\s,/;—-]+|[,/;—-]", s)

    best = cur = 0
    expect_token = True
    for tok in tokens:
        if expect_token:
            head = tok.split("/")[0].lower().strip("().")
            is_tech = (
                tok.startswith("X" * 4)  # masked multi-word tech
                or head in _KNOWN_TECH_LOWER
            )
            if is_tech:
                cur += 1
                best = max(best, cur)
                expect_token = False
            else:
                cur = 0
                expect_token = True
        else:
            # Between tokens we must see a connector to continue the run.
            if tok in (",", "/", ";", "—", "-") or tok.lower() == "и":
                expect_token = True
            else:
                cur = 0
                expect_token = True
    return best


def _is_stack_dump_sentence(sentence: str) -> bool:
    """True if the sentence contains a long uninterrupted run of tech tokens
    (a stack dump), e.g. 'Clean Architecture, BLoC/Cubit, Dio, REST API, gRPC,
    Protobuf и Drift'. Prose with a couple of inline technologies has a short
    run and is preserved.
    """
    return _longest_tech_run(sentence) >= 5

# Weak closing openers — a sentence starting with any of these is filler.
_WEAK_ENDING_RE = re.compile(
    r"(?:^|(?<=[.!?]\s))\s*"
    r"(?:готов(?:а)?|буду\s+рад(?:а)?|хотел(?:а)?\s+бы|надеюсь|рассчитываю)\b[^.!?]*[.!?]",
    re.IGNORECASE,
)

# Meta / reasoning leakage lines.
_META_LEAD_RE = re.compile(
    r"^\s*(?:вот|итак|конечно|разумеется)\b[\s,]*",
    re.IGNORECASE,
)
_META_FULL_LINE_RE = re.compile(
    r"^\s*(?:вот\s+)?(?:готовое\s+)?(?:письмо|ответ|черновик|текст|план|структура)"
    r"(?:\s+для\s+вас|\s+ниже)?\s*:?\s*$",
    re.IGNORECASE,
)

_SIGNATURE_RE = re.compile(r"^\s*с\s+уважением\b", re.IGNORECASE)

_WORD_RE = re.compile(r"[\w’'-]+", re.UNICODE)


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


_DECIMAL_RE = re.compile(r"(\d)\.(\d)")
_DECIMAL_SENTINEL = "\u0001"


def _split_sentences(paragraph: str) -> List[str]:
    """Split into sentences keeping their trailing punctuation.

    Decimals like '99.5' are masked first so the '.' isn't treated as a
    sentence boundary, then restored.
    """
    masked = _DECIMAL_RE.sub(rf"\1{_DECIMAL_SENTINEL}\2", paragraph)
    parts = re.findall(r"[^.!?]*[.!?]+|\S[^.!?]*$", masked)
    return [p.replace(_DECIMAL_SENTINEL, ".").strip() for p in parts if p.strip()]


def strip_stack_dump(text: str) -> str:
    """Remove sentences that are tech-stack enumerations.

    Three checks per sentence:
      1. explicit "Стек:" lead  -> drop sentence,
      2. comma/slash list regex -> drop or excise the list fragment,
      3. tech-density heuristic  -> drop sentence (catches verb-led dumps
         with slashes/spaces/"и" that the regex misses).
    Inline mentions ("использовал gRPC-интеграцию") have low density and are kept.
    """
    out_paragraphs: List[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        kept: List[str] = []
        for sent in _split_sentences(paragraph):
            # 1. Explicit "Стек:" lead — always a dump, drop the sentence.
            if _STACK_LEAD_RE.match(sent):
                continue
            # 3. Density heuristic — a sentence packed with technologies is a
            # dump regardless of how it's punctuated. Checked before the regex
            # because it's the robust path.
            if _is_stack_dump_sentence(sent):
                continue
            # 2. Fallback: explicit comma/slash list of capitalized tokens.
            m = _STACK_LIST_RE.search(sent)
            has_list = bool(m and (m.group(0).count(",") + m.group(0).count("/")) >= 4)
            if _VERB_LEAD_RE.match(sent) and has_list:
                continue
            if has_list:
                if len(m.group(0)) >= 0.6 * len(sent):
                    continue
                sent = (sent[: m.start()] + sent[m.end():]).strip(" ,;—-")
                sent = re.sub(r"\(\s*\)", "", sent)
                sent = re.sub(r"\s+([,;.])", r"\1", sent)
                sent = re.sub(r"([,;])\s*([,;])", r"\1", sent).strip(" ,;—-")
                if not sent:
                    continue
            kept.append(sent)
        if kept:
            out_paragraphs.append(" ".join(kept))
    return "\n\n".join(out_paragraphs)


def strip_weak_ending(text: str) -> str:
    """Remove a trailing weak/filler sentence ('Готов применить…')."""
    paragraphs = re.split(r"\n\s*\n", text)
    if not paragraphs:
        return text
    last = paragraphs[-1]
    sents = _split_sentences(last)
    while sents and _WEAK_ENDING_RE.match(sents[-1]):
        sents.pop()
    paragraphs[-1] = " ".join(sents)
    paragraphs = [p for p in paragraphs if p.strip()]
    return "\n\n".join(paragraphs)


def strip_meta_and_signature(text: str) -> str:
    """Drop leading meta lines and any 'С уважением…' block onward."""
    lines = text.splitlines()
    # Cut signature block (from the first matching line to the end).
    for idx, line in enumerate(lines):
        if _SIGNATURE_RE.match(line.strip()):
            lines = lines[:idx]
            break
    # Drop leading blank lines and standalone meta lines ("Вот письмо для вас:").
    while lines:
        head = lines[0].strip()
        if not head:
            lines.pop(0)
            continue
        if _META_FULL_LINE_RE.match(head):
            lines.pop(0)
            continue
        # Strip an inline lead ("Вот, ...") but keep the rest of the line.
        cleaned = _META_LEAD_RE.sub("", head)
        if cleaned != head and cleaned:
            lines[0] = cleaned
        break
    return "\n".join(lines).strip()


def normalize_punctuation(text: str) -> str:
    """Fix punctuation artifacts left by fragment removal.

    Handles: double dashes ("—  —"), space-before-comma/period, doubled
    commas, empty parens, and stray leading/trailing separators per line.
    """
    # Empty parentheses left after excising a list inside them.
    text = re.sub(r"\(\s*\)", "", text)
    # Collapse repeated dashes (possibly space-separated) into one.
    text = re.sub(r"(?:\s*[—–-]\s*){2,}", " — ", text)
    # Space before closing punctuation.
    text = re.sub(r"\s+([,;.!?])", r"\1", text)
    # Doubled commas/semicolons.
    text = re.sub(r"([,;])\s*[,;]+", r"\1", text)
    # Collapse multiple spaces.
    text = re.sub(r"[ \t]{2,}", " ", text)
    # A dash immediately before end-of-sentence punctuation is orphaned.
    text = re.sub(r"\s*—\s*([.!?])", r"\1", text)
    # Final pass: collapse any double spaces introduced by removals above.
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def normalize_whitespace(text: str) -> str:
    """Collapse 3+ newlines to a paragraph break, trim trailing spaces."""
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def postprocess_letter(text: str) -> str:
    """Full deterministic clean. Order matters: meta/sig first, then stack,
    then weak ending, then punctuation artifacts, then whitespace.
    """
    if not text:
        return text
    text = strip_meta_and_signature(text)
    text = strip_stack_dump(text)
    text = strip_weak_ending(text)
    text = normalize_punctuation(text)
    text = normalize_whitespace(text)
    return text


__all__ = [
    "postprocess_letter",
    "strip_stack_dump",
    "strip_weak_ending",
    "strip_meta_and_signature",
    "normalize_punctuation",
    "normalize_whitespace",
]
