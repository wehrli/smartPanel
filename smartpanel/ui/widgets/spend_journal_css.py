"""Page-specific CSS for the Spend and Journal pages (class prefix `sj-`).

Loaded once per process, on top of `ui/style.css`. Two colours here have no
shared token (the receipt's recessed "paper wrap" background), so the sheet is
re-loaded with the right one whenever `Adw.StyleManager:dark` flips, the same
way `ui/tokens.py` swaps its palette. The receipt paper itself is off-white in
both modes on purpose: it's a printed receipt.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

PAPER = "#f7f8f6"
INK = "#1d1d1d"
INK_MUTED = "#5c5c5c"        # 6.3:1 on PAPER
INK_TIP = "#a8370f"          # accent red, 5.6:1 on PAPER
RULE = "#8a8a8a"
WRAP = {"dark": "#1c1c1c", "light": "#e4e4e4"}

_CSS = """
/* ── spend header ── */
.sj-head-card { padding: 10px 13px; }
.sj-head-value { font-size: 1.45em; font-weight: 700; font-feature-settings: "tnum"; }
.sj-tnum { font-feature-settings: "tnum"; }

/* ── day list ── */
.sj-days { padding: 6px; background: transparent; }
.sj-days row { border-radius: 8px; padding: 5px 9px; }
.sj-days row:selected { background-color: var(--sp-sel); color: inherit; }
.sj-days row:selected .sj-day-label, .sj-days row:selected .sj-day-total { font-weight: 700; }
.sj-day-sub { color: var(--sp-muted); }

/* ── receipt ── */
.sj-wrap { background-color: @@WRAP@@; border-radius: 12px; padding: 16px; }
.sj-paper label {
  font-family: var(--sp-mono);
  font-feature-settings: "tnum";
  font-size: 12px;
  color: @@INK@@;
}
.sj-paper label.sj-m { color: @@INK_MUTED@@; }
.sj-paper label.sj-tip { color: @@INK_TIP@@; }
.sj-paper label.sj-tot { font-weight: 700; font-size: 14.5px; }
.sj-paper label.sj-brand { font-weight: 700; letter-spacing: 0.15em; }
.sj-dash { border-top: 1px dashed @@RULE@@; margin: 7px 0; min-height: 0; }
.sj-note { font-size: 0.85em; color: var(--sp-muted); }

/* ── journal ── */
.sj-jlist { background: transparent; }
.sj-jlist row { padding: 9px 12px; border-bottom: 1px solid var(--sp-line); }
.sj-jlist row:last-child { border-bottom: none; }
.sj-jlist row:selected { background-color: var(--sp-sel); color: inherit; }
.sj-jtitle { font-weight: 600; }
.sj-badge { font-size: 0.8em; font-weight: 700; }
.sj-badge-ok { color: var(--sp-ok); }
.sj-badge-fail { color: var(--sp-crit); }
.sj-jdet { padding: 14px 16px; }
.sj-jdet-title { font-size: 1.25em; font-weight: 700; }
.sj-quote {
  padding: 9px 12px;
  border-left: 3px solid var(--sp-accent);
  background-color: var(--sp-card2);
  border-radius: 0 8px 8px 0;
}
.sj-quote.sj-failed { border-left-color: var(--sp-level-crit); }
.sj-dt { color: var(--sp-muted); }
.sj-dd { font-family: var(--sp-mono); font-size: 0.92em; }
.sj-path { font-family: var(--sp-mono); font-size: 0.88em; color: var(--sp-muted); }
"""


def css(dark: bool) -> str:
    out = _CSS
    for key, val in (("WRAP", WRAP["dark" if dark else "light"]), ("INK", INK),
                     ("INK_MUTED", INK_MUTED), ("INK_TIP", INK_TIP), ("RULE", RULE)):
        out = out.replace(f"@@{key}@@", val)
    return out


_provider = None


def install() -> None:
    """Idempotent: the first call loads the sheet and follows light/dark."""
    global _provider
    if _provider is not None:
        return
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gtk

    display = Gdk.Display.get_default()
    if display is None:
        return
    _provider = Gtk.CssProvider()
    # +2: above style.css (+0) and the palette (+1) so our rules win ties.
    Gtk.StyleContext.add_provider_for_display(
        display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
    manager = Adw.StyleManager.get_default()

    def apply(*_):
        try:
            _provider.load_from_string(css(manager.get_dark()))
        except Exception:  # noqa: BLE001 — a CSS typo must not stop the app
            log.exception("spend/journal css failed to load")

    manager.connect("notify::dark", apply)
    apply()
