#!/usr/bin/env python3
"""Render what the top-bar item looks like, per demo scenario, to PNG (SP-2701).

    python scripts/bar_preview.py --out docs/screenshots/bar.png
    python scripts/bar_preview.py --out /tmp/bar.png --scenarios busy alert --ticker

Each row is a slice of a black GNOME panel with the item exactly as the tray builds
it: `tray.barspec.compose_bar` for the label, `tray.barrender.draw` for the wide
icon (session strip + CPU histogram + memory / swap meters). No GTK and no display:
pycairo + Pango only. The label font is the panel's (Ubuntu / Cantarell when installed);
GNOME draws the real label, so spacing can differ by a pixel or two.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402

from smartpanel import demo  # noqa: E402
from smartpanel.tray import barrender  # noqa: E402
from smartpanel.tray.barspec import compose_bar  # noqa: E402

SCALE = 2                   # HiDPI output; one logical px = 2 image px
ROW_H = 32                  # logical: GNOME's panel height
PAD_X = 14
CAPTION_W = 150
LABEL_FONT = "Ubuntu, Cantarell, Sans 11"   # GNOME panel: 11 pt
PANEL = (0.0, 0.0, 0.0)
FG = (1.0, 1.0, 1.0)
CAPTION = (0.62, 0.62, 0.62)


def _layout(cr: cairo.Context, text: str) -> Pango.Layout:
    """A Pango layout (font fallback included: ⚠, ●○ come from an emoji/symbol font)."""
    lay = PangoCairo.create_layout(cr)
    PangoCairo.context_set_resolution(lay.get_context(), 96)
    lay.set_font_description(Pango.FontDescription.from_string(LABEL_FONT))
    lay.set_text(text, -1)
    return lay


def _text_w(cr: cairo.Context, text: str) -> float:
    return _layout(cr, text).get_pixel_extents()[1].width if text else 0


def _show(cr: cairo.Context, x: float, y_mid: float, text: str) -> None:
    lay = _layout(cr, text)
    h = lay.get_pixel_extents()[1].height
    cr.move_to(x, y_mid - h / 2)
    PangoCairo.show_layout(cr, lay)


def rows_for(scenarios: list[str], ticker: bool) -> list[tuple[str, object]]:
    out = []
    for name in scenarios:
        st = demo.build_state(name)
        out.append((name, compose_bar(st, {"bar.ticker": False})))
        if ticker:
            out.append((f"{name} · ticker", compose_bar(st, {"bar.ticker": True})))
    return out


def render(rows, path: Path) -> None:
    # Measure first: the widest row decides the image width.
    probe = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
    widths = []
    for _, spec in rows:
        icon_w = barrender.layout(spec.icon)["width"] if spec.icon else 16
        text_w = _text_w(probe, spec.label)
        widths.append(icon_w + (8 if spec.label else 0) + text_w)
    width = CAPTION_W + PAD_X * 2 + int(max(widths)) + 24
    height = ROW_H * len(rows)

    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, width * SCALE, height * SCALE)
    cr = cairo.Context(surf)
    cr.scale(SCALE, SCALE)
    for i, (caption, spec) in enumerate(rows):
        y0 = i * ROW_H
        cr.set_source_rgb(*PANEL)
        cr.rectangle(0, y0, width, ROW_H)
        cr.fill()
        if i:
            cr.set_source_rgb(0.16, 0.16, 0.16)
            cr.rectangle(0, y0, width, 1)
            cr.fill()
        cr.set_source_rgb(*CAPTION)
        _show(cr, PAD_X, y0 + ROW_H / 2, caption)

        x = PAD_X + CAPTION_W
        if spec.icon is not None:
            lay = barrender.layout(spec.icon)
            cr.save()
            cr.translate(x, y0 + (ROW_H - lay["height"]) / 2)
            barrender.draw(cr, spec.icon)
            cr.restore()
            x += lay["width"] + 8
        else:                                   # loading / plain: a stand-in glyph
            cr.set_source_rgb(*FG)
            cr.new_sub_path()
            cr.arc(x + 7, y0 + ROW_H / 2, 5.5, 0.6, 5.6)
            cr.set_line_width(1.6)
            cr.stroke()
            x += 16 + 8
        if spec.label:
            cr.set_source_rgb(*FG)
            _show(cr, x, y0 + ROW_H / 2, spec.label)
    path.parent.mkdir(parents=True, exist_ok=True)
    surf.write_to_png(str(path))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--scenarios", nargs="+", default=["busy", "alert", "many", "cold", "notmux"],
                   choices=demo.SCENARIOS, metavar="SCENARIO")
    p.add_argument("--ticker", action="store_true", help="add the A5 ticker variant of each")
    ns = p.parse_args(argv)
    render(rows_for(ns.scenarios, ns.ticker), ns.out)
    print(ns.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
