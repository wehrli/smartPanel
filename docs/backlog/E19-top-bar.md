# E19 · Top bar composition

**Shows:** the panel item: A6 session strip, A4 live graphs, and either A1 numbers
(`12 · 4/1 · 23.84`) or the A5 ticker (one reading at a time), each switchable.
**Appears in:** the GNOME top bar; the live preview in Settings.
**Already built:** `top_bar_format` templates, `_with_alert_badge`, `TOP_BAR_GUIDE`,
middle-click to the dashboard via `set_secondary_activate_target`.

How the strip and graphs get drawn depends on D2 (SP-0002). The BE tasks here produce a
renderer-independent description so that choice stays swappable.

---

### SP-1901 · BE · Bar model
Size M · Depends on SP-0004, SP-0007
Pure `compose_bar(state, config) -> BarSpec` with `label: str`, `icon: IconSpec | None`
(strip blocks, histogram values, meter levels, colours) and `accessible_desc`. Config keys
`bar.strip`, `bar.graphs`, `bar.text`, `bar.ticker`. With nothing enabled it falls back to
the plain icon.
- [ ] Unit tests for each combination and for loading, first run, tmux missing, no swap

### SP-1902 · BE · Ticker rotation
Size S · Depends on SP-1901
Rotate through spend, sessions, swap, CPU every 5 s (`bar.ticker_seconds`), with position
dots `●○○○`. **Pin current reading** stops it; a swap alert pins the swap reading and shows
"pinned by alert" until it clears.
- [ ] The rotation timer exists only while the ticker is on

### SP-1903 · BE · Width guide per composition
Size S · Depends on SP-1901
Compute the widest label the current composition can render (for `set_label`'s guide), so
the panel item doesn't jitter as numbers change width. Replaces the fixed `TOP_BAR_GUIDE`.

### SP-1904 · BE · Theme template and composition precedence
Size S · Depends on SP-1901
If a theme sets `smartpanel.top_bar_format`, it wins over the composition (so custom and
turing-smart-screen themes keep working). The shipped presets switch to the composition.
Document the rule in CLAUDE.md.

### SP-1905 · FE · Render the bar
Size M · Depends on SP-1901, SP-0002
Apply `BarSpec` to the AppIndicator: label text, plus the rendered icon (cached by content
hash so an unchanged icon isn't re-set). Accessible description set for screen readers.

### SP-1906 · FE · Loading label
Size S · Depends on SP-2601
During the cold scan: spinner icon and `reading 812/1284`.

### SP-1907 · FE · Click and middle-click
Size S
Click opens the menu (E20); middle-click opens the dashboard on its last page. Keep the
existing secondary-activate wiring working with the new menu.

### SP-1908 · FE · Settings preview
Size S · Depends on SP-2401
A black strip in Settings → Top bar that renders the current `BarSpec`, updating as the
four switches change.
