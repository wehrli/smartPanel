from __future__ import annotations

from pathlib import Path

import yaml

from smartpanel.theme.model import ClaudeOptions, MetricRender, Theme

_RENDERER_KEYS = {"TEXT", "GRAPH", "LINE_GRAPH", "RADIAL"}


def load_theme(path: str | Path) -> Theme:
    """Load a theme YAML. Compatible with turing-smart-screen-python theme files:
    pixel-layout fields are silently ignored; only structural data is consumed."""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    metrics: list[MetricRender] = []
    stats = data.get("STATS", {}) or {}
    for category, metric_block in stats.items():
        if not isinstance(metric_block, dict):
            continue
        for metric, spec in metric_block.items():
            if not isinstance(spec, dict):
                continue
            interval = float(spec.get("INTERVAL", 5))
            renderers = {k: (spec.get(k) or {}) for k in _RENDERER_KEYS if k in spec}

            # If a metric has no recognizable renderer block, default to TEXT on.
            if not renderers:
                renderers["TEXT"] = {"SHOW": True}

            text = bool((renderers.get("TEXT") or {}).get("SHOW", False))
            graph = bool((renderers.get("GRAPH") or {}).get("SHOW", False))
            line_graph = bool((renderers.get("LINE_GRAPH") or {}).get("SHOW", False))
            radial = bool((renderers.get("RADIAL") or {}).get("SHOW", False))

            if not (text or graph or line_graph or radial):
                continue  # nothing to show for this metric

            graph_spec = renderers.get("GRAPH") or renderers.get("LINE_GRAPH") or {}
            min_value = float(graph_spec.get("MIN_VALUE", 0))
            max_value = float(graph_spec.get("MAX_VALUE", 100))
            history_size = int((renderers.get("LINE_GRAPH") or {}).get("HISTORY_SIZE", 20))
            show_unit = bool((renderers.get("TEXT") or {}).get("SHOW_UNIT", False))
            label = str(spec.get("LABEL", "")) or _default_label(category, metric)

            metrics.append(MetricRender(
                category=category,
                metric=metric,
                interval=interval,
                show_text=text,
                show_graph=graph,
                show_line_graph=line_graph,
                show_radial=radial,
                min_value=min_value,
                max_value=max_value,
                history_size=history_size,
                show_unit=show_unit,
                label=label,
            ))

    sp = data.get("smartpanel", {}) or {}
    claude_cfg = sp.get("claude", {}) or {}
    claude = ClaudeOptions(
        show_per_project=bool(claude_cfg.get("show_per_project", True)),
        show_cost_estimate=bool(claude_cfg.get("show_cost_estimate", True)),
        cost_horizon=str(claude_cfg.get("cost_horizon", "today")),
        top_n_projects=int(claude_cfg.get("top_n_projects", 5)),
    )

    return Theme(
        metrics=metrics,
        top_bar_format=str(sp.get("top_bar_format", "⚡{CPU.PERCENTAGE:.0f}%")),
        menu_order=list(sp.get("menu_order", []) or []),
        claude=claude,
        author=str(data.get("author", "")),
    )


def _default_label(category: str, metric: str) -> str:
    pretty = metric.replace("_", " ").title()
    return f"{category.title()} {pretty}"
