"""Detect *gratification* prose in assistant messages and size it in tokens.

Gratification = the sentences that praise the user instead of doing the work:
"Good question", "You're absolutely right", "Great goal — that's the right
thing". They cost output tokens, and every one of them is re-read as prompt on
every later turn of the same session. This module answers "how much am I paying
for that?".

Everything here is pure and GTK-free: `scan()` takes a string, returns counts.
`parser.py` calls it per assistant text block; `aggregator.py` prices the result.

Scope and honesty about what this measures:

- **Text blocks only.** Thinking blocks and tool-call JSON are skipped — the
  question is about what the assistant *says*.
- **Code and quotations are stripped** before matching, so a "good catch" in a
  commit message, or a drafted email's "thanks for reaching out", isn't billed
  as flattery.
- **The unit is the sentence, not the phrase.** Deleting flattery means
  deleting the whole sentence, so a short sentence containing a match is
  counted whole. A long sentence that merely *opens* with praise ("You're
  right — the parser drops the last line when…") carries real content, so only
  the leading clause is counted.
- **Tokens are estimated**, not measured: transcripts record `output_tokens`
  per message, never per sentence. `CHARS_PER_TOKEN` is the usual English
  approximation. Treat the result as an order of magnitude, not a bill.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Rough English chars-per-token. Used to turn a character span into a token
# estimate — see the module docstring on why this can't be exact.
CHARS_PER_TOKEN = 4.0

# A sentence at or under this length that contains a praise phrase is counted
# whole (it is the praise). Longer ones are trimmed to the leading clause.
SENTENCE_MAX_CHARS = 140
# Cap on how much of a long sentence's leading clause is attributed to praise.
CLAUSE_MAX_CHARS = 90

# (label, pattern). The label is the grouping key in the per-phrase breakdown,
# so several spellings of the same compliment collapse into one row.
#
# Every transcript line runs through these on the GLib main loop, and Python's
# `re` costs one full pass per pattern — so related phrasings are merged into
# one alternation rather than listed separately, and matching happens on
# pre-lowercased text (`re.IGNORECASE` is ~60% slower). Keep the list short.
_PATTERNS: tuple[tuple[str | None, str], ...] = (
    ("you're right",
     r"\byou(?:'re| are|'ve been| were)\s+(?:absolutely\s+|completely\s+|totally\s+|"
     r"quite\s+|100%\s+|so\s+|dead\s+)?(?:right|correct)\b"
     r"|\bthat(?:'s| is| was)\s+(?:exactly\s+)?the\s+right\b"
     # Bare "that's right" only when it *ends* the clause — "that's correct
     # behaviour" is a statement about the code, not a compliment.
     r"|\bthat(?:'s| is| was)\s+(?:exactly\s+)?(?:right|correct)\b"
     r"(?=\s*[.,!?;:—–-]|\s+that\b|\s*$)"
     r"|\b(?:spot on|exactly right|precisely right)\b"),
    # Adjective + noun. The noun picks the label — see _LABEL_BY_NOUN.
    (None,
     r"\b(?:great|good|excellent|fair|interesting|smart|sharp|nice|clever|solid|"
     r"valid|strong|impressive|well)\s+"
     r"(?P<noun>question|point|catch|spot|eye|find|idea|instinct|thinking|call|"
     r"goal|plan|approach|work|move|choice|done)\b"),
    ("nice work", r"\byou(?:'ve| have)\s+nailed\b|\bnailed it\b|\bhit the nail\b"),
    ("bare praise",
     r"(?:^|(?<=[.!?\n]\s))(?:absolutely|perfect|excellent|great|awesome|"
     r"wonderful|fantastic|brilliant|exactly|indeed|nice)\s*[!.,—-]"),
    ("appreciation",
     r"\b(?:thanks for|thank you for|i appreciate|happy to help|glad (?:to help|"
     r"you asked|i could)|my pleasure)\b"),
    ("fair enough", r"\bfair enough\b"),
)

# Which compliment a matched noun belongs to.
_LABEL_BY_NOUN: dict[str, str] = {
    "question": "good question",
    "point": "good point",
    "catch": "good catch", "spot": "good catch",
    "eye": "good catch", "find": "good catch",
    "idea": "good idea", "instinct": "good idea", "thinking": "good idea",
    "call": "good idea", "goal": "good idea", "plan": "good idea",
    "approach": "good idea", "move": "good idea", "choice": "good idea",
    "work": "nice work", "done": "nice work",
}

_COMPILED: tuple[tuple[str | None, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pat)) for label, pat in _PATTERNS
)

# Fenced code blocks (``` … ```), inline code spans, and short quotations —
# blanked before matching. Quoted prose is the assistant drafting or citing
# text ('a "thanks for reaching out" opener'), not complimenting the user.
_FENCE_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_QUOTE_RE = re.compile(r"[\"“][^\"”\n]{1,120}[\"”]")

# A match whose immediately preceding word is one of these isn't a compliment:
# a conditional agrees with nobody ("if that's right, …"), and a definite
# article points at a thing rather than praising it ("the interesting question
# becomes why now" vs "that's an interesting question").
_SKIP_PREFIXES = frozenset({
    "if", "whether", "unless", "when", "once", "assuming", "provided",
    "suppose", "though", "although", "until", "iff", "the",
})
_LAST_WORD_RE = re.compile(r"([A-Za-z']+)\W*$")
# Sentence terminator followed by whitespace, or a newline.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
# Clause boundaries used to trim a long sentence down to its praise opener.
_CLAUSE_CHARS = ",;:—–-"


@dataclass(frozen=True, slots=True)
class PraiseScan:
    """Result of scanning one assistant text block."""

    phrases: int = 0                 # pattern matches (may exceed `spans`)
    chars: int = 0                   # characters attributed to gratification
    by_label: tuple[tuple[str, int], ...] = ()

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.chars)

    def __bool__(self) -> bool:
        return self.phrases > 0


@dataclass(slots=True)
class _Acc:
    phrases: int = 0
    chars: int = 0
    labels: dict[str, int] = field(default_factory=dict)


def estimate_tokens(chars: int) -> int:
    """Characters → estimated tokens. Rounds up; 0 chars stays 0."""
    if chars <= 0:
        return 0
    return max(1, int(round(chars / CHARS_PER_TOKEN)))


def scan(text: str) -> PraiseScan:
    """Find gratification in one assistant text block.

    `chars` counts the merged spans (so a sentence holding two compliments is
    charged once), while `phrases` counts every match — the breakdown is about
    *which* compliments recur, the char total is about what deleting them saves.
    """
    if not text or not text.strip():
        return PraiseScan()

    # Everything downstream works on this lowered copy, including the character
    # counts: patterns are lowercase-only for speed, and `.lower()` can shift
    # offsets for a few exotic characters.
    clean = _strip_code(text).lower()
    spans: list[tuple[int, int]] = []
    acc = _Acc()

    for label, rx in _COMPILED:
        for m in rx.finditer(clean):
            if _skip_prefix(clean, m.start()):
                continue
            name = label or _LABEL_BY_NOUN.get(m.group("noun"), "bare praise")
            acc.phrases += 1
            acc.labels[name] = acc.labels.get(name, 0) + 1
            spans.append(_attributed_span(clean, m.start(), m.end()))

    if not acc.phrases:
        return PraiseScan()

    acc.chars = sum(e - s for s, e in _merge(spans))
    by_label = tuple(sorted(acc.labels.items(), key=lambda kv: (-kv[1], kv[0])))
    return PraiseScan(phrases=acc.phrases, chars=acc.chars, by_label=by_label)


def _strip_code(text: str) -> str:
    """Blank out code and quotations, preserving offsets so spans still line up."""
    def blank(m: re.Match[str]) -> str:
        return " " * (m.end() - m.start())

    out = _FENCE_RE.sub(blank, text)
    out = _INLINE_CODE_RE.sub(blank, out)
    return _QUOTE_RE.sub(blank, out)


def _skip_prefix(text: str, start: int) -> bool:
    """True when the word right before a match disqualifies it as praise.

    `text` is already lowercased by `scan`.
    """
    m = _LAST_WORD_RE.search(text[max(0, start - 24):start])
    return bool(m) and m.group(1) in _SKIP_PREFIXES


def _attributed_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Widen a match to the text that would disappear if the praise were cut."""
    s_start, s_end = _sentence_bounds(text, start)
    if s_end - s_start <= SENTENCE_MAX_CHARS:
        return s_start, s_end
    # Long sentence: it carries content past the compliment. Charge only the
    # leading clause, up to the first boundary at or after the match.
    cut = s_end
    for i in range(end, min(s_end, s_start + CLAUSE_MAX_CHARS)):
        if text[i] in _CLAUSE_CHARS:
            cut = i + 1
            break
    else:
        cut = min(s_end, s_start + CLAUSE_MAX_CHARS)
    return s_start, max(cut, end)


def _sentence_bounds(text: str, pos: int) -> tuple[int, int]:
    start = 0
    for m in _SENTENCE_SPLIT_RE.finditer(text):
        if m.end() > pos:
            return start, m.start()
        start = m.end()
    return start, len(text)


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def merge_labels(chunks) -> dict[str, int]:
    """Sum several `by_label` tuples/dicts into one counter."""
    total: dict[str, int] = {}
    for chunk in chunks:
        items = chunk.items() if isinstance(chunk, dict) else chunk
        for label, n in items:
            total[label] = total.get(label, 0) + n
    return total


def message_text(content) -> str:
    """Join the `text` blocks of a transcript message's content.

    Handles both shapes Claude Code writes: a plain string, or a list of
    content blocks. Thinking and tool_use blocks are deliberately excluded.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [
        b.get("text") or ""
        for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    return "\n".join(p for p in parts if p)
