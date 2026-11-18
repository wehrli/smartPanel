from __future__ import annotations

from smartpanel.sessions import activity
from smartpanel.sessions.activity import IDLE, RUNNING, UNKNOWN, WAITING, classify_pane
from smartpanel.sessions.tmux import TmuxSession

# Captured from real Claude Code panes on the dev box (tmux capture-pane -p).

IDLE_PANE = """\
  make test                    # run the suite

  For the release, ask me to "run the changelog step" — I need your opt-in
  each time, which is the guardrail working as intended.

───────────────────────────────────────────────────────────────────────────────
❯
───────────────────────────────────────────────────────────────────────────────
  ⏵⏵ auto mode on (shift+tab to cycle) · ← 2 agents
"""

RUNNING_PANE = """\
● I'll start by exploring the relevant code.

● Capturing pane content of running session
  ⎿  $ tmux capture-pane -p -t '=claude-smartPanel:'

· Spinning… (33s · ↑ 1.9k tokens · thought for 1s)

───────────────────────────────────────────────────────────────────────────────
❯
───────────────────────────────────────────────────────────────────────────────
  ⏵⏵ auto mode on (shift+tab to cycle) · esc to interrupt · ← 2 agents
"""

BACKGROUND_AGENT_PANE = """\
  Want me to commit those two files? And I'll report back when it lands.
✻ Waiting for 1 background agent to finish
───────────────────────────────────────────────────────────────────────────────
❯
───────────────────────────────────────────────────────────────────────────────
  ⏵⏵ auto mode on (shift+tab to cycle) · ← 2 agents · ↓ to manage
  ● main
  ◯ general-purpose  Reading setup-database.sh for style       3m 47s · ↓ 86.1k
"""

PERMISSION_PANE = """\
● Bash command

╭──────────────────────────────────────────────────────────────────────────────╮
│ Bash command                                                                 │
│                                                                              │
│   rm -rf build/                                                              │
│   Remove the stale build directory                                           │
│                                                                              │
│ Do you want to proceed?                                                      │
│ ❯ 1. Yes                                                                     │
│   2. Yes, and don't ask again for rm commands in /home/user/dev              │
│   3. No, and tell Claude what to do differently (esc)                        │
╰──────────────────────────────────────────────────────────────────────────────╯
"""

# Real capture: the folder-trust prompt, whose options carry no numbers at all.
TRUST_PANE = """\
────────────────────────────────────────────────────────────────────────────────
 Accessing workspace:
 /tmp/scratch/trustdir

 Quick safety check: Is this a project you created or one you trust?
 Claude Code'll be able to read, edit, and execute files here.

 ❯ No, exit
   Yes, I trust this folder

 Enter to confirm · Esc to cancel
"""

# Real capture: an AskUserQuestion box. The footer wording differs, and the
# cursor sits a dozen lines above it — which is why the cursor is looked for
# across the whole tail rather than just above the footer.
QUESTION_PANE = """\
←  ☐ Cache scope  ☐ Expiry  ☐ Commit  ✔ Submit  →

The cache currently keeps every response. How aggressive should eviction be?

❯ 1. Evict by age, keep pinned entries (Recommended)
     entries.body -> dropped after 24h, entries.meta -> kept (cheap to hold
     and needed by the index). pinned entries must stay for the offline mode
     to work, so the hot set is already bounded.
  2. Evict pinned entries too
     Everything above plus pinned entries after 7 days. Simpler as a rule,
     but the offline mode would then miss the same entries.
  3. Leave the cache as specced
     Ship what's implemented and tested now. Memory is not a problem yet.
  4. Type something.
────────────────────────────────────────────────────────────────────────────────
  5. Chat about this

Enter to select · Tab/Arrow keys to navigate · Esc to cancel
"""

PLAN_APPROVAL_PANE = """\
│ Ready to code?                                                               │
│                                                                              │
│ ❯ 1. Yes, and auto-accept edits                                              │
│   2. Yes, and manually approve edits                                         │
│   3. No, keep planning                                                       │
╰──────────────────────────────────────────────────────────────────────────────╯
"""


def test_idle_pane_is_idle():
    assert classify_pane(IDLE_PANE) == IDLE


def test_running_pane_detected_from_the_interrupt_hint():
    assert classify_pane(RUNNING_PANE) == RUNNING


def test_background_agents_count_as_running():
    # The main turn is done, but something *is* still running — which is the
    # question the glyph answers.
    assert classify_pane(BACKGROUND_AGENT_PANE) == RUNNING


def test_permission_prompt_is_waiting():
    assert classify_pane(PERMISSION_PANE) == WAITING


def test_plan_approval_is_waiting():
    assert classify_pane(PLAN_APPROVAL_PANE) == WAITING


def test_unnumbered_trust_prompt_is_waiting():
    # Caught by the modal footer alone — this one has no numbered options, so
    # the structural rule can't see it.
    assert classify_pane(TRUST_PANE) == WAITING


def test_question_box_is_waiting():
    assert classify_pane(QUESTION_PANE) == WAITING


def test_quoted_modal_footer_in_output_is_not_waiting():
    # A session printing another session's screen (exactly how this classifier
    # was developed) must not read as blocked on a prompt: the words are there,
    # but there is no selection cursor — the input box's ❯ carries no text.
    pane = IDLE_PANE.replace(
        "❯\n", "  Enter to confirm · Esc to cancel\n\n❯\n")
    assert classify_pane(pane) == IDLE


def test_waiting_wins_over_running():
    # A choice box can be on screen while the footer still offers "esc to
    # interrupt"; needing the user is the more actionable of the two.
    assert classify_pane(PERMISSION_PANE + RUNNING_PANE.splitlines()[-1]) == WAITING


def test_prose_asking_a_question_is_not_waiting():
    # The regression that killed the phrase-based first cut: an idle session
    # whose last line reads like a prompt.
    pane = IDLE_PANE.replace(
        "❯\n", "❯\n  Do you want me to commit those two files?\n")
    assert classify_pane(pane) == IDLE


def test_typed_text_starting_with_a_number_is_not_waiting():
    # "❯ 1. something" at the input line has a cursor but no sibling options.
    pane = IDLE_PANE.replace("❯\n", "❯ 1. fix the parser first\n")
    assert classify_pane(pane) == IDLE


def test_stale_options_scrolled_out_of_the_tail_are_ignored():
    pane = PERMISSION_PANE + "\n" * activity.TAIL_LINES + IDLE_PANE
    assert classify_pane(pane) == IDLE


def test_empty_capture_is_unknown():
    assert classify_pane("") == UNKNOWN
    assert classify_pane("   \n  \n") == UNKNOWN


def test_activity_of_uses_capture_pane():
    calls = []

    def fake_run(args):
        calls.append(args)
        return 0, PERMISSION_PANE, ""

    assert activity.activity_of("claude-x", run=fake_run) == WAITING
    assert calls == [["capture-pane", "-p", "-t", "=claude-x:"]]


def test_activity_of_degrades_when_tmux_fails():
    assert activity.activity_of("gone", run=lambda a: (1, "", "no such")) == UNKNOWN


def test_counts_tallies_every_state():
    def s(name, state):
        return TmuxSession(name, 0, False, 1, activity=state)

    tally = activity.counts([s("a", WAITING), s("b", RUNNING), s("c", RUNNING),
                             s("d", IDLE)])
    assert tally[WAITING] == 1
    assert tally[RUNNING] == 2
    assert tally[IDLE] == 1
    assert tally[UNKNOWN] == 0
