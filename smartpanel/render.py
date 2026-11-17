"""Pure text helpers shared by the tray, the spend state and the praise report.

    humanize_tokens(43_546_743)        -> '43.55M'
    format_top_bar(template, values)   -> a theme's `smartpanel.top_bar_format`
                                          rendered against `{CATEGORY: {METRIC: v}}`

GTK-free so tests run without a display. (The v1 menu helpers that lived here, the
block-char bars, sparklines and per-metric value formatting, went with the GTK 3
indicator.)
"""
from __future__ import annotations

import string


def humanize_tokens(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return f"{n:.0f}"


def format_top_bar(template: str, values: dict[str, dict[str, float | str | None]]) -> str:
    """Render a template like '⚡{CPU.PERCENTAGE:.0f}%'. Missing metrics render as 0.

    Treats the dotted field name as a single key (not attribute access),
    so `{CPU.PERCENTAGE}` looks up `values["CPU"]["PERCENTAGE"]`.
    """
    try:
        return _DottedFormatter(values).format(template)
    except (ValueError, TypeError):
        return template


class _DottedFormatter(string.Formatter):
    def __init__(self, values: dict[str, dict[str, float | str | None]]):
        self._values = values

    def get_field(self, field_name: str, args, kwargs):
        cat, _, metric = field_name.partition(".")
        v = self._values.get(cat, {}).get(metric) if metric else self._values.get(cat)
        if v is None:
            v = 0
        return v, field_name
