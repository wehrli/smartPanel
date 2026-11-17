# E03 · Memory

**Shows:** RAM %, GB used of total, 15-minute history.
**Appears in:** A4 meter in the top bar, bar menu, Overview card, System card, HUD.
**Already built:** `system.memory()` (`PERCENTAGE`, `USED`, `TOTAL` in GiB).

---

### SP-0301 · BE · Memory history in state
Size S · Depends on SP-0004, SP-0006
Ring buffer of `PERCENTAGE`; `used_gb` and `total_gb` in the state.

### SP-0302 · FE · Memory meter in the top bar (A4)
Size S · Depends on SP-0002, SP-1901
A thin white vertical meter, left of the swap meter, filled to the RAM %.
- [ ] Never the same colour as the swap meter (the prototype bug where they merged)

### SP-0303 · FE · Overview memory card
Size S · Depends on SP-0009
`68%`, sparkline, `10.8 of 16 GB`.

### SP-0304 · FE · System memory card
Size S · Depends on SP-0009
Larger chart, `10.8 of 16 GB used`.

### SP-0305 · FE · Memory in the menu and HUD
Size S · Depends on SP-2001, SP-2302
Menu row `Memory 68%`; HUD row `memory 68%`.
