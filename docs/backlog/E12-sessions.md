# E12 · Session list & activity

**Shows:** every managed Claude session (tmux `claude-*`), what state it's in (needs you,
running, idle, plus the panel's own starting and journaling), and what a running session
is doing.
**Appears in:** A6 strip and A1 count in the top bar, bar menu, Overview list and "Needs
you" card, Sessions page (Cockpit and Board), Sessions nav badge, HUD, palette.
**Already built:** `tmux.list_sessions`, `activity.classify_pane` / `annotate`,
`uiformat.session_label`, the 2-second refresh in `cli.py`.

---

### SP-1201 · BE · Sessions section in the panel state
Size M · Depends on SP-0004
One record per session: `name`, `title`, `project`, `cwd`, `attached`, `created`,
`activity`, plus panel-owned `phase` (`launching`, `journaling` with step, or none). The
2-second refresh in `cli.py` writes into the state instead of calling the indicator and
dashboard directly.
- [ ] The signature dedupe stays, so an unchanged list emits no `changed`

### SP-1202 · BE · "What it's doing" for running sessions
Size M
Pure `describe_running(pane_text) -> str | None` that pulls the tool line the TUI shows
(`Bash · npm test`, `Read · page.tsx`, `Waiting for 2 background agents`). Fixtures are
real captures in `tests/fixtures/panes/`.
- [ ] Returns `None` rather than guessing when nothing matches

### SP-1203 · BE · Panel-owned phases
Size S · Depends on SP-1201
`launching` (a session the panel just started and tmux doesn't list yet, with a 10 s
timeout) and `journaling` (with the current step from E16) are merged into the list, so
the UI never shows a session disappearing and reappearing.

### SP-1204 · BE · Display order
Size S · Depends on SP-1201
Pure sort: needs you, running, journaling, starting, idle; stable by creation time within
a group. Used by every surface so the order is the same everywhere.

### SP-1205 · FE · Session strip in the top bar (A6)
Size M · Depends on SP-0002, SP-1901
One block per session in display order: amber (needs you, with a small notch so it isn't
colour-only), blue (running), grey (idle), outlined (starting), purple (journaling).
At most 6 blocks, then `+3`. Hidden when there are no sessions or tmux is missing.

### SP-1206 · FE · Session count in the A1 numbers
Size S · Depends on SP-1901
Middle segment `4` or `4/1` (total / waiting). `!` when tmux is missing.

### SP-1207 · FE · Sessions in the bar menu
Size S · Depends on SP-2001
Section "Sessions · 4 · 1 waiting", the first 4 sessions in display order as
`❓ Fix swap alert hysteresis   1h02` (click to focus), then `+5 more…` (opens the Sessions
page). Starting and journaling rows are disabled.

### SP-1208 · FE · Overview session list
Size M · Depends on SP-0009
Boxed list rows: glyph, title (click to focus), sub-line `project · ● attached · $2.90`,
age, ⟳ (re-read the title) and ✕ (close). A journaling row replaces the age with
"⏳ Skill appends to JOURNAL/2026-11.md…" and disables ⟳ and ✕.

### SP-1209 · FE · "Needs you" card on Overview
Size S · Depends on SP-1301
Shown when at least one session is waiting: one row per waiting session with its title,
the question in quotes, and an **Answer** button that opens Sessions → Cockpit.

### SP-1210 · FE · Sessions page, Cockpit view (B2)
Size L · Depends on SP-2101, SP-0009
A card per session in display order. The header has glyph, title and state pill; the meta
line has project, age, cost and attached. Then by state:
- needs you: the prompt block and answer buttons (E13);
- running: a blinking dot and the tool line (SP-1202);
- journaling: the step checklist (E16);
- starting: a spinner and "Opening Terminator…";
- idle and running: **Focus**, **⟳ Title**, **Close…** buttons.
The toolbar has the Cockpit/Board switch, **Close all…** and **＋ New session**.

### SP-1211 · FE · Sessions page, Board view (B3)
Size L · Depends on SP-1210
Three columns (Needs you / Running / Idle, with starting sessions in Idle), each with a
count and "Nothing here" when empty. Cards show title, project, and cost or "4m waiting".
A drop zone under the board ("Drop a card here to journal & close it") lists the
sessions being journaled with their steps. Cards move between columns only when the
real state changes; dragging between columns does nothing.

### SP-1212 · FE · Sessions nav badge
Size S · Depends on SP-2103
Amber count of waiting sessions, red `!` when tmux is missing.

### SP-1213 · FE · Sessions on the HUD
Size S · Depends on SP-2302
Up to 6 rows (title, then the state word in its colour), then `+3 more`. "No sessions"
or "tmux not installed" otherwise.

### SP-1214 · FE · Many sessions
Size S · Depends on SP-1205, SP-1207, SP-1211, SP-1213
With 9+ sessions (demo "many"): strip shows `+N`, menu shows `+N more…`, board columns
scroll, HUD truncates. Nothing overflows or wraps the top bar.

### SP-1215 · QA · Activity fixtures for new states
Size S · Depends on SP-1202
Add real pane captures for: running with background agents, running a Bash tool,
AskUserQuestion box, folder-trust prompt. Re-run `test_activity.py` on all of them.
