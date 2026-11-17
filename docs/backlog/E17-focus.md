# E17 · Focus

**Shows:** which terminal came forward when you click a session, and how (raised in
place, or re-attached in a new window).
**Appears in:** toasts; the "● attached" marker on rows.
**Already built:** `sessions/focus.py` ladder (custom command, Window Calls, wmctrl/xdotool,
re-attach) returning RAISED / REATTACHED.

---

### SP-1701 · BE · Focus result in the state
Size S · Depends on SP-0005
The `FocusSession` D-Bus method returns the ladder's result (`raised`, `reattached`,
`failed`), and the session's `attached` flag updates on the next refresh.

### SP-1702 · FE · Focus feedback
Size S · Depends on SP-2502
Raised: toast "Raised the terminal for “<title>”". Re-attached: "Re-attached “<title>”
in a new terminal. The old window closed." Failed with re-attach off: error toast with a
**Settings** action.

### SP-1703 · FE · Every way to focus
Size S
Row title click (Overview), **Focus** and **Open terminal** (Cockpit), menu session rows,
palette "Focus · …". Disabled for starting and journaling sessions.

### SP-1704 · FE · Attached marker
Size S
`● attached` in row sub-lines and Cockpit meta.
