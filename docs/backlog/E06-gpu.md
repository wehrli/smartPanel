# E06 · GPU

**Shows:** GPU %, VRAM, temperature, or that no GPU sensor exists.
**Appears in:** System page (the menu hides the row when there's no GPU).
**Already built:** `gpu.gpu()` via pynvml, returning `None` values without NVIDIA.

---

### SP-0601 · BE · Availability flag and AMD support
Size M
Add `AVAILABLE` and `VENDOR` to the sensor. Read AMD busy % and VRAM from
`/sys/class/drm/card*/device/gpu_busy_percent` and `mem_info_vram_*` (the CLAUDE.md
architecture already promises AMD). Tests use a fake sysfs tree.

### SP-0602 · FE · System GPU card
Size S · Depends on SP-0009
With a GPU: `%`, sparkline, `VRAM 3.1 of 8 GB · 54 °C`. Without: dimmed card, chip
"No sensor", text "No NVIDIA or AMD GPU found."

### SP-0603 · FE · Hide GPU rows when unavailable
Size S · Depends on SP-2001
The bar menu leaves out GPU rows when `AVAILABLE` is false, instead of showing `—`.
