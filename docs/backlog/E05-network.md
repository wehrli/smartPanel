# E05 · Network

**Shows:** download and upload rate, the active interface, 15-minute history.
**Appears in:** System page, HUD.
**Already built:** `system.network()` (rates from total byte counters, all interfaces summed).

---

### SP-0501 · BE · Rate history and active interface
Size S · Depends on SP-0004, SP-0006
Ring buffers for down and up; the name of the interface carrying the default route
(read from `/proc/net/route`), falling back to "all".

### SP-0502 · BE · Ignore virtual interfaces
Size S
Exclude `lo`, `docker*`, `veth*`, `br-*`, `virbr*` and `wg*` from the summed rate so
container traffic doesn't double-count. Covered by a test with fake counters.

### SP-0503 · FE · System network card
Size S · Depends on SP-0009
`↓ 2.4 MB/s`, sparkline of down, sub-line `↑ 180 KB/s · wlp2s0`. Units scale B/KB/MB.

### SP-0504 · FE · Network on the HUD
Size S · Depends on SP-2302
Row `net ↓ 2.4 MB/s`.
