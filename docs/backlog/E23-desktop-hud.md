# E23 · Desktop HUD (B6)

**Shows:** today's spend in large type, tokens and burn rate, up to 6 sessions with their
state, CPU, memory, swap and network, and a swap alert line.
**Appears in:** the desktop, behind windows, with no window frame.
**Blocked by:** D3 (SP-0003). The route chosen there decides how SP-2302 is built.

---

### SP-2301 · BE · HUD data feed
Size S · Depends on SP-0005
A reduced, low-rate view of the state for the HUD (every 5 s, not every 2): spend line,
sessions, four metrics, alert level. Served over D-Bus if the HUD is a Shell extension.

### SP-2302 · FE · HUD surface
Size L · Depends on SP-0003, SP-2301
Right-aligned light text on the wallpaper with a soft shadow: `CLAUDE · TODAY`, `23.84`,
`usd · 41.2M tokens · 3.10/h`, a session list (title and state word in its colour), the
metrics, then "⚠ swap critical" when alerting. Shows `…` while loading and `—` on first run.

### SP-2303 · FE · Toggle and remember
Size S · Depends on SP-2302
Toggled from the bar menu checkbox, Settings, and the palette; the choice is saved
(`hud.enabled`).

### SP-2304 · FE · Position
Size S · Depends on SP-2302
Corner choice (top-right default) and margin in Settings, if the D3 route allows placing it.
