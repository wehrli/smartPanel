"""PanelService + PanelClient over a private bus (Gio.TestDBus).

The service is exported on one connection that owns dev.omdev.SmartPanel, exactly as
the Adw.Application does; the client uses a second connection. Client calls block, so
they run on a worker thread while the test thread iterates the default main context
(where the service dispatches). Every wait is bounded: nothing here can hang.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

gi = pytest.importorskip("gi")
from gi.repository import Gio, GLib  # noqa: E402

from smartpanel.dbus import service as svc_mod  # noqa: E402
from smartpanel.dbus.client import PanelClient, PanelError  # noqa: E402
from smartpanel.dbus.service import (  # noqa: E402
    BUS_NAME, CHANGED_WINDOW_MS, INTERFACE, OBJECT_PATH, PanelService,
)
from smartpanel.state.model import SECTIONS, Event, Session  # noqa: E402
from smartpanel.state.store import Store  # noqa: E402

CTX = GLib.MainContext.default()


def pump(seconds: float, until=None) -> bool:
    """Iterate the main context for up to `seconds`, or until `until()` is true."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if until is not None and until():
            return True
        if not CTX.iteration(False):
            time.sleep(0.002)
    return bool(until and until())


def in_thread(fn, timeout: float = 5.0):
    box: dict = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as exc:  # re-raised on the test thread
            box["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert pump(timeout, until=lambda: not t.is_alive()), "client call hung"
    if "error" in box:
        raise box["error"]
    return box.get("value")


class RecordingActions:
    """Implements state.actions.Actions by recording every call."""

    def __init__(self, store: Store):
        self.store = store
        self.calls: list[tuple] = []
        self.fail_with: Exception | None = None

    def _rec(self, name, *args):
        self.calls.append((name, *args))
        if self.fail_with is not None:
            raise self.fail_with

    def open_dashboard(self, page=""): self._rec("open_dashboard", page)
    def open_palette(self): self._rec("open_palette")
    def set_hud(self, enabled): self._rec("set_hud", enabled)
    def pin_ticker(self, pinned): self._rec("pin_ticker", pinned)

    def focus_session(self, name):
        self._rec("focus_session", name)
        # Like the real controller: the ladder result comes back as an Event.
        self.store.toast(f"Raised the terminal for “{name}”")

    def retitle_session(self, name): self._rec("retitle_session", name)

    def request_close(self, name):
        self._rec("request_close", name)
        self.store.emit(Event("confirm_close", {"name": name}))

    def close_session(self, name, journal): self._rec("close_session", name, journal)
    def request_close_all(self): self._rec("request_close_all")
    def close_all(self): self._rec("close_all")
    def request_new_session(self): self._rec("request_new_session")
    def new_session(self, directory): self._rec("new_session", directory)

    def answer_prompt(self, name, option, fingerprint):
        self._rec("answer_prompt", name, option, fingerprint)

    def set_setting(self, key, value): self._rec("set_setting", key, value)
    def list_start_folders(self): return []
    def recheck_environment(self): self._rec("recheck_environment")
    def quit(self): self._rec("quit")


@pytest.fixture(scope="module")
def bus():
    test_bus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
    test_bus.up()
    try:
        yield test_bus.get_bus_address()
    finally:
        test_bus.down()


def _connect(address: str) -> Gio.DBusConnection:
    return Gio.DBusConnection.new_for_address_sync(
        address,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)


@pytest.fixture
def env(bus):
    store = Store()
    actions = RecordingActions(store)
    server = _connect(bus)
    service = PanelService(store, actions)
    service.export(server)
    owned = {}
    owner_id = Gio.bus_own_name_on_connection(
        server, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
        lambda *_: owned.setdefault("ok", True), lambda *_: owned.setdefault("lost", True))
    assert pump(3, until=lambda: owned), "could not own the bus name"
    assert "ok" in owned
    client = PanelClient(_connect(bus), timeout_ms=3000)
    yield store, actions, service, client
    service.unexport()
    Gio.bus_unown_name(owner_id)
    client.connection.close_sync(None)
    server.close_sync(None)
    pump(0.05)


# ── method plumbing ──

ACTION_CASES = [
    (("OpenDashboard", "sessions:board"), ("open_dashboard", "sessions:board")),
    (("OpenDashboard", ""), ("open_dashboard", "")),
    (("OpenPalette",), ("open_palette",)),
    (("SetHud", True), ("set_hud", True)),
    (("SetHud", False), ("set_hud", False)),
    (("PinTicker", True), ("pin_ticker", True)),
    (("FocusSession", "claude-ledgerly"), ("focus_session", "claude-ledgerly")),
    (("RetitleSession", "claude-ledgerly"), ("retitle_session", "claude-ledgerly")),
    (("RequestClose", "claude-a"), ("request_close", "claude-a")),
    (("CloseSession", "claude-a", True), ("close_session", "claude-a", True)),
    (("CloseSession", "claude-a", False), ("close_session", "claude-a", False)),
    (("RequestCloseAll",), ("request_close_all",)),
    (("CloseAll",), ("close_all",)),
    (("RequestNewSession",), ("request_new_session",)),
    (("NewSession", "/home/u/dev/x"), ("new_session", "/home/u/dev/x")),
    (("AnswerPrompt", "claude-a", 2, "fp123"), ("answer_prompt", "claude-a", 2, "fp123")),
]


@pytest.mark.parametrize("call, expected", ACTION_CASES,
                         ids=[c[0][0] + str(i) for i, c in enumerate(ACTION_CASES)])
def test_every_method_reaches_actions(env, call, expected):
    _store, actions, _svc, client = env
    assert in_thread(lambda: client.call(*call)) is None
    assert actions.calls == [expected]


def test_xml_and_dispatch_table_cover_the_same_methods():
    info = svc_mod.interface_info()
    xml_methods = {m.name for m in info.methods}
    assert xml_methods == set(svc_mod._ACTIONS) | {"GetState"}
    from smartpanel.dbus.client import SIGNATURES
    assert set(SIGNATURES) == xml_methods
    for m in info.methods:
        sig = "(" + "".join(a.signature for a in m.in_args) + ")"
        assert SIGNATURES[m.name] == sig, m.name


def test_answer_prompt_option_is_int(env):
    _s, actions, _svc, client = env
    in_thread(lambda: client.call("AnswerPrompt", "claude-a", 0, ""))
    (_n, _name, option, _fp), = actions.calls
    assert type(option) is int and option == 0


@pytest.mark.parametrize("value", [True, 7, 2.5, None, "~/dev", [1, 2], {"a": 1}])
def test_set_setting_decodes_json(env, value):
    _s, actions, _svc, client = env
    in_thread(lambda: client.call("SetSetting", "x.key", value))
    assert actions.calls == [("set_setting", "x.key", value)]


def test_set_setting_raw_json_and_bad_json(env):
    _s, actions, _svc, client = env
    in_thread(lambda: client.set_setting_json("budget.daily_usd", "25"))
    assert actions.calls == [("set_setting", "budget.daily_usd", 25)]
    with pytest.raises(PanelError) as err:
        in_thread(lambda: client.set_setting_json("budget.daily_usd", "{nope"))
    assert err.value.name == "org.freedesktop.DBus.Error.InvalidArgs"
    assert "budget.daily_usd" in err.value.message
    assert len(actions.calls) == 1


def test_action_failure_is_a_readable_error_and_service_survives(env):
    _s, actions, _svc, client = env
    actions.fail_with = RuntimeError("tmux is not installed")
    with pytest.raises(PanelError) as err:
        in_thread(lambda: client.call("FocusSession", "claude-a"))
    assert err.value.name == f"{INTERFACE}.Error.Failed"
    assert "FocusSession" in err.value.message
    assert "tmux is not installed" in err.value.message
    actions.fail_with = None
    in_thread(lambda: client.call("CloseAll"))
    assert actions.calls[-1] == ("close_all",)


def test_focus_session_result_travels_as_event(env):
    """SP-1701 plumbing: the ladder's outcome reaches D-Bus callers as an Event."""
    _s, _a, _svc, client = env
    got = []
    undo = client.watch_events(lambda kind, data: got.append((kind, data)))
    in_thread(lambda: client.call("FocusSession", "claude-a"))
    assert pump(2, until=lambda: got)
    undo()
    assert got[0][0] == "toast"
    assert got[0][1]["text"] == "Raised the terminal for “claude-a”"


# ── GetState / Version ──

def test_get_state_round_trip_all_sections(env):
    store, _a, _svc, client = env
    store.update("app", version="2.0.0", demo="busy")
    store.mutate("sessions", lambda s: s.items.append(
        Session(name="claude-x", project="x", title="Fix it ✳")))
    state = in_thread(lambda: client.get_state())
    assert set(state) == set(SECTIONS)
    assert state == json.loads(store.to_json())
    assert state["app"]["demo"] == "busy"
    assert state["sessions"]["items"][0]["title"] == "Fix it ✳"
    assert state["system"]["swap"]["percent"] is None


def test_get_state_section_filter(env):
    _s, _a, _svc, client = env
    state = in_thread(lambda: client.get_state(["spend", "app"]))
    assert set(state) == {"spend", "app"}
    assert len(state["spend"]["hourly_today"]) == 24


def test_get_state_unknown_section_is_invalid_args(env):
    _s, _a, _svc, client = env
    with pytest.raises(PanelError) as err:
        in_thread(lambda: client.get_state(["spend", "nope"]))
    assert err.value.name == "org.freedesktop.DBus.Error.InvalidArgs"
    assert "'nope'" in err.value.message


def test_version_property(env):
    _s, _a, _svc, client = env
    assert in_thread(lambda: client.version) == 1


def test_introspection_lists_interface(env):
    _s, _a, _svc, client = env
    reply = in_thread(lambda: client.connection.call_sync(
        BUS_NAME, OBJECT_PATH, "org.freedesktop.DBus.Introspectable", "Introspect",
        None, GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 3000, None))
    xml = reply.unpack()[0]
    assert INTERFACE in xml and "AnswerPrompt" in xml and "Changed" in xml


# ── signals ──

def test_changed_is_coalesced_into_one_signal(env):
    store, _a, _svc, client = env
    got: list[tuple[float, list[str]]] = []
    undo = client.watch_changed(lambda s: got.append((time.monotonic(), s)))
    pump(0.05)
    t0 = time.monotonic()
    for i in range(40):
        store.update("system", updated_at=float(i + 1))
    store.update("spend", cost_today=1.5)
    store.touch("sessions")
    store.update("system", updated_at=999.0)
    assert time.monotonic() - t0 < CHANGED_WINDOW_MS / 1000
    assert pump(2, until=lambda: got)
    pump(CHANGED_WINDOW_MS / 1000 * 2)       # a second signal would land here
    undo()
    assert len(got) == 1
    assert got[0][1] == ["sessions", "spend", "system"]
    assert got[0][0] - t0 >= CHANGED_WINDOW_MS / 1000 * 0.9


def test_changed_windows_are_separate(env):
    store, _a, _svc, client = env
    got: list[list[str]] = []
    undo = client.watch_changed(got.append)
    pump(0.05)
    store.touch("spend")
    assert pump(2, until=lambda: len(got) == 1)
    store.touch("journal")
    store.touch("app")
    assert pump(2, until=lambda: len(got) == 2)
    pump(0.4)
    undo()
    assert got == [["spend"], ["app", "journal"]]


def test_no_change_no_signal(env):
    store, _a, _svc, client = env
    got = []
    undo = client.watch_changed(got.append)
    store.update("app", version="")          # equal value: Store does not notify
    pump(0.4)
    undo()
    assert got == []


def test_store_events_are_forwarded(env):
    store, _a, _svc, client = env
    got = []
    undo = client.watch_events(lambda kind, data: got.append((kind, data)))
    pump(0.05)
    store.toast("Copied", action={"label": "Open", "page": "journal:abc"})
    store.emit(Event("open_palette"))
    store.emit(Event("weird", {"obj": object()}))   # unserializable -> str, no crash
    assert pump(2, until=lambda: len(got) == 3)
    undo()
    assert got[0] == ("toast", {"text": "Copied", "error": False,
                                "action": {"label": "Open", "page": "journal:abc"}})
    assert got[1] == ("open_palette", {})
    assert got[2][0] == "weird" and isinstance(got[2][1]["obj"], str)


def test_action_events_are_forwarded(env):
    _s, _a, _svc, client = env
    got = []
    undo = client.watch_events(lambda kind, data: got.append((kind, data)))
    in_thread(lambda: client.call("RequestClose", "claude-z"))
    assert pump(2, until=lambda: got)
    undo()
    assert got == [("confirm_close", {"name": "claude-z"})]


# ── lifecycle ──

def test_unexport_stops_signals_and_calls(env):
    store, _a, service, client = env
    got = []
    undo = client.watch_changed(got.append)
    store.touch("spend")          # pending when unexported: must be dropped
    service.unexport()
    assert not service.exported
    store.touch("app")
    store.toast("ignored")
    pump(0.5)
    undo()
    assert got == []
    with pytest.raises(PanelError) as err:
        in_thread(lambda: client.call("OpenPalette"))
    assert "UnknownMethod" in err.value.name or "UnknownObject" in err.value.name
    service.unexport()            # idempotent


def test_export_is_idempotent_on_same_connection(env):
    store, _a, service, client = env
    conn = service._connection
    service.export(conn)          # no second registration / subscription
    got = []
    undo = client.watch_changed(got.append)
    store.touch("spend")
    assert pump(2, until=lambda: got)
    pump(0.4)
    undo()
    assert got == [["spend"]]


# ── pure helpers ──

def test_parse_sections():
    assert svc_mod.parse_sections([]) is None
    assert svc_mod.parse_sections(["spend"]) == {"spend"}
    with pytest.raises(svc_mod.InvalidArgs):
        svc_mod.parse_sections(["bogus"])


def test_introspection_xml_is_package_data():
    xml = svc_mod.introspection_xml()
    assert 'interface name="dev.omdev.SmartPanel1"' in xml


# ── SP-2301: the reduced HUD view ──

def test_get_state_hud_view_is_reduced(env):
    store, _a, _svc, client = env
    store.update("spend", cost_today=23.84, tokens_today=41_200_000,
                 tokens_today_human="41.2M", burn_per_hour=3.1)

    def fill(system):
        system.cpu.percent, system.memory.percent = 12.0, 64.0
        system.swap.percent, system.swap.level = 92.0, 2
        system.disk.percent = 71.0
        system.cpu.history = [1.0] * 300
    store.mutate("system", fill)
    store.mutate("sessions", lambda s: s.items.extend([
        Session(name="claude-a", project="a", title="Fix it", activity="waiting",
                phase="none"),
        Session(name="claude-b", project="b"),
    ]))
    store.update("app", first_run=False)

    state = in_thread(lambda: client.get_state(["hud"]))
    assert set(state) == {"hud"}
    hud = state["hud"]
    assert set(hud) == {"spend", "sessions", "metrics", "swap_level", "loading",
                        "first_run"}
    assert hud["spend"] == {"cost_today": 23.84, "tokens_today": 41_200_000,
                            "tokens_today_human": "41.2M", "burn_per_hour": 3.1}
    assert hud["metrics"] == {"cpu": 12.0, "memory": 64.0, "swap": 92.0, "disk": 71.0}
    assert hud["swap_level"] == 2
    assert hud["loading"] is None and hud["first_run"] is False
    assert [set(s) for s in hud["sessions"]] == [{"name", "title", "activity", "phase"}] * 2
    assert hud["sessions"][0]["title"] == "Fix it"
    assert hud["sessions"][1]["title"] == "b"          # display_title fallback

    def no_lists_of_numbers(obj):
        if isinstance(obj, dict):
            return all(no_lists_of_numbers(v) for v in obj.values())
        if isinstance(obj, list):
            return all(isinstance(v, dict) and no_lists_of_numbers(v) for v in obj)
        return True
    assert no_lists_of_numbers(hud), "HUD view must carry no history arrays"


def test_get_state_hud_loading(env):
    store, _a, _svc, client = env
    from smartpanel.state.model import Loading
    store.update("app", loading=Loading(done=3, total=10), first_run=True)
    hud = in_thread(lambda: client.get_state(["hud"]))["hud"]
    assert hud["loading"] == {"done": 3, "total": 10}
    assert hud["first_run"] is True


def test_get_state_hud_must_be_alone(env):
    _s, _a, _svc, client = env
    with pytest.raises(PanelError) as err:
        in_thread(lambda: client.get_state(["hud", "spend"]))
    assert err.value.name == "org.freedesktop.DBus.Error.InvalidArgs"
    assert "alone" in err.value.message


# ── SP-0006: histories are trimmed to the newest 60 points over D-Bus ──

def test_get_state_trims_system_histories(env):
    store, _a, _svc, client = env

    def fill(system):
        system.cpu.history = [float(i) for i in range(100)]
        system.net.history_down = [float(i) for i in range(100)]
        system.memory.history = [1.0] * 10
    store.mutate("system", fill)
    system = in_thread(lambda: client.get_state(["system"]))["system"]
    assert system["cpu"]["history"] == [float(i) for i in range(40, 100)]
    assert len(system["net"]["history_down"]) == svc_mod.HISTORY_POINTS == 60
    assert system["memory"]["history"] == [1.0] * 10
    assert len(store.state.system.cpu.history) == 100     # the Store is untouched


def test_trim_histories_pure():
    state = {"system": {"cpu": {"history": list(range(5)), "percent": 1},
                        "updated_at": None},
             "spend": {"hourly_today": list(range(24))}}
    out = svc_mod.trim_histories(state, points=2)
    assert out["system"]["cpu"]["history"] == [3, 4]
    assert len(out["spend"]["hourly_today"]) == 24       # only system.* is trimmed


def test_parse_sections_hud():
    assert svc_mod.parse_sections(["hud"]) == {"hud"}
    with pytest.raises(svc_mod.InvalidArgs):
        svc_mod.parse_sections(["hud", "app"])


# ── client robustness: spoofed / malformed signals never raise into the loop ──

def test_client_survives_malformed_event_and_failing_handler(env):
    _store, _a, service, client = env
    got = []
    undo = client.watch_events(lambda kind, data: got.append((kind, data)))
    calls = []

    def boom(sections):
        calls.append(sections)
        raise RuntimeError("handler bug")
    undo2 = client.watch_changed(boom)
    pump(0.05)
    service._emit("Event", GLib.Variant("(ss)", ("toast", "{not json")))
    service._emit("Event", GLib.Variant("(ss)", ("toast", "[1, 2]")))
    service._emit("Changed", GLib.Variant("(as)", (["spend"],)))
    assert pump(2, until=lambda: len(got) == 2 and calls)
    undo()
    undo2()
    assert got == [("toast", {}), ("toast", {})]
