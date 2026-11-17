"""Widget-kit gallery (SP-0009): every widget with sample data, in every state.

    smartpanel --gallery [--style light|dark]
    smartpanel --snapshot gallery --out gallery.png

`build_gallery()` is also what the GTK smoke test instantiates.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from smartpanel.state.model import (IDLE, LEVEL_CRITICAL, LEVEL_OK, LEVEL_WARNING,  # noqa: E402
                                    PHASE_JOURNALING, PHASE_LAUNCHING, RUNNING, UNKNOWN,
                                    WAITING)
from smartpanel.ui import tokens  # noqa: E402
from smartpanel.ui.widgets import (ActivityGlyph, Banner, EmptyState, HourBars,  # noqa: E402
                                   LevelChip, MetricCard, NavBadge, Pill, Sparkline, Track)
from smartpanel.ui.shellmodel import Badge  # noqa: E402

CPU = [18, 22, 25, 31, 28, 24, 20, 26, 35, 41, 38, 30, 27, 23, 25, 29, 33, 27, 22, 23]
MEM = [66, 66, 67, 67, 68, 68, 68, 69, 68, 68, 67, 68, 68, 69, 69, 68, 68, 68, 68, 68]
SWAP_UP = [12, 14, 19, 25, 33, 41, 48, 55, 61, 64, 66, 70, 76, 82, 86, 88, 90, 91, 92, 93]
HOURS = [0, 0, 0, 0, 0, 0, 0, 0, .4, 1.9, 3.4, 2.8, 3.1, 4.2, 3.3, 2.6, 1.4, .74]
STATES = (WAITING, RUNNING, IDLE, PHASE_LAUNCHING, PHASE_JOURNALING, UNKNOWN)


def _section(title: str) -> Gtk.Label:
    label = Gtk.Label(label=title.upper(), xalign=0, margin_top=10)
    label.add_css_class("sp-cap")
    return label


def build_gallery() -> Gtk.Widget:
    """A scrollable column holding one of everything. Returns the scroller;
    `scroller.widgets` maps a name to each instance (for tests)."""
    tokens.install()
    col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=16,
                  margin_bottom=24, margin_start=18, margin_end=18)
    w: dict[str, Gtk.Widget] = {}

    col.append(_section("Banner"))
    w["banner_warn"] = Banner()
    w["banner_warn"].show("Swap is filling up · 66% used (5.3 of 8 GB). Close something heavy "
                          "before the system stalls.", LEVEL_WARNING, "Open System")
    w["banner_crit"] = Banner()
    w["banner_crit"].show("Swap is critically full · 93% used (7.4 of 8 GB). Close something "
                          "heavy before the system stalls.", LEVEL_CRITICAL, "Open System")
    col.append(w["banner_warn"])
    col.append(w["banner_crit"])

    col.append(_section("MetricCard"))
    grid = Gtk.Grid(column_spacing=10, row_spacing=10, column_homogeneous=True)
    cpu = MetricCard("CPU", vmax=100)
    cpu.set_value("23%"); cpu.set_history(CPU); cpu.set_sub("61 °C")
    mem = MetricCard("Memory", vmax=100)
    mem.set_value("68%"); mem.set_history(MEM); mem.set_sub("10.9 of 16 GB")
    swap = MetricCard("Swap", vmax=100, chip=True)
    swap.set_value("66%"); swap.set_history(SWAP_UP[:11], thresholds=[(60, "warn"), (85, "crit")])
    swap.set_sub("5.3 of 8 GB"); swap.set_level(LEVEL_WARNING); swap.set_chip(LEVEL_WARNING)
    crit = MetricCard("Swap", vmax=100, chip=True)
    crit.set_value("93%"); crit.set_history(SWAP_UP, thresholds=[(60, "warn"), (85, "crit")])
    crit.set_sub("7.4 of 8 GB"); crit.set_level(LEVEL_CRITICAL); crit.set_chip(LEVEL_CRITICAL)
    gpu = MetricCard("GPU", chip=True)
    gpu.set_value("—"); gpu.set_sub("No NVIDIA or AMD GPU found."); gpu.set_na(True)
    gpu.set_chip(None, "No sensor")
    for i, card in enumerate((cpu, mem, swap, crit, gpu)):
        grid.attach(card, i % 3, i // 3, 1, 1)
    w.update(cpu_card=cpu, mem_card=mem, swap_card=swap, crit_card=crit, gpu_card=gpu)
    col.append(grid)

    col.append(_section("Sparkline · HourBars · Track"))
    row = Gtk.Box(spacing=16)
    w["sparkline"] = Sparkline(200, 40, vmax=100)
    w["sparkline"].set_values(SWAP_UP, thresholds=[(60, "warn"), (85, "crit")])
    w["hourbars"] = HourBars(240, 46)
    w["hourbars"].set_values(HOURS)
    tracks = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, hexpand=True)
    w["track"] = Track(0.6)
    w["track_over"] = Track()
    w["track_over"].set_percent(112)
    tracks.append(w["track"])
    tracks.append(w["track_over"])
    for x in (w["sparkline"], w["hourbars"], tracks):
        row.append(x)
    col.append(row)

    col.append(_section("ActivityGlyph · Pill"))
    states = Gtk.Box(spacing=14)
    for s in STATES:
        pair = Gtk.Box(spacing=6)
        g, p = ActivityGlyph(s), Pill()
        p.set_activity(s)
        pair.append(g)
        pair.append(p)
        states.append(pair)
        w[f"glyph_{s}"], w[f"pill_{s}"] = g, p
    col.append(states)

    col.append(_section("LevelChip · NavBadge"))
    chips = Gtk.Box(spacing=10)
    for name, lvl in (("ok", LEVEL_OK), ("warn", LEVEL_WARNING), ("crit", LEVEL_CRITICAL),
                      ("na", None)):
        w[f"chip_{name}"] = LevelChip(lvl)
        chips.append(w[f"chip_{name}"])
    for name, badge in (("count", Badge("2", "amber", "2 sessions need you")),
                        ("dot", Badge("·", "amber", "Swap is filling up")),
                        ("bang", Badge("!", "red", "tmux is not installed"))):
        b = NavBadge()
        b.set_badge(badge)
        w[f"badge_{name}"] = b
        chips.append(b)
    col.append(chips)

    col.append(_section("EmptyState"))
    empties = Gtk.Box(spacing=10, homogeneous=True)
    w["empty"] = EmptyState("No Claude sessions running",
                            "Sessions you start here run inside tmux as claude-<project>.",
                            glyph="⌁", action_label="＋ New session")
    w["empty_icon"] = EmptyState("tmux isn’t installed", "Install it, then check again.",
                                 icon_name="dialog-warning-symbolic",
                                 action_label="Check again", suggested=False)
    for e in (w["empty"], w["empty_icon"]):
        e.add_css_class("sp-card")
        empties.append(e)
    col.append(empties)

    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    scroller.set_child(col)
    scroller.widgets = w
    return scroller


def run_gallery(ns) -> int:
    app = Adw.Application(application_id="dev.omdev.SmartPanel.Gallery",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)

    def activate(a):
        if getattr(ns, "style", None):
            Adw.StyleManager.get_default().set_color_scheme(tokens.color_scheme_for(ns.style))
        win = Adw.ApplicationWindow(application=a, title="smartPanel widget gallery",
                                    default_width=900, default_height=760)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(build_gallery())
        win.set_content(view)
        win.present()

    app.connect("activate", activate)
    return app.run([])
