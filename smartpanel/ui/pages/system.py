"""System page (prototype `systemHTML`): CPU, Memory, Swap, Disk, Network, GPU.

Two columns of cards (one when narrow; a FlowBox decides), built once and updated
in place on every collector tick (SP-2108): values, sub-lines, sparklines, chips
and the disk bar are set through no-op-when-unchanged setters, so nothing is
rebuilt and nothing flickers. Copy lives in `ui/widgets/overview_system_model.py`.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from smartpanel.state.model import LEVEL_CRITICAL, LEVEL_OK, LEVEL_WARNING  # noqa: E402
from smartpanel.ui.widgets import MetricCard, Track  # noqa: E402
from smartpanel.ui.widgets import overview_system_model as m  # noqa: E402
from smartpanel.ui.widgets.overview_system_widgets import install_css, toggle_class  # noqa: E402

CHART_W, CHART_H = 200, 46


def build(store, actions, config=None):
    return SystemPage(store, actions, config)


class SystemPage(Adw.Bin):
    def __init__(self, store, actions, config=None):
        super().__init__()
        install_css()
        self.store, self.actions, self.config = store, actions, config

        grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                           min_children_per_line=1, max_children_per_line=2,
                           column_spacing=10, row_spacing=10, valign=Gtk.Align.START)
        grid.add_css_class("os-grid")
        grid.add_css_class("os-page")

        def card(title, *, chart=True, chip=False, vmax=100):
            c = MetricCard(title, chart=chart, chip=chip, vmax=vmax,
                           chart_width=CHART_W, chart_height=CHART_H)
            c.add_css_class("os-scard")
            # a wrapping sub-line reports its whole text as natural width, which
            # would push the FlowBox down to one column; cap it.
            c.get_last_child().set_max_width_chars(24)
            grid.append(c)
            return c

        self.cpu = card("CPU")
        self.memory = card("Memory")
        self.swap = card("Swap", chip=True)
        self.disk = card("Disk", chart=False, chip=True)
        self.disk_track = Track()
        self.disk_track.set_margin_top(14)
        self.disk_track.set_margin_bottom(14)
        self.disk_track.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)
        value = self.disk.get_first_child().get_next_sibling()
        self.disk.insert_child_after(self.disk_track, value)
        self.net = card("Network", vmax=None)
        self.gpu = card("GPU", chip=True)
        child = grid.get_first_child()
        while child is not None:
            child.set_focusable(False)
            child = child.get_next_sibling()
        self.grid = grid

        self.set_child(Adw.Clamp(maximum_size=1100, tightening_threshold=900, child=grid))
        self._unsub = store.subscribe(self._on_changed)
        self.connect("destroy", lambda *_: self.dispose_page())
        self.refresh()

    def dispose_page(self) -> None:
        unsub, self._unsub = self._unsub, None
        if unsub:
            try:
                unsub()
            except Exception:  # noqa: BLE001 — teardown at exit; the store may be gone
                pass

    def _on_changed(self, sections: set[str]) -> None:
        if "system" in sections:
            self.refresh()

    @staticmethod
    def _apply(card: MetricCard, model: m.Card) -> None:
        card.set_value(model.value)
        card.set_sub(model.sub)
        card.set_na(model.na)

    def refresh(self) -> None:
        sysst = self.store.state.system

        self._apply(self.cpu, m.cpu_card(sysst.cpu, detail=True))
        self.cpu.set_history(sysst.cpu.history)

        self._apply(self.memory, m.memory_card(sysst.memory, detail=True))
        self.memory.set_history(sysst.memory.history)

        swap = sysst.swap
        model = m.swap_card(swap, detail=True)
        self._apply(self.swap, model)
        configured = m.swap_configured(swap)
        self.swap.set_level(model.level if configured else LEVEL_OK)
        self.swap.set_chip(model.level if configured else None, "Not configured")
        self.swap.set_history(swap.history if configured else [],
                              thresholds=m.swap_thresholds(swap))

        disk = m.disk_card(sysst.disk)
        self._apply(self.disk, disk)
        self.disk_track.set_percent(sysst.disk.percent)
        self.disk_track.set_visible(sysst.disk.percent is not None)
        toggle_class(self.disk_track, "os-track-warn", disk.level == LEVEL_WARNING)
        toggle_class(self.disk_track, "os-track-crit", disk.level == LEVEL_CRITICAL)
        # never colour-only: the chip says it in words once the bar turns
        self.disk.chip.set_visible(disk.level != LEVEL_OK)
        self.disk.set_chip(disk.level)

        net = sysst.net
        self._apply(self.net, m.net_card(net))
        self.net.set_history(net.history_down)

        gpu = sysst.gpu
        self._apply(self.gpu, m.gpu_card(gpu))
        self.gpu.chip.set_visible(not gpu.available)
        self.gpu.set_chip(None, "No sensor")
        self.gpu.set_history(gpu.history if gpu.available else [])
