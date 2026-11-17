"""Tray end to end on a private bus (Gio.TestDBus) with a fake StatusNotifierWatcher.

Never touches the user's session bus: TestDBus spawns its own dbus-daemon, and the
environment it changes is restored afterwards.
"""
from __future__ import annotations

import os
import shutil
import time

import pytest
from gi.repository import Gio, GLib

from smartpanel.config import Config
from smartpanel.state.model import LEVEL_CRITICAL, Session
from smartpanel.state.store import Store
from smartpanel.tray import menu, sni
from smartpanel.tray.dbusmenu import register_object
from smartpanel.tray.tray import ITEM_PATH, MENU_SUFFIX, Tray
from tests.fixtures import tray_states as fx

pytestmark = pytest.mark.skipif(shutil.which("dbus-daemon") is None,
                                reason="needs dbus-daemon for Gio.TestDBus")

WATCHER_XML = """
<node><interface name="org.kde.StatusNotifierWatcher">
  <method name="RegisterStatusNotifierItem"><arg name="service" type="s" direction="in"/></method>
  <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
</interface></node>
"""


def spin(cond=lambda: False, timeout=2.0):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        if cond():
            return True
        time.sleep(0.005)
    return cond()


class FakeWatcher:
    def __init__(self, conn):
        self.conn = conn
        self.items: list[tuple[str, str]] = []      # (sender, service)
        info = Gio.DBusNodeInfo.new_for_xml(WATCHER_XML).interfaces[0]
        self.reg = register_object(conn, "/StatusNotifierWatcher", info, self._call, self._get)
        self.own_id = 0

    def own(self):
        acquired = []
        self.own_id = Gio.bus_own_name_on_connection(
            self.conn, sni.WATCHER_NAME, Gio.BusNameOwnerFlags.NONE,
            lambda *a: acquired.append(1), None)
        assert spin(lambda: acquired)

    def release(self):
        Gio.bus_unown_name(self.own_id)
        self.own_id = 0

    def _call(self, conn, sender, path, iface, method, params, inv):
        self.items.append((sender, params.unpack()[0]))
        inv.return_value(None)

    def _get(self, conn, sender, path, iface, name):
        return GLib.Variant("as", [f"{s}{p}" for s, p in self.items])


class RecordingActions:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def rec(*args):
            self.calls.append((name, *args))
        return rec


@pytest.fixture
def bus():
    saved = {k: os.environ.get(k) for k in ("DBUS_SESSION_BUS_ADDRESS", "DISPLAY")}
    tb = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
    tb.up()
    addr = tb.get_bus_address()
    flags = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
             | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
    app = Gio.DBusConnection.new_for_address_sync(addr, flags, None, None)
    host = Gio.DBusConnection.new_for_address_sync(addr, flags, None, None)
    try:
        yield app, host
    finally:
        for c in (app, host):
            try:
                c.close_sync(None)
            except GLib.Error:
                pass
        tb.down()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def call(host, app, path, iface, method, params, reply=None):
    """Async call + spin: both ends live on this thread's main context, so a
    call_sync would block the very loop that has to answer it."""
    box = []
    host.call(app.get_unique_name(), path, iface, method, params,
              GLib.VariantType(reply) if reply else None,
              Gio.DBusCallFlags.NONE, 2000, None,
              lambda conn, res: box.append(res))
    assert spin(lambda: box), f"no reply to {method}"
    return host.call_finish(box[0])


def get_prop(host, app, path, iface, name):
    v = call(host, app, path, "org.freedesktop.DBus.Properties", "Get",
             GLib.Variant("(ss)", (iface, name)), "(v)")
    return v.unpack()[0]


@pytest.fixture
def setup(bus, tmp_path):
    app, host = bus
    watcher = FakeWatcher(host)
    watcher.own()
    store = Store(fx.busy())
    actions = RecordingActions()
    config = Config()
    tray = Tray(store, actions, config, icon_dir=tmp_path, icon_min_interval=0.0)
    signals = []
    host.signal_subscribe(app.get_unique_name(), None, None, None, None,
                          Gio.DBusSignalFlags.NONE,
                          lambda c, s, p, i, name, params: signals.append((i, name, params.unpack())))
    tray.start(app)
    assert spin(lambda: watcher.items)
    yield app, host, watcher, store, actions, config, tray, signals, tmp_path
    tray.stop()


def test_registers_with_watcher_by_path(setup):
    app, host, watcher, *_ = setup
    assert watcher.items == [(app.get_unique_name(), ITEM_PATH)]


def test_properties_over_the_bus(setup):
    app, host, watcher, store, actions, config, tray, signals, tmp = setup
    get = lambda n: get_prop(host, app, ITEM_PATH, sni.INTERFACE, n)
    assert get("Id") == "smartpanel"
    assert get("Status") == "Active"
    assert get("ItemIsMenu") is False
    assert get("Menu") == ITEM_PATH + MENU_SUFFIX
    assert get("XAyatanaLabel") == "12 · 4/1 · 23.84"
    assert get("XAyatanaLabelGuide") == "⚠100 · 00/00 · 000.00"
    icon = get("IconName")
    assert icon.startswith(str(tmp)) and os.path.isabs(icon) and os.path.exists(icon)
    # The icon's description carries no live readings (SP-1905); the ToolTip does.
    assert get("IconAccessibleDesc") == "smartPanel: 4 sessions, 1 waiting for you, 2 running"
    assert get("ToolTip")[3].startswith("smartPanel: swap 12%")


def test_label_signal_only_when_changed(setup):
    app, host, watcher, store, actions, config, tray, signals, tmp = setup
    signals.clear()
    store.update("journal", monthly_log_path="/x")        # not a bar section
    store.mutate("spend", lambda s: None)                  # bar section, same label
    spin(timeout=0.1)
    assert not [s for s in signals if s[1] == "XAyatanaNewLabel"]
    store.update("spend", cost_today=30.0)
    assert spin(lambda: [s for s in signals if s[1] == "XAyatanaNewLabel"])
    labels = [s[2] for s in signals if s[1] == "XAyatanaNewLabel"]
    assert labels == [("12 · 4/1 · 30.00", "⚠100 · 00/00 · 000.00")]


def test_icon_signal_and_new_file_on_strip_change(setup):
    app, host, watcher, store, actions, config, tray, signals, tmp = setup
    before = tray.sni.props["IconName"]
    signals.clear()
    store.mutate("sessions", lambda s: s.items.append(
        Session("claude-x", project="x", activity="running", created=fx.NOW)))
    assert spin(lambda: [s for s in signals if s[1] == "NewIcon"])
    after = get_prop(host, app, ITEM_PATH, sni.INTERFACE, "IconName")
    assert after != before and os.path.exists(after)
    assert len(list(tmp.glob("bar-*.png"))) <= 2


def test_menu_layout_and_click(setup):
    app, host, watcher, store, actions, config, tray, signals, tmp = setup
    mpath = ITEM_PATH + MENU_SUFFIX
    rev, (nid, props, children) = call(
        host, app, mpath, "com.canonical.dbusmenu", "GetLayout",
        GLib.Variant("(iias)", (0, -1, ["type", "children-display"])), "(u(ia{sv}av))").unpack()
    assert nid == 0 and props["children-display"] == "submenu"
    assert len(children) == len(menu.ROW_KEYS)
    assert get_prop(host, app, mpath, "com.canonical.dbusmenu", "Version") == 3
    new_id = menu.ROW_IDS["act.new"]
    call(host, app, mpath, "com.canonical.dbusmenu", "Event",
         GLib.Variant("(isvu)", (new_id, "clicked", GLib.Variant("i", 0), 0)))
    s0 = menu.ROW_IDS["sessions.0"]
    call(host, app, mpath, "com.canonical.dbusmenu", "Event",
         GLib.Variant("(isvu)", (s0, "clicked", GLib.Variant("i", 0), 0)))
    # disabled header: ignored
    call(host, app, mpath, "com.canonical.dbusmenu", "Event",
         GLib.Variant("(isvu)", (menu.ROW_IDS["sys.header"], "clicked", GLib.Variant("i", 0), 0)))
    assert actions.calls == [("request_new_session",), ("focus_session", "claude-smartPanel")]


def test_menu_updates_are_property_only(setup):
    app, host, watcher, store, actions, config, tray, signals, tmp = setup
    signals.clear()
    store.update("sessions", items=[], tmux_ok=False)
    assert spin(lambda: [s for s in signals if s[1] == "ItemsPropertiesUpdated"])
    assert not [s for s in signals if s[1] == "LayoutUpdated"]


def test_middle_click_opens_dashboard(setup):
    app, host, watcher, store, actions, config, tray, signals, tmp = setup
    call(host, app, ITEM_PATH, sni.INTERFACE, "XAyatanaSecondaryActivate", GLib.Variant("(u)", (0,)))
    call(host, app, ITEM_PATH, sni.INTERFACE, "SecondaryActivate", GLib.Variant("(ii)", (0, 0)))
    assert actions.calls == [("open_dashboard", ""), ("open_dashboard", "")]


def test_reregisters_when_watcher_returns(setup):
    app, host, watcher, *_ = setup
    watcher.release()
    spin(timeout=0.2)
    watcher.own()
    assert spin(lambda: len(watcher.items) == 2)
    assert watcher.items[1] == (app.get_unique_name(), ITEM_PATH)


def test_ticker_timer_exists_only_while_on(setup):
    app, host, watcher, store, actions, config, tray, signals, tmp = setup
    assert not tray.ticker_running
    config.set("bar.ticker", True)
    assert tray.ticker_running
    assert tray.sni.props["XAyatanaLabel"] == "$23.84 today ●○○○"
    config.set("bar.ticker_seconds", 7)
    assert tray.ticker_running and tray._ticker_secs == 7
    config.set("bar.ticker", False)
    assert not tray.ticker_running


def test_ticker_step_pin_and_alert(setup):
    app, host, watcher, store, actions, config, tray, signals, tmp = setup
    config.set("bar.ticker", True)
    tray._on_ticker()
    assert store.state.app.ticker_index == 1
    assert tray.sni.props["XAyatanaLabel"] == "4 sessions · 1 waiting ○●○○"
    store.update("app", ticker_pinned=True)
    tray._on_ticker()
    assert store.state.app.ticker_index == 1                       # pinned: timer holds
    tray.sni.handle("Scroll", (120, "vertical"))                   # a scroll still steps
    assert store.state.app.ticker_index == 2
    store.update("app", ticker_pinned=False)
    store.mutate("system", lambda s: (setattr(s.swap, "percent", 91.0),
                                      setattr(s.swap, "level", LEVEL_CRITICAL)))
    tray._on_ticker()
    assert store.state.app.ticker_index == 2                       # alert pins
    assert tray.sni.props["XAyatanaLabel"] == "⚠ Swap 91% pinned by alert"


def test_pin_menu_item_calls_actions(setup):
    app, host, watcher, store, actions, config, tray, signals, tmp = setup
    config.set("bar.ticker", True)
    tray._on_menu_event(menu.ROW_IDS["act.pin"], "clicked", 0)
    assert actions.calls == [("pin_ticker", True)]


def test_graph_only_changes_are_throttled(setup):
    app, host, watcher, store, actions, config, tray, signals, tmp = setup
    tray.icon_min_interval = 60.0
    tray._icon_last_render = time.monotonic()
    n = tray.icon_renders
    store.mutate("system", lambda s: s.cpu.history.append(99.0))
    store.mutate("system", lambda s: setattr(s.memory, "percent", 12.0))
    assert tray.icon_renders == n and tray._icon_pending_id      # deferred, one timer
    store.update("sessions", items=[])                           # strip change: immediate
    assert tray.icon_renders == n + 1 and not tray._icon_pending_id


def test_loading_uses_spinner_icon(setup):
    app, host, watcher, store, actions, config, tray, signals, tmp = setup
    store.update("app", loading=fx.loading().app.loading)
    assert tray.sni.props["IconName"] == "process-working-symbolic"
    assert tray.sni.props["XAyatanaLabel"] == "reading 812/1284"


def test_stop_goes_passive_and_cleans_files(setup):
    app, host, watcher, store, actions, config, tray, signals, tmp = setup
    item = tray.sni
    tray.stop()
    assert item.props["Status"] == "Passive"
    assert list(tmp.glob("bar-*.png")) == []
    spin(timeout=0.05)
    assert any(s[1] == "NewStatus" and s[2] == ("Passive",) for s in signals)
    tray.stop()                                                  # idempotent


def test_theme_template_wins(bus, tmp_path):
    from smartpanel.theme.model import Theme
    app, host = bus
    theme = Theme(top_bar_format="⚡{CPU.PERCENTAGE:.0f}% · {SESSIONS.COUNT}")
    theme.top_bar_format_explicit = True       # type: ignore[attr-defined]
    tray = Tray(Store(fx.busy()), RecordingActions(), Config(), theme=theme, icon_dir=tmp_path)
    tray.start(app)
    try:
        assert tray.sni.props["XAyatanaLabel"] == "⚡23% · 4"
    finally:
        tray.stop()


def test_cpu_tick_with_graphs_off_emits_no_new_icon(setup):
    """SP-1905: a reading that only moves the ToolTip must not make the host reload
    the (unchanged) PNG - the extension reloads on every NewIcon."""
    app, host, watcher, store, actions, config, tray, signals, tmp = setup
    config.set("bar.graphs", False)
    spin(timeout=0.05)
    signals.clear()
    n = tray.icon_renders
    for v in (31.0, 47.0, 62.0):
        store.mutate("system", lambda s, v=v: (setattr(s.cpu, "percent", v),
                                               setattr(s.memory, "percent", v)))
    spin(timeout=0.1)
    names = [s[1] for s in signals]
    assert "NewIcon" not in names and "NewAttentionIcon" not in names
    assert "NewToolTip" in names
    assert tray.icon_renders == n


def test_memory_only_change_waits_for_next_cpu_tick(setup):
    """SP-0203: the graphs redraw at the CPU interval, no faster - a memory or swap
    sample between two CPU samples is folded into the next CPU-driven render."""
    app, host, watcher, store, actions, config, tray, signals, tmp = setup
    tray.icon_min_interval = 0.0
    tray.icon_fallback_interval = 60.0
    tray._icon_last_render = time.monotonic() - 2.0      # 2 s since the last CPU render
    n = tray.icon_renders
    store.mutate("system", lambda s: setattr(s.memory, "percent", 91.0))
    store.mutate("system", lambda s: setattr(s.swap, "percent", 13.0))
    assert tray.icon_renders == n and tray._icon_pending_id   # parked, not drawn
    store.mutate("system", lambda s: s.cpu.history.append(55.0))   # the CPU tick
    assert tray.icon_renders == n + 1 and not tray._icon_pending_id
    assert tray._icon_applied.mem_pct == 91.0 and tray._icon_applied.swap_pct == 13.0


def test_memory_only_change_falls_back_when_cpu_stalls(setup):
    app, host, watcher, store, actions, config, tray, signals, tmp = setup
    tray.icon_fallback_interval = 0.05
    tray._icon_last_render = time.monotonic()
    n = tray.icon_renders
    store.mutate("system", lambda s: setattr(s.memory, "percent", 91.0))
    assert tray.icon_renders == n
    assert spin(lambda: tray.icon_renders == n + 1, timeout=1.0)


def test_theme_template_guide_is_stable(bus, tmp_path):
    from smartpanel.theme.model import Theme
    app, host = bus
    theme = Theme(top_bar_format="⚡{CPU.PERCENTAGE:.0f}% · {SESSIONS.COUNT}")
    theme.top_bar_format_explicit = True       # type: ignore[attr-defined]
    store = Store(fx.busy())
    tray = Tray(store, RecordingActions(), Config(), theme=theme, icon_dir=tmp_path)
    tray.start(app)
    try:
        guide = tray.sni.props["XAyatanaLabelGuide"]
        store.mutate("system", lambda s: setattr(s.cpu, "percent", 87.0))
        assert tray.sni.props["XAyatanaLabel"] == "⚡87% · 4"
        assert tray.sni.props["XAyatanaLabelGuide"] == guide
        assert len(guide) >= len("🔴🔃100% ⚡87% · 4")
    finally:
        tray.stop()


def test_loading_progress_never_renders_the_png(setup):
    """Cold-scan progress only moves the label; when it ends with the same
    IconSpec the existing PNG comes back without a render."""
    app, host, watcher, store, actions, config, tray, signals, tmp = setup
    spin(timeout=0.05)
    n = tray.icon_renders
    before = tray.sni.props["IconName"]
    assert before.endswith(".png")
    ld = fx.loading().app.loading
    for done in (100, 400, 812):
        store.update("app", loading=type(ld)(done=done, total=ld.total))
    assert tray.sni.props["IconName"] == "process-working-symbolic"
    assert tray.icon_renders == n
    store.update("app", loading=None)
    assert tray.sni.props["IconName"] == before
    assert tray.icon_renders == n
