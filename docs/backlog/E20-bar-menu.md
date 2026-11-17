# E20 · Bar menu

**Shows:** a short summary and the main actions, on click of the panel item.
**Appears in:** the AppIndicator menu (DBusMenu, so text, separators and checkboxes only).
**Already built:** a minimal launcher menu (Open dashboard / New session / Quit).

---

### SP-2001 · FE · Menu structure
Size M · Depends on SP-0004
Sections, in order:
1. **System**: CPU, Memory, Swap (with "high" or "critical").
2. **Claude · today**: Spent, Burn rate (or "Reading transcripts… 812/1284", or "No
   transcripts yet").
3. **Sessions · 4 · 1 waiting**: up to 4 sessions, then "+N more…" (SP-1207).
4. **Actions**: New session…, Command palette… (shows the shortcut), Open dashboard,
   Pin current reading / Resume rotation (only while the ticker is on), ☑ Desktop HUD.
5. Quit smartPanel.

### SP-2002 · FE · Update rows in place
Size S · Depends on SP-2001
Rebuilding a DBusMenu re-sends the whole tree and can make an open menu flicker. Keep a
fixed pool of session items, change their labels and visibility, and only update rows
whose text changed.

### SP-2003 · FE · Disabled and hidden rows
Size S · Depends on SP-2001
Starting and journaling sessions are disabled. GPU rows are hidden without a GPU (SP-0603).
Session actions are disabled when tmux is missing.
