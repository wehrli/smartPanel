# E26 · App states

**Shows:** what smartPanel is doing when it can't show normal data: reading transcripts for
the first time, a machine with no Claude usage yet, tmux missing, no sessions, no swap.
These are the prototype's scenarios: cold, first, notmux, empty, noswap.

---

### SP-2601 · BE · Cold-start progress
Size S · Depends on SP-0801
`app.loading = {done, total}` while the first aggregator scan runs, `None` after.

### SP-2602 · FE · Loading everywhere
Size M · Depends on SP-2601
Top bar `reading 812/1284` with a spinner (SP-1906). Overview: skeleton cards for Today
and Memory, while CPU and Swap already show (they don't depend on the scan). Spend: the
progress page (SP-1006). HUD: `…`. Menu: "Reading transcripts… 812/1284". Sidebar footer:
"Reading transcripts…". A toast "Read 1,284 transcripts" when it finishes.

### SP-2603 · BE · First-run detection
Size S
`app.first_run` is true when `~/.claude/projects` is missing or has no `*.jsonl`.

### SP-2604 · FE · First-run states
Size S · Depends on SP-2603
Bar spend `—`. Overview Today card: "No transcripts yet"; budget area: "Spend appears here
once Claude Code writes its first transcript." Spend page empty state (SP-1006). Journal
empty state (SP-1804).

### SP-2605 · BE · tmux and Terminator availability
Size S
`app.tmux_ok` (from `tmux.tmux_available`, rechecked every 30 s and on **Check again**) and
`app.terminal_ok` (the configured terminal is on PATH).

### SP-2606 · FE · tmux missing
Size S · Depends on SP-2605
Bar `!` in the sessions segment, sidebar red `!`, Sessions page error: "tmux isn't
installed", explanation, `sudo apt install tmux` with **Copy**, and **Check again**.
Overview session area shows a short version with **How to fix**. New session buttons
disabled.

### SP-2607 · FE · No sessions
Size S
Sessions page: "No Claude sessions running", what tmux sessions are and why, **＋ New
session**. Overview: a compact version. Board columns: "Nothing here". Menu: "No sessions".
HUD: "No sessions".

### SP-2608 · FE · No swap
Size S
Everywhere swap appears: `—`, "No swap configured", the "Not configured" chip, and no alert.
