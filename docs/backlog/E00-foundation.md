# E00 · Foundation

Shared plumbing every information epic builds on: one state model, a way for separate
surfaces to read it, metric history, persisted settings, the visual language, and a demo
mode that replays the prototype's scenarios.

---

### SP-0001 · SPIKE · Decide the dashboard toolkit (D1)
Size S
Build a throwaway libadwaita window (`Adw.NavigationSplitView` with 6 rows) as a second
process, and a GTK3 window styled with CSS to imitate it. Compare the look, the cost of
the IPC, startup time and memory.
- [ ] Short ADR in `docs/adr/0001-dashboard-toolkit.md` with the choice and why
- [ ] Confirms `gir1.2-adw-1` availability on Ubuntu 24.04 and the apt line for the README

### SP-0002 · SPIKE · Graphics in the top bar (D2)
Size S
On Ubuntu 24.04 with the stock AppIndicator extension, test:
(1) a cairo-rendered icon 3:1 wide (does it keep its aspect or get squashed to a square?),
(2) icon refresh rate every 3 s (does it flicker or leak?),
(3) Unicode fallbacks in the label (`▁▂▃▅▇` histogram, `▮` strip).
- [ ] ADR `0002-top-bar-graphics.md`: which A4/A6 rendering ships, and the fallback
- [ ] Screenshot of each option in the real panel

### SP-0003 · SPIKE · Desktop HUD on Wayland (D3)
Size S
Try: a borderless transparent GTK window, `gtk-layer-shell` (not supported by Mutter, confirm),
and a minimal GNOME Shell extension that draws a `St.Label` on the background group.
- [ ] ADR `0003-desktop-hud.md` with the chosen route
- [ ] If an extension: the size of the GJS piece, and how it gets data (D-Bus, SP-0005)

### SP-0004 · BE · Panel state model
Size L · Depends on SP-0001
One GTK-free `PanelState` (dataclasses) holding everything the surfaces show: system
metrics, swap alert, Claude spend, sessions (with panel-owned states like launching and
journaling), journal list, settings, app status (loading, first run, tmux missing). It
emits `changed(section)` so surfaces redraw only what moved. Sensors and session refresh
write into it; the indicator and dashboard read from it.
- [ ] `smartpanel/state.py` with sections: `system`, `swap`, `claude`, `sessions`, `journal`, `app`
- [ ] Indicator and existing dashboard migrated to read from it (no behaviour change yet)
- [ ] Unit tests for change notification and section diffing

### SP-0005 · BE · D-Bus service `org.smartpanel.Panel1`
Size L · Depends on SP-0004
Expose the state to other processes (GTK4 dashboard, palette, HUD extension).
- [ ] Property `State` (JSON string, versioned) and signal `Changed(as sections)`
- [ ] Methods: `OpenDashboard(s page)`, `OpenPalette()`, `SetHud(b)`, `FocusSession(s)`,
      `CloseSession(s, b journal)`, `CloseAll()`, `NewSession(s dir)`,
      `AnswerPrompt(s session, u option, s fingerprint)`, `SetSetting(s key, v value)`
- [ ] Introspection XML in `smartpanel/dbus/panel1.xml`; a client helper for the dashboard
- [ ] Calls that take long (journal) return immediately; progress comes through `Changed`

### SP-0006 · BE · Metric history ring buffers
Size S · Depends on SP-0004
Fixed-size history per metric (CPU, memory, swap, network down/up): 300 points at the
poll interval (15 min at 3 s). Replaces the per-row `HISTORY_SIZE` deques in the indicator.
- [ ] `RingBuffer` with `push`, `last(n)`, `values()`; unit-tested
- [ ] Exposed in state as lists (the D-Bus JSON sends only the last 60 by default)

### SP-0007 · BE · Settings file
Size M
`~/.config/smartpanel/config.yaml`, written atomically. Precedence: env var, then file,
then default. Keys cover every Settings control in the prototype (see E24).
- [ ] `smartpanel/config.py`: load, validate, save, observers notified on change
- [ ] A garbled file logs a warning and falls back to defaults (never crashes)
- [ ] Each key reports whether an env var is overriding it (the UI greys those controls out)

### SP-0008 · FE · Design tokens
Size S · Depends on SP-0001
One CSS file shared by window, palette and HUD: accent `#E95420`; activity colours
(waiting amber, running blue, idle grey, journaling purple); warn/critical; light and dark
values; mono font for numbers.
- [ ] `smartpanel/ui/style.css`, loaded once per process
- [ ] Contrast checked on light and dark (WCAG AA for text)

### SP-0009 · FE · Widget kit
Size L · Depends on SP-0008, SP-0010
The reusable pieces in the prototype: `MetricCard` (title, value, chart, sub-line, level
border), `Sparkline`, `HourBars`, `Pill`, `ActivityGlyph`, `LevelChip`, `EmptyState`
(icon, title, text, action), `Banner`, `Receipt`.
- [ ] Each widget has a gallery entry in `smartpanel --demo gallery`

### SP-0010 · FE · Chart drawing helpers
Size M
Cairo drawing for sparklines (area fill, line, endpoint, dashed threshold lines) and bar
charts. Geometry (points from values, scale, thresholds) is a pure function with tests;
only painting touches cairo.
- [ ] `smartpanel/ui/charts.py` + `tests/test_charts.py` (geometry only)

### SP-0011 · BE · Single instance and CLI verbs
Size M · Depends on SP-0005
`smartpanel --dashboard [page]`, `--palette`, `--hud on|off|toggle` forward to the running
instance over D-Bus, and start it if absent.
- [ ] A second `smartpanel` launch doesn't start a second indicator
- [ ] Exit codes and messages when the daemon isn't running and can't start

### SP-0012 · QA · Demo mode with the prototype's scenarios
Size M · Depends on SP-0004
`smartpanel --demo <scenario>` fills the state from fixtures instead of real sensors:
busy, calm, alert, empty, many, cold, first, notmux, noswap. Flows (journal, new session,
answer) are simulated with timers, like the prototype.
- [ ] Scenario fixtures in `tests/fixtures/demo/*.yaml`
- [ ] Used by every FE task's "done" check and by the screenshot suite (SP-2701)

### SP-0013 · QA · State model test harness
Size S · Depends on SP-0004
Helpers to build a `PanelState` from a scenario fixture and assert on rendered strings
(bar label, menu rows) without GTK.
