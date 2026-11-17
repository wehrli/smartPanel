# E22 · Command palette (B5)

**Shows:** every action you can take, filtered as you type: answer, focus, journal &
close a session, new session, close all, go to a page, toggle the HUD or rotation, open
the monthly journal. A status line shows swap, sessions and today's spend.
**Appears in:** a centred search window, opened with a GNOME shortcut, Ctrl+K in the
dashboard, the header search button or the bar menu.

---

### SP-2201 · BE · Command registry
Size M · Depends on SP-0004
Builds the action list from the state: `Command(id, title, subtitle, glyph, run)`. Waiting
sessions put "Answer · <title>" first. Starting and journaling sessions get no actions.
- [ ] Unit tests: the list for the busy, empty and notmux fixtures

### SP-2202 · BE · Matching and ranking
Size S
Pure fuzzy match: the query's letters in order (case-insensitive), with a substring match
ranked above a scattered one, then the registry order. Returns the match ranges for
highlighting.
- [ ] Tests: `jour` finds "Journal & close · …", `fsah` finds "Fix swap alert hysteresis"

### SP-2203 · FE · Palette window
Size M · Depends on SP-2201, SP-2202
A search field (placeholder "Type a session or an action…"), up to 9 results with glyph,
title (matched part highlighted), subtitle, and `⏎` on the selected row. ↑↓ moves, Enter
runs, Esc closes. "Nothing matches “xyz”" when empty. Footer: `Swap 12% · 4 sessions · 1
waiting · $23.84 today · ↑↓ move · ⏎ run`.

### SP-2204 · FE · Global shortcut
Size S · Depends on SP-0011
`smartpanel --palette` opens it. Settings explains how to bind it in GNOME Settings →
Keyboard, and offers **Add shortcut** that writes a custom keybinding
(`org.gnome.settings-daemon.plugins.media-keys custom-keybindings`, Ctrl+Alt+Space) after
asking.

### SP-2205 · FE · Focus on open
Size S · Depends on SP-2203
The palette takes keyboard focus when opened from the shortcut (it uses the activation
token passed by the launcher so GNOME doesn't show "smartPanel is ready" instead).
