from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from smartpanel.theme.loader import load_theme


def _write(path: Path, body: str) -> Path:
    path.write_text(dedent(body))
    return path


def test_loads_minimal_theme(tmp_path: Path):
    p = _write(tmp_path / "t.yaml", """
        STATS:
          CPU:
            PERCENTAGE:
              INTERVAL: 2
              TEXT:  { SHOW: True, SHOW_UNIT: True }
              GRAPH: { SHOW: True, MIN_VALUE: 0, MAX_VALUE: 100 }
        smartpanel:
          top_bar_format: "⚡{CPU.PERCENTAGE:.0f}%"
    """)
    theme = load_theme(p)
    assert len(theme.metrics) == 1
    m = theme.metrics[0]
    assert m.category == "CPU"
    assert m.metric == "PERCENTAGE"
    assert m.interval == 2.0
    assert m.show_text and m.show_graph
    assert m.show_unit
    assert m.max_value == 100
    assert theme.top_bar_format == "⚡{CPU.PERCENTAGE:.0f}%"


def test_ignores_pixel_layout_fields(tmp_path: Path):
    """Original turing-smart-screen themes carry X/Y/FONT/COLOR — must be ignored cleanly."""
    p = _write(tmp_path / "t.yaml", """
        author: legacy
        static_images:
          BACKGROUND: { PATH: bg.png, X: 0, Y: 0, WIDTH: 320 }
        static_text:
          CPU_LABEL: { TEXT: CPU, X: 5, Y: 5, FONT: roboto.ttf }
        STATS:
          CPU:
            PERCENTAGE:
              INTERVAL: 1
              TEXT:  { SHOW: True, X: 5, Y: 35, FONT_SIZE: 75, FONT_COLOR: 0,0,0 }
              GRAPH: { SHOW: True, X: 170, Y: 64, WIDTH: 150, HEIGHT: 50,
                       MIN_VALUE: 0, MAX_VALUE: 100, BAR_COLOR: 0,120,255 }
    """)
    theme = load_theme(p)
    assert theme.author == "legacy"
    assert len(theme.metrics) == 1
    assert theme.metrics[0].show_text and theme.metrics[0].show_graph


def test_skips_metrics_with_all_renderers_off(tmp_path: Path):
    p = _write(tmp_path / "t.yaml", """
        STATS:
          CPU:
            PERCENTAGE:
              INTERVAL: 1
              TEXT:  { SHOW: False }
              GRAPH: { SHOW: False }
    """)
    theme = load_theme(p)
    assert theme.metrics == []


def test_claude_options_parsed(tmp_path: Path):
    p = _write(tmp_path / "t.yaml", """
        STATS:
          CLAUDE:
            COST_TODAY:
              INTERVAL: 30
              TEXT: { SHOW: True }
        smartpanel:
          claude:
            show_per_project: false
            cost_horizon: week
            top_n_projects: 3
    """)
    theme = load_theme(p)
    assert theme.claude.show_per_project is False
    assert theme.claude.cost_horizon == "week"
    assert theme.claude.top_n_projects == 3


def test_builtin_presets_load():
    here = Path(__file__).parent.parent / "smartpanel" / "theme" / "presets"
    for name in ("default.yaml", "claude-focus.yaml"):
        theme = load_theme(here / name)
        assert theme.metrics, f"{name} has no metrics"
        assert theme.top_bar_format
