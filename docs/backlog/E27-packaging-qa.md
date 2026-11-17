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

### SP-2703 · DOCS · README and CLAUDE.md
Size S
New screenshots, the settings list, the palette shortcut, the new apt packages
(libadwaita if D1 picks it), the HUD route from D3. Update the CLAUDE.md architecture tree
for `state.py`, `config.py`, `dbus/`, `ui/`.

### SP-2704 · OPS · Install and autostart
Size S · Depends on SP-0005
`scripts/install.sh` installs the D-Bus service file (so `--dashboard` and `--palette` can
start the daemon), creates `~/.config/smartpanel/`, and keeps the systemd user service.
