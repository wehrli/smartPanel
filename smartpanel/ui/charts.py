"""Chart geometry (pure) and cairo painters (SP-0010).

The geometry functions are GTK-free and cairo-free so they unit-test without a
display: values in, coordinates out. Only the `paint_*` functions touch cairo, and
they take a cairo context plus a colour dict, so the widgets in `ui/widgets` are
thin `Gtk.DrawingArea` wrappers.

The maths mirrors the prototype's `spark()` and `bars()` (docs/design/prototype.html):

- sparkline: 2 px side inset, 2 px bottom inset, 4 px of headroom at the top,
  auto-scale to 115 % of the peak unless a fixed `vmax` (e.g. 100 for percents)
  is given;
- bars: auto-scale to 110 % of the peak, 1.5 px gap, the "now" bar highlighted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

Point = tuple[float, float]

SIDE_PAD = 2.0      # left/right inset so the line's caps and the end dot aren't clipped
BOTTOM_PAD = 2.0
TOP_PAD = 4.0       # headroom: (h - BOTTOM_PAD - TOP_PAD) is the drawable height
AUTO_HEADROOM = 1.15
BAR_HEADROOM = 1.10
BAR_GAP = 1.5


def _clean(values: Sequence[float | None]) -> list[float]:
    """Drop unknowns; a `None` sample is a gap in the data, not a zero."""
    return [float(v) for v in values if v is not None]


def scale_max(values: Sequence[float | None], vmax: float | None = None) -> float:
    """The value mapped to the top of the plot."""
    if vmax is not None and vmax > 0:
        return float(vmax)
    vals = _clean(values)
    return max(vals + [1.0]) * AUTO_HEADROOM


def value_y(value: float, height: float, vmax: float) -> float:
    """y (top-down) of a value, clamped to the plot so outliers stay visible."""
    span = height - BOTTOM_PAD - TOP_PAD
    frac = min(max(value / vmax, 0.0), 1.0) if vmax > 0 else 0.0
    return height - BOTTOM_PAD - frac * span


def sparkline_points(values: Sequence[float | None], width: float, height: float,
                     vmax: float | None = None) -> list[Point]:
    """Polyline points, oldest left. One sample is drawn as a flat line."""
    vals = _clean(values)
    if not vals or width <= 0 or height <= 0:
        return []
    top = scale_max(vals, vmax)
    if len(vals) == 1:
        y = value_y(vals[0], height, top)
        return [(SIDE_PAD, y), (width - SIDE_PAD, y)]
    step = (width - 2 * SIDE_PAD) / (len(vals) - 1)
    return [(SIDE_PAD + i * step, value_y(v, height, top)) for i, v in enumerate(vals)]


def area_polygon(points: Sequence[Point], height: float) -> list[Point]:
    """The filled area under a sparkline: the line, then down to the baseline."""
    if not points:
        return []
    return [*points, (points[-1][0], height), (points[0][0], height)]


def endpoint(points: Sequence[Point]) -> Point | None:
    """Where the 'now' dot goes."""
    return points[-1] if points else None


def threshold_ys(thresholds: Sequence[tuple[float, str]], height: float,
                 vmax: float) -> list[tuple[float, str]]:
    """(y, kind) for each dashed threshold line, e.g. [(60, "warn"), (85, "crit")]."""
    return [(value_y(v, height, vmax), kind) for v, kind in thresholds if v is not None]


@dataclass(frozen=True)
class BarRect:
    index: int
    x: float
    y: float
    width: float
    height: float
    highlight: bool = False


def bar_rects(values: Sequence[float | None], width: float, height: float,
              highlight: int | None = -1, vmax: float | None = None) -> list[BarRect]:
    """One rect per non-zero value. `highlight` is the "now" index (-1 = last)."""
    vals = [0.0 if v is None else max(float(v), 0.0) for v in values]
    if not vals or width <= 0 or height <= 0:
        return []
    top = vmax if vmax and vmax > 0 else max(vals + [0.01]) * BAR_HEADROOM
    bw = width / len(vals)
    hi = None if highlight is None else (highlight % len(vals))
    rects = []
    for i, v in enumerate(vals):
        bh = min(v / top, 1.0) * (height - BOTTOM_PAD)
        if bh <= 0:
            continue
        rects.append(BarRect(i, i * bw + 0.5, height - bh, max(1.0, bw - BAR_GAP), bh,
                             highlight=(i == hi)))
    return rects


# ───────────────────────── painting (cairo) ─────────────────────────

def hex_rgba(color: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    """'#e95420' -> (r, g, b, a) in 0..1, as cairo wants."""
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    r, g, b = (int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return r, g, b, alpha


def paint_sparkline(cr, width: float, height: float, values: Sequence[float | None], *,
                    colors: dict[str, str], vmax: float | None = None,
                    thresholds: Sequence[tuple[float, str]] = (),
                    fill_alpha: float = 0.22) -> None:
    """Area + line + end dot + dashed thresholds. `colors` keys: line, dot, warn, crit."""
    top = scale_max(values, vmax)
    cr.set_line_width(1.0)
    for y, kind in threshold_ys(thresholds, height, top):
        cr.set_source_rgba(*hex_rgba(colors.get(kind, colors["line"])))
        cr.set_dash([3.0, 3.0])
        cr.move_to(0, round(y) + 0.5)
        cr.line_to(width, round(y) + 0.5)
        cr.stroke()
    cr.set_dash([])
    pts = sparkline_points(values, width, height, vmax)
    if not pts:
        return
    area = area_polygon(pts, height)
    cr.move_to(*area[0])
    for p in area[1:]:
        cr.line_to(*p)
    cr.close_path()
    cr.set_source_rgba(*hex_rgba(colors["line"], fill_alpha))
    cr.fill()
    cr.move_to(*pts[0])
    for p in pts[1:]:
        cr.line_to(*p)
    cr.set_source_rgba(*hex_rgba(colors["line"]))
    cr.set_line_width(1.6)
    cr.set_line_join(1)  # ROUND
    cr.stroke()
    ex, ey = endpoint(pts)
    cr.arc(ex, ey, 2.4, 0, 6.283185307179586)
    cr.set_source_rgba(*hex_rgba(colors.get("dot", colors["line"])))
    cr.fill()


def paint_bars(cr, width: float, height: float, values: Sequence[float | None], *,
               colors: dict[str, str], highlight: int | None = -1,
               vmax: float | None = None) -> None:
    """Rounded bars; the highlighted one uses colors['now']."""
    for r in bar_rects(values, width, height, highlight, vmax):
        cr.set_source_rgba(*hex_rgba(colors["now"] if r.highlight else colors["bar"]))
        _rounded_rect(cr, r.x, r.y, r.width, r.height, min(1.0, r.width / 2))
        cr.fill()


def _rounded_rect(cr, x: float, y: float, w: float, h: float, rad: float) -> None:
    import math
    if rad <= 0 or h < 2 * rad:
        cr.rectangle(x, y, w, h)
        return
    cr.new_sub_path()
    cr.arc(x + w - rad, y + rad, rad, -math.pi / 2, 0)
    cr.arc(x + w - rad, y + h - rad, rad, 0, math.pi / 2)
    cr.arc(x + rad, y + h - rad, rad, math.pi / 2, math.pi)
    cr.arc(x + rad, y + rad, rad, math.pi, 3 * math.pi / 2)
    cr.close_path()
