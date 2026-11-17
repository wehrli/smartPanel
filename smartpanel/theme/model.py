from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MetricRender:
    category: str           # e.g. "CPU"
    metric: str             # e.g. "PERCENTAGE"
    interval: float = 5.0
    show_text: bool = True
    show_graph: bool = False
    show_line_graph: bool = False
    show_radial: bool = False
    min_value: float = 0.0
    max_value: float = 100.0
    history_size: int = 20
    show_unit: bool = False
    label: str = ""         # display prefix; defaults to "<CATEGORY> <METRIC>"

    @property
    def key(self) -> str:
        return f"{self.category}.{self.metric}"


@dataclass
class ClaudeOptions:
    show_per_project: bool = True
    show_cost_estimate: bool = True
    cost_horizon: str = "today"   # today | week | lifetime
    top_n_projects: int = 5


@dataclass
class Theme:
    """A loaded theme.

    Top-bar precedence (SP-1904): when the YAML sets `smartpanel.top_bar_format`,
    `top_bar_format_explicit` is True and that template renders the bar label
    instead of the v2 composition (A1 text / A5 ticker); the A6 strip and A4 graphs
    still draw beside it. The shipped presets set no template, so they get the
    composition. `None` means "not loaded from a file": the tray then falls back
    to comparing `top_bar_format` with the loader default and the presets.
    """
    metrics: list[MetricRender] = field(default_factory=list)
    top_bar_format: str = "⚡{CPU.PERCENTAGE:.0f}%"
    top_bar_format_explicit: bool | None = None
    menu_order: list[str] = field(default_factory=list)
    claude: ClaudeOptions = field(default_factory=ClaudeOptions)
    author: str = ""

    def metrics_by_category(self) -> dict[str, list[MetricRender]]:
        out: dict[str, list[MetricRender]] = {}
        for m in self.metrics:
            out.setdefault(m.category, []).append(m)
        return out

    def categories_in_order(self) -> list[str]:
        seen: list[str] = []
        for c in self.menu_order:
            if c not in seen:
                seen.append(c)
        for m in self.metrics:
            if m.category not in seen:
                seen.append(m.category)
        return seen
