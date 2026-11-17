# E02 · CPU

**Shows:** CPU %, 15-minute history, temperature, core count.
**Appears in:** A4 histogram in the top bar, A5 ticker, bar menu, Overview card, System
card, HUD.
**Already built:** `system.cpu()` with `PERCENTAGE`, load averages and `TEMPERATURE`.

---

### SP-0201 · BE · CPU history, cores and temperature in state
Size S · Depends on SP-0004, SP-0006
Push `PERCENTAGE` into a ring buffer each tick; add the logical core count; temperature
stays `None` when no sensor matches (it's shown as `—`, never `0 °C`).
- [ ] Test: missing `coretemp`/`k10temp` gives `None`

### SP-0202 · BE · Temperature source fallback
Size S
`_read_cpu_temp` checks `coretemp`, then `k10temp`, then `zenpower`, then the first
`acpitz`. Log the chosen source once at INFO.

### SP-0203 · FE · CPU histogram in the top bar (A4)
Size M · Depends on SP-0002, SP-1901
The last 10 samples as bars, using the rendering chosen in D2 (cairo icon or `▁▂▃▅▇`).
- [ ] Redraws at the CPU interval, no faster

### SP-0204 · FE · Overview CPU card
Size S · Depends on SP-0009
`23%`, sparkline (0 to 100 fixed scale), sub-line `61 °C`.

### SP-0205 · FE · System CPU card
Size S · Depends on SP-0009
Larger chart, `61 °C · 8 cores · 15 min history`.

### SP-0206 · FE · CPU in the menu, ticker and HUD
Size S · Depends on SP-2001, SP-1902, SP-2302
Menu row `CPU 23%`; ticker item `CPU 23% · 61 °C`; HUD row `cpu 23%`.
