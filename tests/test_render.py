from collections import deque

from smartpanel.render import (
    format_top_bar,
    format_value,
    humanize_tokens,
    render_bar,
    render_sparkline,
)


def test_render_bar_endpoints():
    assert render_bar(0, 0, 100) == "░" * 10
    assert render_bar(100, 0, 100) == "█" * 10
    bar = render_bar(50, 0, 100)
    assert bar.count("█") == 5
    assert bar.count("░") == 5


def test_render_bar_clamps_out_of_range():
    assert render_bar(-50, 0, 100).count("█") == 0
    assert render_bar(500, 0, 100).count("█") == 10


def test_render_sparkline_covers_full_height():
    line = render_sparkline(deque([0, 20, 40, 60, 80, 100]), 0, 100)
    assert line[0] == "▁"
    assert line[-1] == "█"
    assert len(line) == 6


def test_humanize_tokens():
    assert humanize_tokens(42) == "42"
    assert humanize_tokens(2500) == "2.5k"
    assert humanize_tokens(43_546_743) == "43.55M"


def test_format_value_cost_and_tokens():
    # Note: Python uses banker's rounding for str.format — 1.349 avoids ambiguity.
    assert format_value("CLAUDE.COST_TODAY", 1.349, False) == "$1.35"
    assert format_value("CLAUDE.TOKENS_TODAY", 43_546_743.0, False) == "43.55M"
    assert format_value("CPU.PERCENTAGE", 78.3, True) == "78.3%"
    assert format_value("CPU.TEMPERATURE", 62.0, True) == "62.0°C"


def test_format_value_praise_metrics():
    assert format_value("CLAUDE.PRAISE_COST_LIFETIME", 0.194, False) == "$0.19"
    assert format_value("CLAUDE.PRAISE_TOKENS_LIFETIME", 1_250.0, False) == "1.2k"
    assert format_value("CLAUDE.PRAISE_PHRASES_TODAY", 7.0, False) == "7"
    assert format_value("CLAUDE.PRAISE_SHARE", 0.0234, True) == "0.02%"


def test_format_top_bar_substitutes_dotted_keys():
    out = format_top_bar(
        "⚡{CPU.PERCENTAGE:.0f}% 🌡{CPU.TEMPERATURE:.0f}° ${CLAUDE.COST_TODAY:.2f}",
        {"CPU": {"PERCENTAGE": 78.3, "TEMPERATURE": 62.5},
         "CLAUDE": {"COST_TODAY": 1.34}},
    )
    assert out == "⚡78% 🌡62° $1.34"


def test_format_top_bar_missing_metric_renders_zero():
    out = format_top_bar(
        "⚡{CPU.PERCENTAGE:.0f}% gpu={GPU.PERCENTAGE:.0f}%",
        {"CPU": {"PERCENTAGE": 12}},
    )
    assert out == "⚡12% gpu=0%"


def test_format_top_bar_bad_template_returns_template():
    # Malformed template — should fall back to the literal string, not crash.
    out = format_top_bar("{{{bogus", {})
    assert isinstance(out, str)
