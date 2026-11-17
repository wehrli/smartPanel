"""Page-specific CSS for the Sessions page and its dialogs (class prefix `sps-`).

Kept out of the shared `ui/style.css` (owned by WP-ui). Colours only ever refer
to the `--sp-*` tokens that `ui/tokens.py` emits for the current light/dark
mode, so this follows the style switch like everything else. Loaded once per
display, at a priority just under the palette provider.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

CSS = """
/* ── page + toolbar ── */
.sps-page { padding: 14px 18px 16px 18px; }
.sps-toolbar { margin-bottom: 12px; }
.sps-progress { color: var(--sp-muted); font-size: 0.9em; }

/* ── cockpit cards ── */
.sps-card {
  background-color: var(--sp-card);
  border: 1px solid var(--sp-line);
  border-radius: 12px;
  padding: 11px 13px;
}
.sps-card.sps-st-waiting {
  background-color: var(--sp-waitbg);
  border-color: var(--sp-level-warn);
}
.sps-card-title { font-weight: 600; font-size: 1.04em; }
.sps-meta { color: var(--sp-muted); font-size: 0.88em; font-feature-settings: "tnum"; }
.sps-mono { font-family: var(--sp-mono); font-feature-settings: "tnum"; }
.sps-prompt {
  font-family: var(--sp-mono);
  font-size: 0.88em;
  background-color: var(--sp-bg);
  border: 1px solid var(--sp-line);
  border-radius: 8px;
  padding: 9px 11px;
}
.sps-cursor { color: var(--sp-accent-text); font-weight: 700; }
.sps-doing { font-family: var(--sp-mono); font-size: 0.88em; color: var(--sp-run); }
.sps-starting { font-size: 0.9em; color: var(--sp-muted); }
@keyframes sps-blink { 0% { opacity: 1; } 50% { opacity: 0.25; } 100% { opacity: 1; } }
.sps-blink { color: var(--sp-run); animation: sps-blink 1.2s steps(2) infinite; }

/* ── journal checklist ── */
.sps-step { font-size: 0.92em; color: var(--sp-muted); }
.sps-step-icon { min-width: 16px; font-weight: 700; }
.sps-step.sps-done { color: var(--sp-fg); }
.sps-step.sps-done .sps-step-icon { color: var(--sp-ok); }
.sps-step.sps-now { color: var(--sp-fg); font-weight: 600; }
.sps-step.sps-fail { color: var(--sp-crit); font-weight: 600; }

/* ── board ── */
.sps-col {
  background-color: var(--sp-card2);
  border-radius: 12px;
  padding: 9px;
}
.sps-col-head { font-size: 0.9em; font-weight: 600; padding: 2px 3px; }
.sps-col-count { font-feature-settings: "tnum"; color: var(--sp-muted); }
.sps-colempty { color: var(--sp-muted); font-size: 0.9em; padding: 16px 4px; }
.sps-bcard {
  background-color: var(--sp-card);
  border: 1px solid var(--sp-line);
  border-left: 3px solid transparent;
  border-radius: 10px;
  padding: 9px 10px;
  font-size: 0.94em;
}
.sps-bcard:focus-visible { outline: 2px solid var(--sp-accent); outline-offset: -2px; }
.sps-bcard.sps-st-waiting { border-left-color: var(--sp-level-warn); }
.sps-bcard.sps-st-running { border-left-color: var(--sp-run); }
.sps-bcard.sps-st-idle, .sps-bcard.sps-st-unknown { border-left-color: var(--sp-idle); }
.sps-bcard.sps-st-launching { opacity: 0.7; }
.sps-bcard-title { font-weight: 600; }
.sps-bcard-sub { color: var(--sp-muted); font-size: 0.9em; font-feature-settings: "tnum"; }
.sps-dropzone {
  border: 2px dashed var(--sp-line);
  border-radius: 12px;
  padding: 14px;
  color: var(--sp-muted);
}
.sps-dropzone.sps-over {
  border-color: var(--sp-accent);
  background-color: alpha(var(--sp-accent), 0.10);
  color: var(--sp-fg);
}
.sps-dropzone .sps-card { padding: 10px 12px; }

/* ── error / empty ── */
.sps-code {
  font-family: var(--sp-mono);
  background-color: var(--sp-card2);
  border-radius: 8px;
  padding: 4px 4px 4px 10px;
}

/* ── folder picker ── */
.sps-crumbs {
  font-family: var(--sp-mono);
  font-size: 0.88em;
  color: var(--sp-muted);
  background-color: var(--sp-card2);
  border-radius: 8px;
  padding: 6px 10px;
}
flowbox.sps-dirs > flowboxchild { border-radius: 8px; padding: 6px 9px; }
flowbox.sps-dirs > flowboxchild:selected {
  background-color: var(--sp-accent);
  color: #ffffff;
}
flowbox.sps-dirs > flowboxchild:selected .sps-has-session { color: #ffffff; }
.sps-has-session { color: var(--sp-run); }
.sps-hint { color: var(--sp-muted); font-size: 0.92em; }
"""

_installed: set[int] = set()


def install(display=None) -> None:
    """Add the provider once per display. Never raises."""
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, Gtk
        display = display or Gdk.Display.get_default()
        if display is None or id(display) in _installed:
            return
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        _installed.add(id(display))
    except Exception:  # noqa: BLE001 — styling must never break the page
        log.exception("sessions CSS failed to load")
