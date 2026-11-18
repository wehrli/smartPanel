"""Exports ``dev.omdev.SmartPanel1`` (panel1.xml) at ``/dev/omdev/SmartPanel``.

API (fixed by the v2 contract)::

    svc = PanelService(store, actions)
    svc.export(connection)     # from Adw.Application.do_dbus_register (same connection)
    ...
    svc.unexport()             # from do_dbus_unregister

The application owns the bus name ``dev.omdev.SmartPanel``; this module only registers
the object on the connection it is handed, so the name and the object share a lifetime.

What the interface does
-----------------------
- ``GetState(as sections) -> s``: the JSON of ``Store.to_dict(sections)`` with system
  histories trimmed (below); an empty list means every section, ``['hud']`` is the
  reduced HUD view. An unknown section name is an ``InvalidArgs`` error, not a silent empty dict.
- ``Version`` (``u``) is the JSON schema version (``SCHEMA_VERSION``). Consumers should
  check it once on connect and refuse/degrade on a mismatch.
- ``Changed(as sections)`` is **coalesced**: store notifications are collected and
  emitted at most once per ``CHANGED_WINDOW_MS`` (250 ms) window, carrying the union of
  every section touched inside it. A burst of 50 updates is one signal.
- ``Event(s kind, s data)`` mirrors every ``state.model.Event`` the store emits (toasts,
  confirm dialogs, navigation), immediately and uncoalesced: they are one-shot.
- Every action method maps 1:1 onto ``state.actions.Actions`` and returns immediately
  (the Actions contract forbids blocking). Results the user should see come back as
  ``Event`` signals, never as return values, with one exception:
- ``FocusSession(s name) -> s result`` replies with the focus ladder's outcome,
  ``raised`` / ``reattached`` / ``failed`` (SP-1701), once the ladder has run on the
  controller's worker. The handler returns at once; the reply is sent from the
  ``on_result`` callback ``Controller.focus_session`` posts back to the main thread. An
  Actions implementation without ``on_result`` (the demo controller) is called plainly
  and the reply is ``failed``: no real window was raised. The same outcome also shows
  up as the ``toast`` Event and as ``Session.attached`` in the next ``sessions`` change.
- ``SetSetting`` takes its value as a JSON string and decodes it, then checks it here,
  before calling ``Actions.set_setting`` (whose own failures only toast, for the GTK
  callers): an unknown key or an invalid value is ``InvalidArgs``, a key an env var
  owns (``settings.overridden_by``) is ``dev.omdev.SmartPanel1.Error.Overridden``. A
  rejected remote call never reaches the controller, so it never toasts in the user's UI.
- A failing action becomes a D-Bus error reply with a readable message
  (``dev.omdev.SmartPanel1.Error.Failed``). Nothing raised by an action or a listener
  can reach the GLib loop.

Low-rate consumers (the desktop HUD extension, SP-2301)
-------------------------------------------------------
The HUD draws the spend line, up to four session rows, cpu / memory / swap / net-down
and the swap alert, placed by ``hud.enabled`` / ``hud.corner`` / ``hud.margin``. Rather
than a new method (the interface in panel1.xml is fixed), ``GetState`` accepts the
pseudo-section ``"hud"``, which returns ``hud_view()``: the **same tree shape** as the
real sections, pruned to exactly the fields ``extension/.../hudmodel.js`` reads, so
``buildHudModel(reply.hud)`` and ``hudSettings(reply.hud)`` work unchanged::

    {"hud": {
        "spend":    {"cost_today", "tokens_today", "tokens_today_human", "burn_per_hour"},
        "sessions": {"tmux_ok": bool,
                     "items": [{"name", "title", "project", "activity", "phase"}, ...]},
        "system":   {"cpu": {"percent"}, "memory": {"percent"},
                     "swap": {"percent", "level"}, "net": {"down_bps"}},
        "app":      {"loading": null | {"done", "total"}, "first_run": bool},
        "settings": {"values": {"hud.enabled", "hud.corner", "hud.margin"}}
    }}

About 1 KB instead of about 15 KB for the five full sections: no history arrays, no
per-day breakdowns, no prompts, no other settings. ``"hud"`` must be requested
**alone**: mixing it with real sections is an ``InvalidArgs`` error, so a reply is either
a slice of the state tree or the HUD view, never an ambiguous mix.

The rate limit is the client's, by design (a signal-driven server cannot know a
consumer's cadence). The intended recipe:

1. On connect, read ``Version``; then ``GetState(['hud'])`` once.
2. Subscribe to ``Changed``. Ignore it unless its sections intersect ``HUD_SECTIONS``
   (``spend``, ``sessions``, ``system``, ``app``, ``settings``; a ``journal``-only
   change never touches the HUD). A ``settings`` change is urgent (the HUD may have
   been switched on or moved); anything else is throttled.
3. Fetch at most once per 5 s: one trailing ``GetState(['hud'])`` per window.

History trimming (SP-0006): the Store keeps up to 300 points per series, but over D-Bus
every ``history*`` list under ``system`` is cut to its newest ``HISTORY_POINTS`` (60).

Threading: this object lives on the main thread. Store listeners run on the main
thread (the Store contract), D-Bus method calls are dispatched on the main context the
service was exported from, and the coalescing timer is a GLib timeout.
"""
from __future__ import annotations

import inspect
import json
import logging
from importlib import resources
from typing import Any, Callable

from gi.repository import Gio, GLib

from smartpanel import config as config_mod
from smartpanel.state.model import SECTIONS, Event
from smartpanel.state.store import to_dict

log = logging.getLogger(__name__)

BUS_NAME = "dev.omdev.SmartPanel"
OBJECT_PATH = "/dev/omdev/SmartPanel"
INTERFACE = "dev.omdev.SmartPanel1"
SCHEMA_VERSION = 1
CHANGED_WINDOW_MS = 250
HISTORY_POINTS = 60                      # newest points per series sent over D-Bus
HUD = "hud"                              # GetState pseudo-section (SP-2301)
HUD_SECTIONS = ("spend", "sessions", "system", "app", "settings")  # what hud_view reads
HUD_SETTINGS = ("hud.enabled", "hud.corner", "hud.margin")

ERROR_FAILED = f"{INTERFACE}.Error.Failed"
ERROR_OVERRIDDEN = f"{INTERFACE}.Error.Overridden"
FOCUS_FAILED = "failed"                  # == sessions.focus.FAILED
ERROR_INVALID_ARGS = "org.freedesktop.DBus.Error.InvalidArgs"
ERROR_UNKNOWN_METHOD = "org.freedesktop.DBus.Error.UnknownMethod"


class InvalidArgs(ValueError):
    """A caller error: reported as org.freedesktop.DBus.Error.InvalidArgs."""


class Overridden(ValueError):
    """SetSetting on a key an env var owns: reported as Error.Overridden."""


def introspection_xml() -> str:
    """The interface XML shipped next to this module (package data)."""
    return resources.files("smartpanel.dbus").joinpath("panel1.xml").read_text("utf-8")


_NODE_INFO: Gio.DBusNodeInfo | None = None


def interface_info() -> Gio.DBusInterfaceInfo:
    global _NODE_INFO
    if _NODE_INFO is None:
        _NODE_INFO = Gio.DBusNodeInfo.new_for_xml(introspection_xml())
    info = _NODE_INFO.lookup_interface(INTERFACE)
    if info is None:  # pragma: no cover - the XML is part of the package
        raise RuntimeError(f"{INTERFACE} missing from panel1.xml")
    return info


def parse_sections(sections: list[str] | tuple[str, ...]) -> set[str] | None:
    """GetState's ``as`` argument -> Store.to_json's filter. Empty means all.

    ``["hud"]`` is returned as ``{"hud"}``; ``"hud"`` mixed with anything else is an
    error.
    """
    if not sections:
        return None
    if HUD in sections:
        if any(s != HUD for s in sections):
            raise InvalidArgs(f"'{HUD}' must be requested alone")
        return {HUD}
    unknown = [s for s in sections if s not in SECTIONS]
    if unknown:
        raise InvalidArgs(
            f"unknown section(s) {', '.join(map(repr, unknown))}; "
            f"expected any of {', '.join(SECTIONS)} (or '{HUD}' alone)")
    return set(sections)


def trim_histories(state: dict, points: int = HISTORY_POINTS) -> dict:
    """Cut every ``history*`` list under ``state['system']`` to its newest ``points``."""
    system = state.get("system")
    if isinstance(system, dict):
        for part in system.values():
            if isinstance(part, dict):
                for key, val in part.items():
                    if key.startswith("history") and isinstance(val, list) \
                            and len(val) > points:
                        part[key] = val[-points:]
    return state


def hud_view(state) -> dict:
    """PanelState -> the reduced HUD view: the state tree pruned to what the HUD reads
    (see the module docstring)."""
    sp, sy, ss, app = state.spend, state.system, state.sessions, state.app
    values = state.settings.values or {}
    return {
        "spend": {
            "cost_today": sp.cost_today,
            "tokens_today": sp.tokens_today,
            "tokens_today_human": sp.tokens_today_human,
            "burn_per_hour": sp.burn_per_hour,
        },
        "sessions": {
            "tmux_ok": bool(ss.tmux_ok),
            "items": [
                {"name": s.name, "title": s.display_title, "project": s.project,
                 "activity": s.activity, "phase": s.phase}
                for s in ss.items
            ],
        },
        "system": {
            "cpu": {"percent": sy.cpu.percent},
            "memory": {"percent": sy.memory.percent},
            "swap": {"percent": sy.swap.percent, "level": int(sy.swap.level)},
            "net": {"down_bps": sy.net.down_bps},
        },
        "app": {
            "loading": to_dict(app.loading),
            "first_run": bool(app.first_run),
        },
        "settings": {"values": {k: values[k] for k in HUD_SETTINGS if k in values}},
    }


def state_json(store, sections: set[str] | None) -> str:
    """GetState's reply body."""
    if sections == {HUD}:
        body = {HUD: hud_view(store.state)}
    else:
        body = trim_histories(store.to_dict(sections))
    return json.dumps(body, separators=(",", ":"))


def decode_setting(key: str, json_value: str) -> Any:
    if not key:
        raise InvalidArgs("SetSetting: empty key")
    try:
        return json.loads(json_value)
    except (TypeError, ValueError) as exc:
        raise InvalidArgs(f"SetSetting {key}: value is not valid JSON ({exc})") from None


def check_setting(store, key: str, value: Any) -> None:
    """Refuse what ``Config.set`` would refuse, so the remote caller gets the error.

    Validation is ``config.validate`` (unknown key or bad value -> InvalidArgs); env
    ownership is read from ``store.settings.overridden_by``, which ``Config`` mirrors.
    """
    try:
        config_mod.validate(key, value)
    except ValueError as exc:
        raise InvalidArgs(f"SetSetting: {exc}") from None
    try:
        owner = (store.state.settings.overridden_by or {}).get(key)
    except AttributeError:
        owner = None
    if owner:
        raise Overridden(f"{key} is set by {owner}; unset it to change this here")


def accepts_on_result(fn) -> bool:
    try:
        return "on_result" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def event_json(event: Event) -> str:
    """Event.data -> compact JSON; tolerant of dataclasses and odd values."""
    return json.dumps(to_dict(event.data), separators=(",", ":"), default=str)


# D-Bus method name -> (Actions method, argument adapter). The adapter turns the
# unpacked D-Bus tuple into the Python call's positional args.
_ACTIONS: dict[str, tuple[str, Callable[[tuple], tuple]]] = {
    "OpenDashboard": ("open_dashboard", lambda a: (a[0],)),
    "OpenPalette": ("open_palette", lambda a: ()),
    "SetHud": ("set_hud", lambda a: (bool(a[0]),)),
    "PinTicker": ("pin_ticker", lambda a: (bool(a[0]),)),
    "RetitleSession": ("retitle_session", lambda a: (a[0],)),
    "RequestClose": ("request_close", lambda a: (a[0],)),
    "CloseSession": ("close_session", lambda a: (a[0], bool(a[1]))),
    "RequestCloseAll": ("request_close_all", lambda a: ()),
    "CloseAll": ("close_all", lambda a: ()),
    "RequestNewSession": ("request_new_session", lambda a: ()),
    "NewSession": ("new_session", lambda a: (a[0],)),
    "AnswerPrompt": ("answer_prompt", lambda a: (a[0], int(a[1]), a[2])),
    "OpenPath": ("open_path", lambda a: (a[0],)),
    "SetSetting": ("set_setting", lambda a: (a[0], decode_setting(a[0], a[1]))),
}


class PanelService:
    def __init__(self, store, actions):
        self.store, self.actions = store, actions
        self._connection: Gio.DBusConnection | None = None
        self._reg_id = 0
        self._unsubscribe: list[Callable[[], None]] = []
        self._pending: set[str] = set()
        self._timer = 0

    # ── lifecycle ──
    @property
    def exported(self) -> bool:
        return self._reg_id != 0

    def export(self, connection) -> None:
        if self._reg_id:
            if connection is self._connection:
                return
            self.unexport()
        # GLib >= 2.84 deprecates register_object in favour of the closures2 variant.
        register = getattr(connection, "register_object_with_closures2", None) \
            or connection.register_object
        self._reg_id = register(
            OBJECT_PATH, interface_info(),
            self._on_method_call, self._on_get_property, None)
        self._connection = connection
        self._unsubscribe = [
            self.store.subscribe(self._on_store_changed),
            self.store.on_event(self._on_store_event),
        ]

    def unexport(self) -> None:
        for undo in self._unsubscribe:
            try:
                undo()
            except Exception:  # pragma: no cover - defensive
                log.exception("unsubscribe failed")
        self._unsubscribe = []
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self._pending.clear()
        if self._reg_id and self._connection is not None:
            try:
                self._connection.unregister_object(self._reg_id)
            except Exception:  # connection already closed
                log.debug("unregister_object failed", exc_info=True)
        self._reg_id = 0
        self._connection = None

    # ── outgoing: Changed (coalesced) and Event ──
    def _on_store_changed(self, sections: set[str]) -> None:
        self._pending |= sections
        if not self._timer and self._pending:
            self._timer = GLib.timeout_add(CHANGED_WINDOW_MS, self._flush_changed)

    def _flush_changed(self) -> bool:
        self._timer = 0
        sections = sorted(self._pending)
        self._pending.clear()
        if sections:
            self._emit("Changed", GLib.Variant("(as)", (sections,)))
        return GLib.SOURCE_REMOVE

    def _on_store_event(self, event: Event) -> None:
        try:
            data = event_json(event)
        except Exception:
            log.exception("Event %s: data not serializable", event.kind)
            data = "{}"
        self._emit("Event", GLib.Variant("(ss)", (event.kind, data)))

    def _emit(self, signal: str, params: GLib.Variant) -> None:
        if self._connection is None:
            return
        try:
            self._connection.emit_signal(None, OBJECT_PATH, INTERFACE, signal, params)
        except Exception:
            log.warning("emitting %s failed", signal, exc_info=True)

    # ── incoming ──
    def _on_get_property(self, _conn, _sender, _path, _iface, name):
        if name == "Version":
            return GLib.Variant("u", SCHEMA_VERSION)
        return None

    def _on_method_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        try:
            args = params.unpack() if params is not None else ()
            if method == "GetState":
                payload = state_json(self.store, parse_sections(args[0]))
                invocation.return_value(GLib.Variant("(s)", (payload,)))
                return
            if method == "FocusSession":
                self._focus_session(args[0], invocation)
                return
            entry = _ACTIONS.get(method)
            if entry is None:
                invocation.return_dbus_error(ERROR_UNKNOWN_METHOD, f"no method {method}")
                return
            attr, adapt = entry
            call_args = adapt(args)
            if method == "SetSetting":
                check_setting(self.store, *call_args)
            getattr(self.actions, attr)(*call_args)
            invocation.return_value(None)
        except InvalidArgs as exc:
            invocation.return_dbus_error(ERROR_INVALID_ARGS, str(exc))
        except Overridden as exc:
            invocation.return_dbus_error(ERROR_OVERRIDDEN, str(exc))
        except Exception as exc:
            log.exception("D-Bus %s failed", method)
            invocation.return_dbus_error(
                ERROR_FAILED, f"{method} failed: {type(exc).__name__}: {exc}")

    def _focus_session(self, name: str, invocation) -> None:
        """Reply with the ladder's result once it arrives (exactly once)."""
        fn = self.actions.focus_session
        if not accepts_on_result(fn):
            fn(name)
            invocation.return_value(GLib.Variant("(s)", (FOCUS_FAILED,)))
            return
        replied: list[bool] = []

        def on_result(result: str) -> None:
            if replied:
                return
            replied.append(True)
            invocation.return_value(GLib.Variant("(s)", (str(result or FOCUS_FAILED),)))

        try:
            fn(name, on_result=on_result)
        except Exception:
            if replied:          # pragma: no cover - replied, then raised
                log.exception("D-Bus FocusSession failed after replying")
                return
            replied.append(True)
            raise
