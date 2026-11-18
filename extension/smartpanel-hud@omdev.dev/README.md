# smartPanel HUD (`smartpanel-hud@omdev.dev`)

The optional desktop HUD (B6, epic E23, decision D3): today's Claude spend, up to six
Claude sessions with their state, CPU / memory / swap / network, and a swap alert line,
drawn on the wallpaper **behind every window**. GNOME Shell 50, ESM.

- **Data:** the smartPanel app over D-Bus (`dev.omdev.SmartPanel`, `/dev/omdev/SmartPanel`,
  `dev.omdev.SmartPanel1`). `GetState(['spend','sessions','system','settings','app'])`
  when the name appears, then on `Changed` at most every 5 s. A change to `settings` is
  fetched at once. When the name vanishes, the HUD shows a dim "smartPanel not running"
  if it was on.
- **Settings (owned by the app):** `hud.enabled` (false), `hud.corner` (`top-right`,
  `top-left`, `bottom-right` or `bottom-left`), `hud.margin` (optional, px, default 30).
  Placed in the primary monitor's work area, and re-placed on `monitors-changed` /
  `workareas-changed`.
- **Placement:** a child of `Main.layoutManager._backgroundGroup` (a
  `Meta.BackgroundGroup`), which mutter always stacks below window actors. A bare actor
  in `global.window_group` would be restacked above the windows. Nothing is reactive.
- **Files:** `hudmodel.js` holds all formatting (plain JS, no shell imports).
  `throttle.js` is the 5 s limiter. `extension.js` builds actors and does D-Bus.

## Tests

- `pytest tests/test_hud_extension.py` runs the gjs suites below. It is skipped when gjs
  is missing.
- `gjs -m tests/js/test_hudmodel.js` and `gjs -m tests/js/test_throttle.js` test the pure modules.
- `gjs -m tests/js/test_extension.js` checks syntax and linking, then drives
  enable/disable against fake shell modules.
- `tests/js/headless_shell_smoke.sh [fixture]` is manual. It runs a throwaway headless
  GNOME Shell with a fake service, then dumps the actor tree and a screenshot.

## Install

`scripts/install-hud.sh` runs pack and install; it never enables the extension, so
re-running it leaves a HUD you switched off switched off. Then log out and back in (Wayland), run
`gnome-extensions enable smartpanel-hud@omdev.dev`, and turn the HUD on in smartPanel.
