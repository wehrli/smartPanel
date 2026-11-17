"""The widget kit (SP-0009). Every widget updates in place through `set_*` methods,
so pages keep their widgets across store notifications instead of rebuilding.

Importing this package loads GTK 4 / libadwaita.
"""
from smartpanel.ui.widgets.banner import Banner
from smartpanel.ui.widgets.card import MetricCard
from smartpanel.ui.widgets.chart import HourBars, Sparkline
from smartpanel.ui.widgets.empty import EmptyState
from smartpanel.ui.widgets.labels import ActivityGlyph, LevelChip, NavBadge, Pill
from smartpanel.ui.widgets.track import Track

__all__ = ["ActivityGlyph", "Banner", "EmptyState", "HourBars", "LevelChip",
           "MetricCard", "NavBadge", "Pill", "Sparkline", "Track"]
