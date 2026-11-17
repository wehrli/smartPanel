# E21 · Dashboard shell (B1)

**Shows:** the window that holds every page: sidebar navigation with badges, header bar,
banner, toasts and dialogs.
**Appears in:** the dashboard window (GTK4/libadwaita, or GTK3 if D1 says so).
**Already built:** a GTK3 scrollable dashboard (`dashboard.py`) that this replaces.

---

### SP-2101 · FE · Window and navigation
Size L · Depends on SP-0001, SP-0005
A split view: sidebar with the app name and six rows (Overview, Sessions, Spend, System,
Journal, Settings, each with a symbolic icon), and a content area with a header bar (page
title, a search button that opens the palette, close). It remembers its size and last page.
- [ ] Opens centred via the menu, middle-click, or `smartpanel --dashboard`

### SP-2102 · FE · Page routing
Size S · Depends on SP-2101
`OpenDashboard(page)` with optional detail (`sessions:board`, `journal:<id>`). Unknown
pages fall back to Overview.

### SP-2103 · FE · Nav badges
Size S · Depends on SP-2101
Sessions: amber waiting count, or red `!` when tmux is missing. System: amber `·` at a swap
warning, red `!` when critical.

### SP-2104 · FE · Banner, toast and dialog hosts
Size M · Depends on SP-2101
One banner slot under the header (E01), an overlay for toasts (E25), and modal dialogs
inside the window (E15, E16) with a dimmed backdrop.

### SP-2105 · FE · Light and dark
Size S · Depends on SP-0008
Follow GNOME's style preference by default; Settings can force light or dark.

### SP-2106 · FE · Sidebar footer
Size S · Depends on SP-2101
"Updated 3 s ago" (or "Reading transcripts…") and the version.

### SP-2107 · FE · Keyboard
Size S · Depends on SP-2101
Ctrl+K palette, Ctrl+1 to Ctrl+6 pages, Esc closes dialogs, Ctrl+W closes the window.

### SP-2108 · FE · Live updates without flicker
Size M · Depends on SP-2101
Pages update only the widgets whose data changed (from `Changed(sections)`), keeping
scroll position, keyboard focus and selection. No full page rebuild every tick.

### SP-2109 · FE · Accessibility
Size M · Depends on SP-2101
Every glyph has an accessible name ("Needs you", "Running"). State is never shown by colour
alone (the strip's notch, words on pills). Tab order follows the visual order. Checked with
Orca on the Overview and Sessions pages.
