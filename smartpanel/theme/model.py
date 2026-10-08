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
    metrics: list[MetricRender] = field(default_factory=list)
    top_bar_format: str = "⚡{CPU.PERCENTAGE:.0f}%"
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
