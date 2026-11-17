"""Chart geometry (SP-0010): pure maths, no cairo, no display."""
import pytest

from smartpanel.ui import charts


def test_sparkline_spans_the_width_with_side_insets():
    pts = charts.sparkline_points([0, 50, 100], 104, 30, vmax=100)
    assert [x for x, _ in pts] == [2.0, 52.0, 102.0]


def test_sparkline_y_maps_value_into_padded_height():
    # h=30: bottom inset 2, top headroom 4 -> 0 at y=28, vmax at y=4
    pts = charts.sparkline_points([0, 100], 50, 30, vmax=100)
    assert pts[0][1] == pytest.approx(28.0)
    assert pts[1][1] == pytest.approx(4.0)


def test_sparkline_autoscale_leaves_headroom_like_the_prototype():
    # prototype: max = max(values, 1) * 1.15
    assert charts.scale_max([10, 20]) == pytest.approx(23.0)
    pts = charts.sparkline_points([20], 50, 30)
    assert pts[0][1] > 4.0  # the peak never touches the top


def test_sparkline_clamps_outliers_and_skips_unknowns():
    pts = charts.sparkline_points([None, 150, -5, None], 50, 30, vmax=100)
    assert len(pts) == 2
    assert pts[0][1] == pytest.approx(4.0)   # clamped to top
    assert pts[1][1] == pytest.approx(28.0)  # clamped to bottom


def test_sparkline_single_sample_is_a_flat_line():
    pts = charts.sparkline_points([50], 100, 30, vmax=100)
    assert len(pts) == 2 and pts[0][1] == pts[1][1]
    assert pts[0][0] == 2.0 and pts[1][0] == 98.0


@pytest.mark.parametrize("values,w,h", [([], 100, 30), ([None], 100, 30), ([1, 2], 0, 30)])
def test_sparkline_degenerate_inputs_draw_nothing(values, w, h):
    assert charts.sparkline_points(values, w, h) == []
    assert charts.endpoint([]) is None
    assert charts.area_polygon([], 30) == []


def test_area_closes_down_to_the_baseline():
    pts = charts.sparkline_points([10, 20, 30], 60, 30, vmax=100)
    area = charts.area_polygon(pts, 30)
    assert area[:3] == pts
    assert area[3] == (pts[-1][0], 30)
    assert area[4] == (pts[0][0], 30)


def test_endpoint_is_the_newest_sample():
    pts = charts.sparkline_points([10, 20, 90], 60, 30, vmax=100)
    assert charts.endpoint(pts) == pts[-1]


def test_threshold_lines_sit_where_their_values_would():
    ys = charts.threshold_ys([(60, "warn"), (85, "crit")], 30, 100)
    assert [k for _, k in ys] == ["warn", "crit"]
    assert ys[0][1] == "warn" and ys[0][0] == pytest.approx(charts.value_y(60, 30, 100))
    assert ys[1][0] < ys[0][0]  # critical is higher up


def test_bar_rects_skip_zero_hours_and_highlight_now():
    rects = charts.bar_rects([0, 1, 2, 0, 4], 50, 20)
    assert [r.index for r in rects] == [1, 2, 4]
    assert [r.highlight for r in rects] == [False, False, True]
    assert all(r.y + r.height == pytest.approx(20) for r in rects)


def test_bar_rect_geometry_matches_the_prototype():
    rects = charts.bar_rects([1.0, 2.0], 20, 22)
    # bw = 10, x = i*bw + .5, width = bw - 1.5, height = v / (2*1.1) * (h-2)
    assert rects[0].x == pytest.approx(0.5) and rects[1].x == pytest.approx(10.5)
    assert rects[0].width == pytest.approx(8.5)
    assert rects[1].height == pytest.approx(2.0 / 2.2 * 20)


def test_bars_explicit_highlight_and_none_values():
    rects = charts.bar_rects([1, None, 3], 30, 20, highlight=0)
    assert [(r.index, r.highlight) for r in rects] == [(0, True), (2, False)]
    assert charts.bar_rects([], 30, 20) == []
    assert all(r.width >= 1.0 for r in charts.bar_rects([1] * 100, 30, 20))


def test_hex_rgba():
    assert charts.hex_rgba("#ff0000") == (1.0, 0.0, 0.0, 1.0)
    assert charts.hex_rgba("#fff", 0.5) == (1.0, 1.0, 1.0, 0.5)


def test_painters_run_on_an_image_surface():
    cairo = pytest.importorskip("cairo")
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 120, 30)
    cr = cairo.Context(surface)
    colors = {"line": "#e95420", "dot": "#ffffff", "warn": "#c7870b", "crit": "#e01b24",
              "bar": "#e95420", "now": "#ffb36b"}
    charts.paint_sparkline(cr, 120, 30, [1, 50, 99], colors=colors, vmax=100,
                           thresholds=[(60, "warn"), (85, "crit")])
    charts.paint_bars(cr, 120, 30, [0, 1, 2, 3], colors=colors)
    charts.paint_sparkline(cr, 120, 30, [], colors=colors)  # nothing, no error
    surface.flush()
    assert any(surface.get_data())
