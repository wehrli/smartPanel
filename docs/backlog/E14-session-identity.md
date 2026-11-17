# E14 · Session titles & per-session cost

**Shows:** each session's title (the name Claude gave the conversation), its project,
whether a terminal is attached, and how much that session has cost.
**Appears in:** every session row and card, the HUD, the palette.
**Already built:** titles from `#{pane_title}` via `clean_title`, terminal-title sync,
the ⟳ refresh in the dashboard.

---

### SP-1401 · BE · Cost per session
Size M · Depends on SP-0801
Map a session to its transcript (`journal.find_transcript(cwd)`, newest `*.jsonl`) and
read that file's cost from the aggregator's per-file cache, without re-parsing.
- [ ] When several sessions share one cwd, the mapping is ambiguous: show the cost only
      for the newest and `None` for the others, rather than a wrong number
- [ ] Recomputed on the aggregator's tick, not on the 2-second session refresh

### SP-1402 · BE · Session registry from hooks (optional)
Size L
The roadmap item from CLAUDE.md: `SessionStart` / `SessionEnd` hooks write
`~/.claude/smartpanel/sessions.json` (tmux name ↔ session id ↔ transcript). This removes the
ambiguity in SP-1401 and enables journal-on-natural-exit.

### SP-1403 · FE · Show per-session cost
Size S · Depends on SP-1401
`$2.90` in row sub-lines and Cockpit meta; hidden (not `$0.00`) when unknown.

### SP-1404 · FE · Refresh title feedback
Size S · Depends on SP-1208
⟳ shows a small spinner next to the title while it re-reads tmux, then a toast "Title
re-read from tmux".

### SP-1405 · FE · Title fallback
Size S
Before Claude names the conversation, show the project name as the title everywhere
(rows, cards, HUD, terminal title), never an empty string or "Claude Code".
