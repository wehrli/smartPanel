"""Render `IconSpec` (A6 strip + A4 graphs) to a wide PNG with pycairo.

Why a file (decision D2 in docs/architecture/v2.md): the Ubuntu AppIndicator
extension keeps an icon's aspect ratio only when the SNI `IconName` is an absolute
path to an image at least 1.5x wider than tall, inside a user-writable directory
(`_loadCustomImage` / `_isFileInWritableArea` in its appIndicator.js). Pixmap icons
are forced square. The extension also caches textures by file, so every distinct
image gets a new name: `bar-<content hash>.png`.

Sizing: the extension sizes the actor from the image's *pixel* dimensions times the
shell's integer scale factor, so one image pixel is one logical pixel in the panel.
The tray therefore renders at `scale=1` (a 2x image would show twice as tall). The
`scale` argument exists for in-app previews (Settings, SP-1908), where a GTK4
texture drawn at half size gives a crisp HiDPI strip.

Colours follow the prototype after the user's fixes: no green anywhere, the memory
meter is white and the swap meter light grey (amber/red with the alert) so the two
can never merge, and neither looks like a toggle switch.
"""
from __future__ import annotations

import hashlib
import io
import logging
import math
import os
import tempfile
from pathlib import Path

import cairo

from smartpanel.state.model import LEVEL_CRITICAL, LEVEL_WARNING
from smartpanel.tray.barspec import (
    HIST_SAMPLES, IDLE, JOURNALING, LAUNCHING, RUNNING, WAITING, IconSpec,
)

log = logging.getLogger(__name__)

# ── geometry, in logical pixels ──
HEIGHT = 16
MIN_ASPECT = 1.5                      # the extension's threshold for a wide image
GROUP_GAP = 6
BLOCK_W, BLOCK_H, BLOCK_GAP, BLOCK_R = 7, 12, 2, 2
BLOCK_Y = HEIGHT - BLOCK_H            # room above for the waiting notch
NOTCH_R = 1.5
MORE_FONT_PX = 11
BAR_W, BAR_GAP = 3, 1
METER_W, METER_GAP, METER_R = 5, 2, 2
HIST_METER_GAP = 3

# ── colours (prototype CSS) ──
def _rgb(hexs: str) -> tuple[float, float, float]:
    h = hexs.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


COLORS = {
    WAITING: "#f5a623",
    RUNNING: "#62a0ea",
    IDLE: "#5e5e5e",
    LAUNCHING: "#9a9a9a",        # outline only
    JOURNALING: "#c061cb",
    "more": "#bbbbbb",
    "cpu": "#e95420",
    "meter_bg": "#444444",
    "mem": "#ffffff",
    "swap_ok": "#bdbdbd",
    "swap_warn": "#f5a623",
    "swap_crit": "#e01b24",
}


def swap_color(level: int) -> str:
    if level >= LEVEL_CRITICAL:
        return COLORS["swap_crit"]
    if level >= LEVEL_WARNING:
        return COLORS["swap_warn"]
    return COLORS["swap_ok"]


# ───────────────────────── layout (pure geometry, testable) ─────────────────────────

def _more_text(spec: IconSpec) -> str:
    return f"+{spec.strip_more}" if spec.strip_more else ""


def _text_width(text: str) -> float:
    if not text:
        return 0.0
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
    cr = cairo.Context(surf)
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(MORE_FONT_PX)
    return cr.text_extents(text).x_advance


def layout(spec: IconSpec) -> dict:
    """x offsets of every group, before left padding. Returns widths in logical px."""
    x = 0.0
    out: dict = {"strip_x": None, "more_x": None, "hist_x": None, "meters_x": None}
    groups = 0
    if spec.strip or spec.strip_more:
        out["strip_x"] = x
        x += len(spec.strip) * BLOCK_W + max(0, len(spec.strip) - 1) * BLOCK_GAP
        more = _more_text(spec)
        if more:
            x += 2
            out["more_x"] = x
            x += math.ceil(_text_width(more))
        groups += 1
    if spec.graphs:
        if groups:
            x += GROUP_GAP
        out["hist_x"] = x
        x += HIST_SAMPLES * BAR_W + (HIST_SAMPLES - 1) * BAR_GAP
        x += HIST_METER_GAP
        out["meters_x"] = x
        x += 2 * METER_W + METER_GAP
    content = math.ceil(x)
    width = max(content, math.ceil(HEIGHT * MIN_ASPECT))
    out["pad"] = width - content           # transparent padding on the left
    out["width"] = width
    out["height"] = HEIGHT
    return out


def _rounded(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
    r = max(0.0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def cpu_bar_height(v: float) -> int:
    """Prototype: clamp(round(v/100*16), 2, 16)."""
    return int(min(HEIGHT, max(2, math.floor(v / 100 * HEIGHT + 0.5))))


def draw(cr: cairo.Context, spec: IconSpec) -> None:
    """Draw at logical size onto `cr` (caller sets the scale)."""
    lay = layout(spec)
    pad = lay["pad"]
    if lay["strip_x"] is not None:
        x = pad + lay["strip_x"]
        for kind in spec.strip:
            if kind == LAUNCHING:
                cr.set_source_rgb(*_rgb(COLORS[LAUNCHING]))
                cr.set_line_width(1.5)
                _rounded(cr, x + 0.75, BLOCK_Y + 0.75, BLOCK_W - 1.5, BLOCK_H - 1.5, BLOCK_R)
                cr.stroke()
            else:
                cr.set_source_rgb(*_rgb(COLORS.get(kind, COLORS[IDLE])))
                _rounded(cr, x, BLOCK_Y, BLOCK_W, BLOCK_H, BLOCK_R)
                cr.fill()
                if kind == WAITING:   # notch: not colour-only
                    cr.arc(x + BLOCK_W / 2, BLOCK_Y - NOTCH_R - 1, NOTCH_R, 0, 2 * math.pi)
                    cr.fill()
            x += BLOCK_W + BLOCK_GAP
        more = _more_text(spec)
        if more and lay["more_x"] is not None:
            cr.set_source_rgb(*_rgb(COLORS["more"]))
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(MORE_FONT_PX)
            cr.move_to(pad + lay["more_x"], BLOCK_Y + BLOCK_H - 1)
            cr.show_text(more)
            cr.new_path()
    if spec.graphs:
        hx = pad + lay["hist_x"]
        cr.set_source_rgb(*_rgb(COLORS["cpu"]))
        samples = list(spec.cpu)[-HIST_SAMPLES:]
        offset = HIST_SAMPLES - len(samples)      # right-align a short history
        for i, v in enumerate(samples):
            h = cpu_bar_height(v)
            cr.rectangle(hx + (offset + i) * (BAR_W + BAR_GAP), HEIGHT - h, BAR_W, h)
        cr.fill()
        mx = pad + lay["meters_x"]
        for j, (value, color) in enumerate(((spec.mem_pct, COLORS["mem"]),
                                            (spec.swap_pct, swap_color(spec.swap_level)))):
            x = mx + j * (METER_W + METER_GAP)
            cr.save()
            _rounded(cr, x, 0, METER_W, HEIGHT, METER_R)
            cr.clip()
            cr.set_source_rgb(*_rgb(COLORS["meter_bg"]))
            cr.paint()
            if value:
                fill = max(0.0, min(100.0, value)) / 100 * HEIGHT
                cr.set_source_rgb(*_rgb(color))
                cr.rectangle(x, HEIGHT - fill, METER_W, fill)
                cr.fill()
            cr.restore()


def render_png(spec: IconSpec, scale: int = 1) -> bytes:
    lay = layout(spec)
    w, h = lay["width"] * scale, lay["height"] * scale
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    cr.scale(scale, scale)
    draw(cr, spec)
    buf = io.BytesIO()
    surf.write_to_png(buf)
    return buf.getvalue()


# ───────────────────────── files ─────────────────────────

def default_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base and os.path.isdir(base):
        return Path(base) / "smartpanel"
    return Path(tempfile.gettempdir()) / f"smartpanel-{os.getuid()}"


class IconWriter:
    """Writes `bar-<hash>.png` files and deletes stale ones.

    Keeps the current and the previous image: the shell may still be loading the
    previous name when the next one is announced. Everything else matching
    `bar-*.png` (including leftovers from an earlier run) is removed.
    """
    PREFIX = "bar-"

    def __init__(self, directory: Path | str | None = None, scale: int = 1):
        self.directory = Path(directory) if directory else default_dir()
        self.scale = scale
        self.current: Path | None = None
        self.previous: Path | None = None
        self._last_spec: IconSpec | None = None

    def write(self, spec: IconSpec) -> Path:
        if spec == self._last_spec and self.current is not None and self.current.exists():
            return self.current
        data = render_png(spec, self.scale)
        digest = hashlib.sha1(data).hexdigest()[:16]
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.directory / f"{self.PREFIX}{digest}.png"
        if not path.exists():
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)       # atomic: the shell never reads half a PNG
        if path != self.current:
            self.previous, self.current = self.current, path
        self._last_spec = spec
        self.cleanup()
        return path

    def cleanup(self, keep_none: bool = False) -> None:
        keep = set() if keep_none else {p for p in (self.current, self.previous) if p}
        try:
            for p in self.directory.glob(f"{self.PREFIX}*.png"):
                if p not in keep:
                    try:
                        p.unlink()
                    except OSError:
                        pass
        except OSError as e:
            log.debug("bar icon cleanup failed: %s", e)
        if keep_none:
            self.current = self.previous = None
            self._last_spec = None
