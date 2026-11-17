"""SP-1205, SP-0203, SP-0302, SP-0104 (meter), SP-1905: the wide PNG bar icon."""
from __future__ import annotations

import struct

import cairo
import pytest

from smartpanel.tray import barrender
from smartpanel.tray.barrender import COLORS, HEIGHT, IconWriter, layout, render_png
from smartpanel.tray.barspec import IconSpec, compose_bar
from tests.fixtures import tray_states as fx


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def load(path) -> cairo.ImageSurface:
    return cairo.ImageSurface.create_from_png(str(path))


def pixel(surf: cairo.ImageSurface, x: int, y: int) -> tuple[int, int, int, int]:
    """(r, g, b, a) un-premultiplied, 0..255."""
    data = surf.get_data()
    off = y * surf.get_stride() + x * 4
    b, g, r, a = data[off], data[off + 1], data[off + 2], data[off + 3]
    if a == 0:
        return (0, 0, 0, 0)
    return (round(r * 255 / a), round(g * 255 / a), round(b * 255 / a), a)


def hexrgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def close(px, color, tol=6):
    return px[3] == 255 and all(abs(a - b) <= tol for a, b in zip(px[:3], hexrgb(color)))


def render(spec, tmp_path, scale=1):
    w = IconWriter(tmp_path, scale=scale)
    return load(w.write(spec)), layout(spec)


# ── aspect ratio (D2) ──

@pytest.mark.parametrize("spec", [
    IconSpec(strip=("idle",)),
    IconSpec(strip=("waiting", "running")),
    IconSpec(graphs=True, cpu=(10.0,) * 10, mem_pct=50.0, swap_pct=10.0),
    IconSpec(strip=("waiting",) * 6, strip_more=3, graphs=True, cpu=(50.0,) * 10,
             mem_pct=50.0, swap_pct=90.0, swap_level=2),
])
@pytest.mark.parametrize("scale", [1, 2])
def test_png_is_wide_enough(spec, scale):
    w, h = png_size(render_png(spec, scale))
    assert h == HEIGHT * scale
    assert w >= 1.5 * h


def test_tiny_strip_padded_on_the_left(tmp_path):
    spec = IconSpec(strip=("running",))
    surf, lay = render(spec, tmp_path)
    assert lay["pad"] > 0
    assert pixel(surf, 0, 10)[3] == 0                          # transparent padding
    assert close(pixel(surf, lay["pad"] + 3, 10), COLORS["running"])


# ── strip colours ──

def test_strip_block_colours_and_notch(tmp_path):
    kinds = ("waiting", "running", "idle", "journaling", "launching")
    surf, lay = render(IconSpec(strip=kinds), tmp_path)
    x0 = lay["pad"] + lay["strip_x"]
    step = barrender.BLOCK_W + barrender.BLOCK_GAP
    mid_y = barrender.BLOCK_Y + barrender.BLOCK_H // 2
    for i, kind in enumerate(kinds[:4]):
        assert close(pixel(surf, int(x0 + i * step + 3), mid_y), COLORS[kind]), kind
    # waiting has a notch above its block; running doesn't
    notch_y = int(barrender.BLOCK_Y - barrender.NOTCH_R - 1)
    assert pixel(surf, int(x0 + 3), notch_y)[3] > 0
    assert pixel(surf, int(x0 + step + 3), notch_y)[3] == 0
    # launching is outlined: edge drawn, centre empty
    lx = int(x0 + 4 * step)
    assert pixel(surf, lx + 3, mid_y)[3] == 0
    assert pixel(surf, lx, mid_y)[3] > 0


def test_strip_more_text_drawn(tmp_path):
    spec = IconSpec(strip=("idle",) * 6, strip_more=3)
    surf, lay = render(spec, tmp_path)
    assert lay["more_x"] is not None
    xs = range(int(lay["pad"] + lay["more_x"]), surf.get_width())
    assert any(pixel(surf, x, y)[3] > 0 for x in xs for y in range(HEIGHT))


# ── graphs ──

def test_cpu_histogram_heights(tmp_path):
    cpu = (0.0, 10.0, 25.0, 50.0, 75.0, 100.0, 100.0, 100.0, 100.0, 50.0)
    surf, lay = render(IconSpec(graphs=True, cpu=cpu, mem_pct=0.0, swap_pct=0.0), tmp_path)
    hx = lay["pad"] + lay["hist_x"]
    for i, v in enumerate(cpu):
        x = int(hx + i * (barrender.BAR_W + barrender.BAR_GAP)) + 1
        h = barrender.cpu_bar_height(v)
        assert close(pixel(surf, x, HEIGHT - 1), COLORS["cpu"])
        assert close(pixel(surf, x, HEIGHT - h), COLORS["cpu"])
        if h < HEIGHT:
            assert pixel(surf, x, HEIGHT - h - 1)[3] == 0


def test_cpu_bar_height_clamps():
    assert barrender.cpu_bar_height(0) == 2
    assert barrender.cpu_bar_height(100) == HEIGHT
    assert barrender.cpu_bar_height(50) == 8


def _meter_px(surf, lay, which, y):
    x = int(lay["pad"] + lay["meters_x"] + which * (barrender.METER_W + barrender.METER_GAP)) + 2
    return pixel(surf, x, y)


@pytest.mark.parametrize("level,color", [(0, "swap_ok"), (1, "swap_warn"), (2, "swap_crit")])
def test_meters_memory_white_swap_by_level(tmp_path, level, color):
    spec = IconSpec(graphs=True, cpu=(10.0,) * 10, mem_pct=50.0, swap_pct=50.0, swap_level=level)
    surf, lay = render(spec, tmp_path)
    assert close(_meter_px(surf, lay, 0, HEIGHT - 2), COLORS["mem"])
    assert close(_meter_px(surf, lay, 1, HEIGHT - 2), COLORS[color])
    # above the fill: the dark track
    assert close(_meter_px(surf, lay, 0, 3), COLORS["meter_bg"])
    assert close(_meter_px(surf, lay, 1, 3), COLORS["meter_bg"])


def test_memory_and_swap_never_same_colour():
    for level in (0, 1, 2):
        assert barrender.swap_color(level).lower() != COLORS["mem"].lower()


def test_no_green_anywhere():
    for c in COLORS.values():
        r, g, b = hexrgb(c)
        assert not (g > r + 30 and g > b + 30), c


def test_no_swap_meter_is_empty(tmp_path):
    spec = IconSpec(graphs=True, cpu=(10.0,) * 10, mem_pct=50.0, swap_pct=None)
    surf, lay = render(spec, tmp_path)
    assert close(_meter_px(surf, lay, 1, HEIGHT - 2), COLORS["meter_bg"])


def test_scale_two_doubles_pixels(tmp_path):
    spec = IconSpec(strip=("running",), graphs=True, cpu=(50.0,) * 10, mem_pct=40.0, swap_pct=5.0)
    w1, h1 = png_size(render_png(spec, 1))
    w2, h2 = png_size(render_png(spec, 2))
    assert (w2, h2) == (2 * w1, 2 * h1)


def test_composed_specs_render(tmp_path):
    for make in fx.SCENARIOS.values():
        icon = compose_bar(make(), None).icon
        if icon is not None:
            w, h = png_size(render_png(icon))
            assert w >= 1.5 * h


# ── files ──

def test_writer_names_by_content_and_reuses(tmp_path):
    w = IconWriter(tmp_path)
    a = IconSpec(strip=("idle",))
    p1 = w.write(a)
    assert p1.name.startswith("bar-") and p1.suffix == ".png" and p1.parent == tmp_path
    assert w.write(a) == p1
    w2 = IconWriter(tmp_path)
    assert w2.write(a) == p1                     # same content -> same name


def test_writer_deletes_stale_files_keeps_previous(tmp_path):
    (tmp_path / "bar-leftover.png").write_bytes(b"old")
    (tmp_path / "unrelated.txt").write_text("keep")
    w = IconWriter(tmp_path)
    p1 = w.write(IconSpec(strip=("idle",)))
    assert not (tmp_path / "bar-leftover.png").exists()
    p2 = w.write(IconSpec(strip=("running",)))
    p3 = w.write(IconSpec(strip=("waiting",)))
    assert p1 != p2 != p3
    assert not p1.exists() and p2.exists() and p3.exists()
    assert (tmp_path / "unrelated.txt").exists()
    assert sorted(tmp_path.glob("bar-*.png")) == sorted([p2, p3])


def test_writer_cleanup_all(tmp_path):
    w = IconWriter(tmp_path)
    w.write(IconSpec(strip=("idle",)))
    w.cleanup(keep_none=True)
    assert list(tmp_path.glob("bar-*.png")) == []
    assert w.current is None


def test_default_dir_under_runtime_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert barrender.default_dir() == tmp_path / "smartpanel"
