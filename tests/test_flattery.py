from __future__ import annotations

import pytest

from smartpanel.claude_stats.flattery import (
    CHARS_PER_TOKEN,
    estimate_tokens,
    merge_labels,
    message_text,
    scan,
)


# --- detection ---------------------------------------------------------

@pytest.mark.parametrize("text,label", [
    ("You're absolutely right, that breaks on empty input.", "you're right"),
    ("You are correct.", "you're right"),
    ("That's exactly the right thing to settle first.", "you're right"),
    ("Spot on.", "you're right"),
    ("Good question — let me check.", "good question"),
    ("Excellent question, and the answer is short.", "good question"),
    ("Fair point.", "good point"),
    ("Good catch — the loop is off by one.", "good catch"),
    ("Nice find.", "good catch"),
    ("Good goal — that's worth settling.", "good idea"),
    ("Good instinct.", "good idea"),
    ("Smart move.", "good idea"),
    ("Nice work sticking with it.", "nice work"),
    ("Well done.", "nice work"),
    ("Perfect! Here is the patch.", "bare praise"),
    ("Thanks for the precise answer.", "appreciation"),
    ("Happy to help.", "appreciation"),
    ("Fair enough — I won't force it.", "fair enough"),
])
def test_detects_gratification(text: str, label: str):
    result = scan(text)
    assert result.phrases >= 1
    assert dict(result.by_label).get(label), f"{text!r} -> {result.by_label}"


@pytest.mark.parametrize("text", [
    "The parser drops the last line when the file has no trailing newline.",
    "If that's right, the migration can run unattended.",           # conditional
    "That's correct behaviour for an unassigned project.",           # about the code
    "The interesting question becomes why it only fails in CI.",     # definite article
    "Run `git rebase --onto` and check the right-hand column.",
    "I'll rewrite the handler and re-run the suite.",
    "",
    "   ",
])
def test_ignores_ordinary_prose(text: str):
    assert scan(text).phrases == 0
    assert not scan(text)


def test_ignores_praise_inside_code_fences():
    text = (
        "Here is the commit:\n"
        "```\n"
        "git commit -m 'Good catch: fix the off-by-one'\n"
        "```\n"
        "The suite passes now."
    )
    assert scan(text).phrases == 0


def test_ignores_praise_inside_quotations():
    text = 'The draft opens with "thanks for reaching out" before the pitch.'
    assert scan(text).phrases == 0


def test_ignores_inline_code():
    assert scan("The `good_question` helper returns None.").phrases == 0


# --- what gets charged -------------------------------------------------

def test_short_sentence_is_charged_whole():
    text = "Good question."
    assert scan(text).chars == len(text)


def test_long_sentence_is_charged_only_up_to_the_clause_break():
    lead = "You're absolutely right"
    rest = (" — the aggregator sums every content-block line, so each request's "
            "usage is counted once per block instead of once per message, and "
            "that roughly doubles every figure it reports.")
    result = scan(lead + rest)
    assert result.phrases == 1
    # The leading clause is charged; the substantive remainder is not.
    assert len(lead) <= result.chars < len(lead + rest)


def test_two_compliments_in_one_sentence_are_charged_once():
    text = "Good question — and you're absolutely right."
    result = scan(text)
    assert result.phrases == 2          # both habits are reported
    assert result.chars == len(text)    # but the sentence is only paid for once


def test_multiple_sentences_accumulate():
    one = scan("Good question.")
    two = scan("Good question.\nNice catch.")
    assert two.chars > one.chars
    assert two.phrases == 2


# --- token estimate ----------------------------------------------------

def test_estimate_tokens_uses_chars_per_token():
    assert estimate_tokens(0) == 0
    assert estimate_tokens(1) == 1                       # never rounds down to 0
    assert estimate_tokens(400) == int(400 / CHARS_PER_TOKEN)


def test_scan_tokens_follow_chars():
    result = scan("Good question — let me check that properly.")
    assert result.tokens == estimate_tokens(result.chars)


# --- helpers -----------------------------------------------------------

def test_message_text_keeps_only_text_blocks():
    content = [
        {"type": "thinking", "thinking": "Good question, but internal."},
        {"type": "text", "text": "Here is the answer."},
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
    ]
    assert message_text(content) == "Here is the answer."
    assert message_text("plain string") == "plain string"
    assert message_text(None) == ""


def test_merge_labels_sums_dicts_and_tuples():
    assert merge_labels([
        (("you're right", 2), ("good question", 1)),
        {"you're right": 3},
    ]) == {"you're right": 5, "good question": 1}
