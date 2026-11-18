# E27 · Packaging & QA

---

### SP-2701 · QA · Screenshot suite
Size M · Depends on SP-0012
A script that opens the dashboard on each page in each demo scenario, light and dark, and
saves PNGs to `docs/screenshots/`. Used for the README and to review regressions by eye.

### SP-2702 · QA · Performance budget
Size S · Depends on SP-0801
A profiling run (`--debug-timing`) that logs any main-loop callback over 50 ms. Checked
with 9 sessions and a cold scan of 1,000+ transcripts.

> **Measured 2026-11-18:** `--debug-timing`, private bus, cold scan of 1,100
> transcripts (411 MB). Before (scan on a thread): `SystemCollector._on_timer` worst
> 5563 ms, `SessionsService._tick` worst 1231 ms, 11 watchdog breaches. After (scan in a
> child process, `claude_stats/scan_worker.py`; tmux reads on a worker thread):
> `_on_timer` worst 3.6 ms, `_tick` worst 16.3 ms, no breach after startup. The 9 live
> tmux sessions part was not run: the checks never touch real tmux.

### SP-2703 · DOCS · README and CLAUDE.md
Size S
New screenshots, the settings list, the palette shortcut, the new apt packages
(libadwaita if D1 picks it), the HUD route from D3. Update the CLAUDE.md architecture tree
for `state.py`, `config.py`, `dbus/`, `ui/`.

### SP-2704 · OPS · Install and autostart
Size S · Depends on SP-0005
`scripts/install.sh` installs the D-Bus service file (so `--dashboard` and `--palette` can
start the daemon), creates `~/.config/smartpanel/`, and keeps the systemd user service.
