# E04 · Disk

**Shows:** usage of the root filesystem (%, GB used of total).
**Appears in:** System page.
**Already built:** `system.disk()` for `/`.

---

### SP-0401 · BE · Configurable mount point
Size S · Depends on SP-0007
Setting `disk.mount` (default `/`). An unreadable mount returns `None` values instead of
raising.

### SP-0402 · FE · System disk card
Size S · Depends on SP-0009
`41%`, a horizontal bar, `196 of 476 GB on /`.

### SP-0403 · FE · Disk warning colour
Size S · Depends on SP-0402
Bar turns amber above 90% and red above 97% (display only; no notification).
