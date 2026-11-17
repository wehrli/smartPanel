from smartpanel.render import format_top_bar, humanize_tokens


def test_humanize_tokens():
    assert humanize_tokens(42) == "42"
    assert humanize_tokens(2500) == "2.5k"
    assert humanize_tokens(43_546_743) == "43.55M"


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
