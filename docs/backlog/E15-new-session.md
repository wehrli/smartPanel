# E15 · New session

**Shows:** the folders you can start a session in, the launch in progress, and the result.
**Appears in:** folder-picker dialog, launching dialog, a "Starting" row, toasts. Entry
points: bar menu, Sessions toolbar, Overview and Sessions empty states, palette.
**Already built:** `sessions/launch.py` (Terminator via `systemd-run --scope`,
`--no-dbus`), a GTK file chooser in `dashboard.new_session`.

---

### SP-1501 · BE · List start folders
Size S · Depends on SP-0007
Subfolders of the sessions folder (setting `sessions.dir`, default `~/dev/omdev`, env
`SMARTPANEL_SESSIONS_DIR` wins), sorted case-insensitively, without dot folders. Folders
that already have a running session are flagged.

### SP-1502 · BE · Structured launch result
Size S
`launch()` returns `LaunchResult` (`started`, `terminal_missing`, `tmux_missing`,
`failed` with message) instead of raising, so the UI can show the right message.
- [ ] Tests with a fake runner for each result

### SP-1503 · FE · Folder picker dialog
Size M · Depends on SP-2104
Title "New Claude session", a breadcrumb `~/dev/omdev /`, a two-column grid of folders
(the last-used one preselected), the hint "Opens Terminator running Claude inside tmux, in
~/dev/omdev/ledgerly", and **Cancel** / **Open**. Arrow keys and Enter work. A **Browse…**
link opens the system file chooser for folders outside the list.

### SP-1504 · FE · Launch progress
Size S · Depends on SP-1203
After Open: a short "Opening Terminator" dialog with a spinner. Then the session appears
as **Starting** (outlined block in the strip, spinner on its card) until tmux lists it.
After 10 s without it, the row is removed and an error toast explains.

### SP-1505 · FE · Launch results
Size S · Depends on SP-1502, SP-2502
Toast "Session started in ~/dev/omdev/ledgerly". Terminator missing: error toast "Couldn't
open Terminator. Install it, or set SMARTPANEL_TERMINAL." tmux missing: go to the
Sessions error page (E26).

### SP-1506 · FE · Entry points
Size S · Depends on SP-1503
Wire **New session** in the bar menu, the Sessions toolbar, both empty states and the
palette to the same dialog (opening the window first when needed).
