# E18 · Journal history

**Shows:** past journal receipts: when, which session and project, whether the skill
wrote an entry, its confirmation line, and the Activity section (duration, files,
commands, tool counts).
**Appears in:** the Journal page (list and detail), the "View" action after a close.
**Already built:** receipts written to `~/.claude/smartpanel/journals/<ts>-<project>.md`.

---

### SP-1801 · BE · Machine-readable receipt header
Size S
New receipts start with YAML front matter: `time`, `session`, `title`, `project`,
`outcome`, `duration_s`, `files`, `commands`, `tools`. The Markdown body stays as it is.

### SP-1802 · BE · Receipt index
Size M · Depends on SP-1801, SP-0004
Read the journals folder (newest first, cached by mtime) into `journal_entries` in the
state. Old receipts without front matter are parsed best-effort from their headings;
anything unreadable is listed with "Couldn't read this receipt" instead of being skipped.

### SP-1803 · FE · Journal page
Size M · Depends on SP-2101, SP-1802
Left: entries (title, outcome badge "✓ JOURNALED" or "RECEIPT ONLY", `12:02 · tacomap ·
2h30m`). Right: project and time, title, the confirmation line as a quote (red edge when
failed), Activity as a definition list, and the file path. Buttons: **Open receipt** and
**Open JOURNAL/2026-11.md** (`xdg-open`).

### SP-1804 · FE · Journal empty state
Size S · Depends on SP-0009
"No journal entries yet. Close a session that has been open for more than 2 minutes and
smartPanel writes one here."

### SP-1805 · FE · Select an entry from outside
Size S · Depends on SP-1803
`OpenDashboard("journal:<id>")` opens the page with that entry selected (used by **View**
toasts and notifications).
