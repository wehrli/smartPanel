"""Styles for the Settings page and the command palette (W2-settings-palette).

Kept out of the shared `style.css` (owned by WP-ui): loaded once per display with
its own Gtk.CssProvider. Every class is prefixed `sp-sp-` (settings/palette) so it
can't collide with another package. Colours only ever use the `--sp-*` custom
properties from `ui/tokens.py`, so light and dark follow the app's style.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

CSS = """
/* ── settings: top-bar preview (SP-1908) ── */
.sp-sp-preview {
  background-color: #000000;
  border-radius: 10px;
  min-height: 40px;
  padding: 0 12px;
}
.sp-sp-preview-label {
  color: #ffffff;
  font-feature-settings: "tnum";
  font-weight: 500;
}
.sp-sp-preview-icon { color: #ffffff; }
.sp-sp-preview-note { color: var(--sp-muted); font-size: 0.9em; }
.sp-sp-code {
  font-family: var(--sp-mono);
  font-size: 0.92em;
  background-color: var(--sp-card2);
  color: var(--sp-fg);
  border-radius: 8px;
  padding: 5px 10px;
}
.sp-sp-tag { font-family: var(--sp-mono); font-size: 0.82em; color: var(--sp-muted); }
.sp-sp-value {
  font-family: var(--sp-mono);
  font-feature-settings: "tnum";
  min-width: 42px;
}
.sp-sp-unit { color: var(--sp-muted); }
.sp-sp-error { color: var(--sp-crit); }
row.sp-sp-row-error .subtitle { color: var(--sp-crit); opacity: 1; }
entry.sp-sp-num { min-width: 0; }

/* ── command palette (SP-2203) ── */
window.sp-sp-palette, window.sp-sp-palette > * {
  background-color: var(--sp-card);
  color: var(--sp-fg);
}
window.sp-sp-palette { border-radius: 16px; }
.sp-sp-pal-input { padding: 12px 16px; border-bottom: 1px solid var(--sp-line); }
.sp-sp-pal-input entry, .sp-sp-pal-input text {
  background: none;
  box-shadow: none;
  outline: none;
  font-size: 1.3em;
  min-height: 30px;
}
.sp-sp-pal-search-icon { color: var(--sp-muted); }
.sp-sp-kbd {
  font-family: var(--sp-mono);
  font-size: 0.8em;
  background-color: var(--sp-btn);
  color: var(--sp-fg);
  border-radius: 5px;
  padding: 2px 6px;
}
.sp-sp-pal-list { background: none; padding: 6px; }
.sp-sp-pal-list > row {
  border-radius: 9px;
  padding: 7px 10px;
  margin-bottom: 1px;
}
.sp-sp-pal-list > row:selected, .sp-sp-pal-list > row.sp-sp-sel {
  background-color: var(--sp-sel);
  color: var(--sp-fg);
}
/* the ⏎ cap sits on the --sp-sel row, where --sp-btn is nearly the same grey */
.sp-sp-pal-list > row:selected .sp-sp-kbd, .sp-sp-pal-list > row.sp-sp-sel .sp-sp-kbd {
  background-color: alpha(var(--sp-fg), 0.16);
  border: 1px solid alpha(var(--sp-fg), 0.24);
  color: var(--sp-fg);
}
.sp-sp-pal-glyph { min-width: 20px; }
.sp-sp-pal-sub { color: var(--sp-muted); font-size: 0.86em; }
.sp-sp-pal-none { color: var(--sp-muted); padding: 26px; }
.sp-sp-pal-foot {
  padding: 9px 16px;
  border-top: 1px solid var(--sp-line);
  background-color: var(--sp-bg);
  color: var(--sp-muted);
  font-size: 0.9em;
}
.sp-sp-pal-foot label { font-feature-settings: "tnum"; }
"""

_installed: set[int] = set()


def install(display=None) -> None:
    """Add the provider once per display (idempotent)."""
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gtk

    display = display or Gdk.Display.get_default()
    if display is None or id(display) in _installed:
        return
    provider = Gtk.CssProvider()
    try:
        provider.load_from_string(CSS)
    except Exception:  # noqa: BLE001 — a CSS typo must not stop the app
        log.exception("settings/palette CSS failed to load")
        return
    # +2: above style.css and the token palette, so our rules win ties.
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
    _installed.add(id(display))
