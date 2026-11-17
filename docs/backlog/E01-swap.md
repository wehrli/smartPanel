# E01 · Swap pressure

**Shows:** swap used (% and GB), and the alert level (fine, warning, critical, not configured).
**Appears in:** top-bar numbers and meter, bar menu, window banner, Overview card, System
card, System nav badge, HUD, palette status line, desktop notifications.
**Already built:** `system.swap()` (returns `None` without swap), `alerts.AlertTracker`
(hysteresis, notify up, quiet down, recovery), `notify.py`, the dashboard banner.

---

### SP-0101 · BE · Swap section in the panel state
Size S · Depends on SP-0004
Move what `Indicator._check_swap` computes into the state: `percent | None`, `used_gb`,
`total_gb`, `level` (OK/WARNING/CRITICAL), `alerted`, thresholds in use.
- [ ] `Indicator._check_swap` becomes a reader of the state, not the owner of the tracker
- [ ] `AlertTracker` keeps being the only policy; existing `test_alerts.py` stays green

### SP-0102 · BE · Swap history
Size S · Depends on SP-0006
Push each swap reading into a ring buffer for the Overview and System sparklines.
- [ ] No samples pushed when there's no swap (the chart shows the empty state)

### SP-0103 · BE · Thresholds from settings, applied live
Size M · Depends on SP-0007
Warning and critical thresholds come from the config file (env vars still win). Changing
them in Settings reconfigures the tracker without a restart and without dropping the
current level or re-firing a notification.
- [ ] Critical below warning is clamped up (as `thresholds_from_env` does today)
- [ ] Tests: raise warn above the current %, then the level drops quietly per hysteresis

### SP-0104 · FE · Swap in the top bar
Size M · Depends on SP-1901, SP-0002
- A1 numbers: the first segment is the swap %, as `12`. At warning it becomes `⚠66`, and at
  critical `⚠88` (plus colour if D2 allows). No swap shows `—`.
- A4 meter: a thin bar, grey while fine, amber at warning, red at critical.
- A5 ticker: a swap alert pins `⚠ Swap 88%` and stops the rotation.
- [ ] Width stays inside `TOP_BAR_GUIDE` at 100%

### SP-0105 · FE · Swap row in the bar menu
Size S · Depends on SP-2001
`Swap 12%`, `Swap 66% high`, `Swap 88% critical`, or `Swap none`.

### SP-0106 · FE · Window banner
Size S · Depends on SP-2104
Amber (warning) or red (critical) banner on every page: "Swap is filling up · 66% used
(5.3 of 8 GB). Close something heavy before the system stalls." The **Open System**
button is hidden when already on System. Hidden at OK.

### SP-0107 · FE · Overview swap card
Size S · Depends on SP-0009
Value, 15-min sparkline with dashed warn and critical lines, "1.0 of 8 GB", and an amber
or red border by level. No swap: `—` and "No swap configured".

### SP-0108 · FE · System page swap card
Size S · Depends on SP-0009
Bigger chart, level chip (Fine / Warning / Critical / Not configured), and the sub-line
"warns at 60%, critical at 85%". The no-swap text explains that a full RAM means the OOM
killer, not a slowdown.

### SP-0109 · FE · System nav badge
Size S · Depends on SP-2103
Sidebar "System" shows `·` (amber) at warning and `!` (red) at critical.

### SP-0110 · FE · Swap on the HUD
Size S · Depends on SP-2302
Swap row coloured by level, plus a line "⚠ swap critical" under the metrics while alerting.

### SP-0111 · FE · Swap in the palette status line
Size S · Depends on SP-2203
`Swap 12%` or `Swap —` in the palette footer.

### SP-0112 · QA · Swap journey test
Size S · Depends on SP-0101, SP-0012
Drive the state 12 → 93 → 15 (the prototype's "Swap climbs / recovers" flows) and
assert: one warning notification, one critical notification, nothing on critical to warning,
one "back to normal", and the correct bar text at each step.
