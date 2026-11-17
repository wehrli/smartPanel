"""Design tokens (SP-0008): one palette for the window, palette, dialogs and charts.

The colours come from the prototype (docs/design/prototype.html, `.win` and
`.win.light`). They live here, in Python, rather than only in `style.css` because
the cairo chart painters need them too, and because GTK's own
`prefers-color-scheme` media query does not follow libadwaita's forced
light/dark: the palette is emitted as CSS custom properties (`--sp-*`) and the
matching block is swapped in whenever `Adw.StyleManager:dark` flips.
`style.css` holds the structure and only ever refers to `var(--sp-*)`.

`install(display)` is idempotent: CSS is loaded once per process.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

ACCENT = "#E95420"
STYLE_CSS = Path(__file__).with_name("style.css")
MONO_FAMILY = '"Ubuntu Sans Mono", "Ubuntu Mono", monospace'

# Palette per mode. Text tokens (fg, muted, wait, run, idle, jour, crit, ok) are
# checked for WCAG AA (4.5:1) against `bg` and `card` in tests/test_tokens.py.
PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#242424", "side": "#2e2e2e", "card": "#363636", "card2": "#3e3e3e",
        "fg": "#ffffff", "muted": "#a6a6a6", "line": "rgba(255,255,255,0.09)",
        "btn": "#454545", "sel": "rgba(255,255,255,0.10)",
        "wait": "#f6b73c", "run": "#78aeed", "idle": "#a0a0a0", "jour": "#dc8add",
        "crit": "#ff7b7b", "ok": "#57e389", "warn": "#f6b73c",
        "waitbg": "rgba(245,166,35,0.13)",
        "accent": ACCENT, "accent_text": "#ff8a5c",
        "banner_warn_bg": "#7a4f00", "banner_warn_fg": "#ffffff",
        "banner_crit_bg": "#a51d2d", "banner_crit_fg": "#ffffff",
        "toast_bg": "#1e1e1e", "toast_err_bg": "#a51d2d", "toast_fg": "#ffffff",
        "chart_dot": "#ffffff",
    },
    "light": {
        "bg": "#fafafa", "side": "#ebebeb", "card": "#ffffff", "card2": "#f1f1f1",
        "fg": "#1e1e1e", "muted": "#5e5c64", "line": "rgba(0,0,0,0.10)",
        "btn": "#dedede", "sel": "rgba(0,0,0,0.07)",
        "wait": "#8a5a00", "run": "#1a64c0", "idle": "#5e5c64", "jour": "#813d9c",
        "crit": "#c01c28", "ok": "#1b7a4c", "warn": "#8a5a00",
        "waitbg": "#fdf0d5",
        "accent": ACCENT, "accent_text": "#b83a0e",
        "banner_warn_bg": "#f6d32d", "banner_warn_fg": "#3d2c00",
        "banner_crit_bg": "#a51d2d", "banner_crit_fg": "#ffffff",
        "toast_bg": "#303030", "toast_err_bg": "#a51d2d", "toast_fg": "#ffffff",
        "chart_dot": "#1e1e1e",
    },
}

# Mode-independent fills with their own text colour (pills, chips, badges).
# Each (bg, fg) pair is AA-checked in tests.
FILLS: dict[str, tuple[str, str]] = {
    "pill_waiting": ("#c7870b", "#1a1000"),
    "pill_running": ("#1c4f80", "#d3e8ff"),
    "pill_journaling": ("#613583", "#f1dcf6"),
    "chip_warn": ("#c7870b", "#1a1000"),
    "chip_crit": ("#c01c28", "#ffffff"),
    "badge_amber": ("#f5a623", "#1a1000"),
    "badge_red": ("#c01c28", "#ffffff"),
}

# Borders and chart strokes (non-text, so 3:1 is the bar, not 4.5:1).
LEVEL_WARN_BORDER = "#c7870b"
LEVEL_CRIT_BORDER = "#e01b24"
CHART_BAR = ACCENT
CHART_BAR_NOW = "#ffb36b"


def palette(dark: bool) -> dict[str, str]:
    return PALETTES["dark" if dark else "light"]


def chart_colors(dark: bool) -> dict[str, str]:
    """Colours for ui.charts painters."""
    p = palette(dark)
    return {"line": ACCENT, "dot": p["chart_dot"], "warn": LEVEL_WARN_BORDER,
            "crit": LEVEL_CRIT_BORDER, "bar": CHART_BAR, "now": CHART_BAR_NOW}


def palette_css(dark: bool) -> str:
    """The `--sp-*` custom properties for one mode, as a `:root` block."""
    p = palette(dark)
    props = [f"--sp-{k.replace('_', '-')}: {v};" for k, v in p.items()]
    for name, (bg, fg) in FILLS.items():
        key = name.replace("_", "-")
        props.append(f"--sp-{key}-bg: {bg};")
        props.append(f"--sp-{key}-fg: {fg};")
    props += [f"--sp-level-warn: {LEVEL_WARN_BORDER};",
              f"--sp-level-crit: {LEVEL_CRIT_BORDER};",
              f"--sp-mono: {MONO_FAMILY};"]
    return ":root {\n  " + "\n  ".join(props) + "\n}\n"


# ── WCAG maths (used by the tests and handy when picking new colours) ──

def _channel(c: float) -> float:
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    c = color.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# ───────────────────────── GTK side ─────────────────────────

_installed = False
_palette_provider = None


def install(display=None) -> None:
    """Load style.css + the palette for the current mode, once per process.

    Safe to call from every window constructor; only the first call does work.
    """
    global _installed, _palette_provider
    if _installed:
        return
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gtk

    display = display or Gdk.Display.get_default()
    if display is None:
        log.warning("no display; styles not loaded")
        return
    structure = Gtk.CssProvider()
    try:
        structure.load_from_path(str(STYLE_CSS))
    except Exception:  # noqa: BLE001 — a CSS typo must not stop the app
        log.exception("failed to load %s", STYLE_CSS)
    prio = Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    Gtk.StyleContext.add_provider_for_display(display, structure, prio)

    _palette_provider = Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_display(display, _palette_provider, prio + 1)
    manager = Adw.StyleManager.get_default()

    def apply(*_):
        _palette_provider.load_from_string(palette_css(manager.get_dark()))

    manager.connect("notify::dark", apply)
    apply()
    _installed = True


def is_dark() -> bool:
    """Whether the app currently renders dark (False when no GTK / display)."""
    try:
        import gi
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        return bool(Adw.StyleManager.get_default().get_dark())
    except Exception:  # noqa: BLE001
        return False


def color_scheme_for(style: str | None):
    """Map the `appearance.style` setting to an Adw.ColorScheme."""
    import gi
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    return {
        "light": Adw.ColorScheme.FORCE_LIGHT,
        "dark": Adw.ColorScheme.FORCE_DARK,
    }.get((style or "system").lower(), Adw.ColorScheme.DEFAULT)
