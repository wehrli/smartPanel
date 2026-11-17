from __future__ import annotations

import pytest

from smartpanel.sessions import prompt as P
from smartpanel.sessions.prompt import AnswerResult, answer, fingerprint, parse_prompt
from tests.panes import pane

# ───────── parsing (SP-1301) ─────────


def test_file_edit_permission():
    p = parse_prompt(pane("edit_permission"))
    assert p.question == "Do you want to make this edit to alerts.py?"
    assert p.options == ["Yes", "Yes, allow all edits during this session",
                         "No, and tell Claude what to do differently"]
    assert p.cursor == 0
    assert p.kind == "permission"


def test_bash_permission_joins_a_wrapped_option():
    p = parse_prompt(pane("bash_permission"))
    assert p.question == "Do you want to proceed?"
    assert p.options[1] == ("Yes, and don't ask again for npm run build commands in "
                            "/home/user/dev/omdev/tacomap")
    assert len(p.options) == 3
    assert p.kind == "permission"


def test_ask_user_question_with_three_options():
    p = parse_prompt(pane("question_3"))
    assert p.kind == "question"
    assert p.question == "Which migration strategy should I use for 0023?"
    # Descriptions are not labels; "Type something." / "Chat about this" need
    # the terminal and are not offered.
    assert p.options == ["Down-migrate 0022 first", "Apply on top of the current schema",
                         "Skip 0023 for now"]


def test_ask_user_question_with_four_options_and_a_wrapped_question():
    p = parse_prompt(pane("question_4"))
    assert p.kind == "question"
    assert p.question == ("The cache currently keeps every response. "
                          "How aggressive should eviction be?")
    assert len(p.options) == 4
    assert p.options[2] == "Evict by size (LRU, 512 MB cap)"


def test_trust_prompt_numbered():
    p = parse_prompt(pane("trust"))
    assert p.question == "Quick safety check: Is this a project you created or one you trust?"
    assert p.options == ["Yes, I trust this folder", "No, exit"]


def test_trust_prompt_without_numbers():
    p = parse_prompt(pane("trust_unnumbered"))
    assert p.options == ["No, exit", "Yes, I trust this folder"]
    assert p.cursor == 0


def test_cursor_on_a_later_option():
    text = pane("edit_permission").replace(" ❯ 1. Yes", "   1. Yes").replace(
        "   2. Yes, allow", " ❯ 2. Yes, allow")
    assert parse_prompt(text).cursor == 1


@pytest.mark.parametrize("name", ["user_numbered", "user_numbered_draft",
                                  "idle_prose_question", "running_bash",
                                  "running_agents"])
def test_not_a_prompt(name):
    assert parse_prompt(pane(name)) is None


def test_empty_is_none():
    assert parse_prompt("") is None


# ───────── fingerprint (SP-1302) ─────────


def test_fingerprint_is_stable_and_ignores_the_cursor():
    a = parse_prompt(pane("edit_permission"))
    moved = pane("edit_permission").replace(" ❯ 1. Yes", "   1. Yes").replace(
        "   2. Yes, allow", " ❯ 2. Yes, allow")
    assert parse_prompt(moved).fingerprint == a.fingerprint
    assert a.fingerprint == fingerprint(a.question, a.options, a.kind)


def test_fingerprint_changes_with_the_question_or_options():
    a = parse_prompt(pane("edit_permission"))
    other_file = pane("edit_permission").replace("edit to alerts.py?", "edit to tmux.py?")
    assert parse_prompt(other_file).fingerprint != a.fingerprint
    assert fingerprint("q", ["a", "b"]) != fingerprint("q", ["a", "c"])
    assert fingerprint("q", ["a b"]) != fingerprint("q", ["a", "b"])


# ───────── answering (SP-1303 / SP-1308) ─────────


class FakeTmux:
    """Runner standing in for tmux: serves pane captures in order, records keys."""

    def __init__(self, *panes: str, send_rc: int = 0):
        self.panes = list(panes)
        self.sent: list[list[str]] = []
        self.send_rc = send_rc

    def __call__(self, args):
        if args[0] == "capture-pane":
            text = self.panes.pop(0) if len(self.panes) > 1 else self.panes[0]
            return (0, text, "") if text is not None else (1, "", "can't find session")
        if args[0] == "send-keys":
            self.sent.append(args)
            return self.send_rc, "", ""
        raise AssertionError(f"unexpected tmux call {args}")


def _answer(run, option, fp, **kw):
    clock = iter(range(1000))
    return answer("claude-x", option, fp, run=run, sleep=lambda _s: None,
                  clock=lambda: next(clock), **kw)


def test_answer_sends_the_digit_and_confirms():
    fp = parse_prompt(pane("edit_permission")).fingerprint
    run = FakeTmux(pane("edit_permission"), pane("running_bash"))
    assert _answer(run, 1, fp) is AnswerResult.SENT
    assert run.sent == [["send-keys", "-t", "=claude-x:", "-l", "2"]]


def test_answer_unnumbered_uses_arrows_and_enter():
    fp = parse_prompt(pane("trust_unnumbered")).fingerprint
    run = FakeTmux(pane("trust_unnumbered"), pane("idle_prose_question"))
    assert _answer(run, 1, fp) is AnswerResult.SENT
    assert run.sent == [["send-keys", "-t", "=claude-x:", "Down", "Enter"]]


def test_answer_unnumbered_on_the_cursor_is_just_enter():
    fp = parse_prompt(pane("trust_unnumbered")).fingerprint
    run = FakeTmux(pane("trust_unnumbered"), "")
    assert _answer(run, 0, fp) is AnswerResult.SENT
    assert run.sent == [["send-keys", "-t", "=claude-x:", "Enter"]]


def test_answer_fails_when_the_prompt_stays_up():
    fp = parse_prompt(pane("edit_permission")).fingerprint
    run = FakeTmux(pane("edit_permission"))
    assert _answer(run, 0, fp) is AnswerResult.FAILED
    assert len(run.sent) == 1


def test_answer_fails_when_tmux_refuses_the_keys():
    fp = parse_prompt(pane("edit_permission")).fingerprint
    run = FakeTmux(pane("edit_permission"), send_rc=1)
    assert _answer(run, 0, fp) is AnswerResult.FAILED


@pytest.mark.parametrize("capture,expected", [
    ("idle_prose_question", AnswerResult.NOT_WAITING),
    ("running_bash", AnswerResult.NOT_WAITING),
    ("user_numbered", AnswerResult.NOT_WAITING),
    ("bash_permission", AnswerResult.PROMPT_CHANGED),   # a different prompt is up
    (None, AnswerResult.NOT_WAITING),                   # the pane vanished
])
def test_never_types_into_the_wrong_state(capture, expected):
    fp = parse_prompt(pane("edit_permission")).fingerprint
    run = FakeTmux(pane(capture) if capture else None)
    assert _answer(run, 0, fp) is expected
    assert run.sent == []  # zero send-keys


def test_out_of_range_option_sends_nothing():
    fp = parse_prompt(pane("edit_permission")).fingerprint
    run = FakeTmux(pane("edit_permission"))
    assert _answer(run, 7, fp) is AnswerResult.FAILED
    assert _answer(run, -1, fp) is AnswerResult.FAILED
    assert run.sent == []


def test_answer_never_raises():
    def boom(_args):
        raise RuntimeError("tmux exploded")
    assert _answer(boom, 0, "x") is AnswerResult.FAILED


def test_result_values_are_the_wire_strings():
    assert [r.value for r in AnswerResult] == ["sent", "prompt_changed", "not_waiting", "failed"]
    assert P.CONFIRM_S == 2.0
