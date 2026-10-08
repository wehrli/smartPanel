"""Pure rendering helpers — text formatting, bars, sparklines, top-bar template.

Kept GTK-free so tests can run without a display server.
"""
from __future__ import annotations

from typing import Iterable

BAR_WIDTH = 10
BAR_FILLED = "█"
BAR_EMPTY = "░"
SPARK_CHARS = "▁▂▃▄▅▆▇█"

# Suffix shown for some metrics when SHOW_UNIT is on. Keyed by "category.metric".
UNITS: dict[str, str] = {
    "CPU.PERCENTAGE": "%",
    "CPU.FREQUENCY": " MHz",
    "CPU.TEMPERATURE": "°C",
    "MEMORY.PERCENTAGE": "%",
    "MEMORY.USED": " GiB",
    "MEMORY.FREE": " GiB",
    "MEMORY.SWAP_PERCENT": "%",
    "MEMORY.SWAP_USED": " GiB",
    "SWAP.PERCENTAGE": "%",
    "SWAP.USED": " GiB",
    "SWAP.FREE": " GiB",
    "SWAP.TOTAL": " GiB",
    "DISK.USED_PERCENT": "%",
    "DISK.USED": " GiB",
    "NET.DOWNLOAD": " MB/s",
    "NET.UPLOAD": " MB/s",
    "NET.DOWNLOADED": " GiB",
    "NET.UPLOADED": " GiB",
    "GPU.PERCENTAGE": "%",
    "GPU.MEMORY_PERCENT": "%",
    "GPU.TEMPERATURE": "°C",
    "BATTERY.PERCENTAGE": "%",
    "CLAUDE.CACHE_HIT_RATIO": "%",
    "CLAUDE.PRAISE_SHARE": "%",
}


def format_value(key: str, value, show_unit: bool) -> str:
    if isinstance(value, float):
        if key.startswith("CLAUDE.") and ("COST" in key or key == "CLAUDE.BURN_RATE"):
            text = f"${value:,.2f}"
            if key == "CLAUDE.BURN_RATE":
                text = f"{text} /hr"
        elif key in ("CLAUDE.SESSIONS_TODAY", "SESSIONS.COUNT"):
            text = f"{int(value)}"
        elif key.startswith("CLAUDE.PRAISE_PHRASES"):
            text = f"{int(value)}"
        elif key.startswith("CLAUDE.PRAISE_SHARE"):
            text = f"{value:.2f}"
        elif "TOKENS" in key and key.startswith("CLAUDE.") and not key.endswith("_HUMAN"):
            text = humanize_tokens(value)
        elif key in ("NET.DOWNLOAD", "NET.UPLOAD"):
            text = f"{value:.2f}"
        elif abs(value) >= 100:
            text = f"{value:.0f}"
        else:
            text = f"{value:.1f}"
    elif isinstance(value, int):
        text = str(value)
    else:
        text = str(value)
    if show_unit:
        text = text + UNITS.get(key, "")
    return text


def humanize_tokens(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return f"{n:.0f}"


def render_bar(value: float, vmin: float, vmax: float, width: int = BAR_WIDTH) -> str:
    span = max(vmax - vmin, 1e-9)
    pct = max(0.0, min(1.0, (value - vmin) / span))
    filled = int(round(pct * width))
    return BAR_FILLED * filled + BAR_EMPTY * (width - filled)


def render_sparkline(history: Iterable[float], vmin: float, vmax: float) -> str:
    span = max(vmax - vmin, 1e-9)
    out = []
    for v in history:
        pct = max(0.0, min(1.0, (v - vmin) / span))
        idx = min(len(SPARK_CHARS) - 1, int(pct * (len(SPARK_CHARS) - 1)))
        out.append(SPARK_CHARS[idx])
    return "".join(out)


def render_radial(value: float, vmin: float, vmax: float) -> str:
    chars = "▘▝▗▖"
    span = max(vmax - vmin, 1e-9)
    pct = max(0.0, min(1.0, (value - vmin) / span))
    return chars[int(pct * (len(chars) - 1))]


def format_top_bar(template: str, values: dict[str, dict[str, float | str | None]]) -> str:
    """Render a template like '⚡{CPU.PERCENTAGE:.0f}%'. Missing metrics render as 0.

    Treats the dotted field name as a single key (not attribute access),
    so `{CPU.PERCENTAGE}` looks up `values["CPU"]["PERCENTAGE"]`.
    """
    try:
        return _DottedFormatter(values).format(template)
    except (ValueError, TypeError):
        return template


import string


class _DottedFormatter(string.Formatter):
    def __init__(self, values: dict[str, dict[str, float | str | None]]):
        self._values = values

    def get_field(self, field_name: str, args, kwargs):
        cat, _, metric = field_name.partition(".")
        v = self._values.get(cat, {}).get(metric) if metric else self._values.get(cat)
        if v is None:
            v = 0
        return v, field_name
