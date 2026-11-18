# ADR 0003: Desktop HUD on Wayland (D3, SP-0003)

Status: accepted.

## Context

The design (B6, `hudHTML()` in `docs/design/prototype.html`) has an optional HUD drawn on
the desktop: today's Claude spend, the sessions and four system metrics. It sits behind
every window, does not take clicks and is placed in a chosen corner. The target is GNOME
Shell 50, which runs Wayland only.

## Options

1. **Borderless, transparent GTK window.** On Wayland a client can't place its own
   window, keep it below the others, keep it off the alt-tab list or make it
   click-through. Mutter decides all of that. It would float above other windows or move
   around.
2. **`gtk-layer-shell`.** This is the protocol made for this job
   (`zwlr_layer_shell_v1`), but Mutter does not implement it. The library loads and then
   cannot create a layer surface on GNOME.
3. **A small GNOME Shell extension** that adds `St` actors to
   `Main.layoutManager._backgroundGroup`. That group is a `Meta.BackgroundGroup`, which
   Mutter always stacks below window actors. It is hidden in the overview and on the
   lock screen for free.

## Decision

Option 3: `extension/smartpanel-hud@omdev.dev` (GJS, ESM, shell-version 50). It is
optional, and smartPanel works the same without it.

- **Size of the GJS piece:** about 800 lines. `extension.js` (about 400) builds the
  actors and does the D-Bus work. `hudmodel.js` (about 300) holds all the formatting as
  plain JS with no shell imports. `throttle.js` (about 110) is the 5 s rate limiter.
  The two pure modules are unit-tested with bare `gjs`.
- **Data route:** the app's D-Bus API (`dev.omdev.SmartPanel1`,
  `smartpanel/dbus/panel1.xml`, SP-0005). On name-appeared the extension reads the
  `Version` property. A schema it doesn't know shows "smartPanel version mismatch" and
  nothing is fetched. Otherwise it calls `GetState` for the sections it draws, then
  refetches on `Changed` at most every 5 s. A `settings` change is fetched at once, so
  the toggle feels instant. The app owns `hud.enabled`, `hud.corner` and `hud.margin`.
  The extension only draws.
- Nothing is reactive, so clicks fall through to the desktop.

## Consequences

- The extension is a separate install (`scripts/install-hud.sh`), needs a log-out to be
  discovered on Wayland and must be enabled by the user. The installer never enables
  it.
- It only works where GNOME Shell lists its version in `metadata.json` (`["50"]`). Every
  Shell major needs a check.
- `_backgroundGroup` is a private field of the Shell's layout manager. A Shell change
  there breaks placement, and `tests/js/headless_shell_smoke.sh` is the check for that.
- The D-Bus contract is shared with other consumers, so a breaking change bumps
  `Version`.
