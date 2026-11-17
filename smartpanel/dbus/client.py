"""Small synchronous client for ``dev.omdev.SmartPanel1``.

For tests, scripts and future tools living in *another* process (or another thread)
than the service::

    c = PanelClient()                      # session bus, dev.omdev.SmartPanel
    c.version                              # -> 1
    c.get_state(["spend"])                 # -> {"spend": {...}}
    c.call("FocusSession", "claude-ledgerly")
    c.call("SetSetting", "hud.enabled", True)   # a Python value, JSON-encoded for you
    c.set_setting_json("hud.enabled", "true")    # already-encoded JSON text
    undo = c.watch_changed(lambda sections: ...)   # needs a running GLib main loop
    undo()

Calls block the calling thread. Never call it from the thread whose main context runs
the service itself (that would deadlock until the timeout): the service's own process
already has the Store.

Errors come back as ``PanelError`` carrying the D-Bus error name and the service's
readable message.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from gi.repository import Gio, GLib

BUS_NAME = "dev.omdev.SmartPanel"
OBJECT_PATH = "/dev/omdev/SmartPanel"
INTERFACE = "dev.omdev.SmartPanel1"
DEFAULT_TIMEOUT_MS = 5000

log = logging.getLogger(__name__)

# Method name -> GVariant input signature (mirrors panel1.xml).
SIGNATURES: dict[str, str] = {
    "GetState": "(as)",
    "OpenDashboard": "(s)",
    "OpenPalette": "()",
    "SetHud": "(b)",
    "PinTicker": "(b)",
    "FocusSession": "(s)",
    "RetitleSession": "(s)",
    "RequestClose": "(s)",
    "CloseSession": "(sb)",
    "RequestCloseAll": "()",
    "CloseAll": "()",
    "RequestNewSession": "()",
    "NewSession": "(s)",
    "AnswerPrompt": "(sus)",
    "SetSetting": "(ss)",
}


class PanelError(RuntimeError):
    def __init__(self, name: str, message: str):
        super().__init__(f"{name}: {message}" if name else message)
        self.name, self.message = name, message


def _wrap(exc: GLib.Error) -> PanelError:
    name = Gio.DBusError.get_remote_error(exc) or ""
    if name:
        Gio.DBusError.strip_remote_error(exc)
    return PanelError(name, exc.message)


class PanelClient:
    def __init__(self, connection: Gio.DBusConnection | None = None, *,
                 bus_name: str = BUS_NAME, timeout_ms: int = DEFAULT_TIMEOUT_MS):
        self.connection = connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.bus_name = bus_name
        self.timeout_ms = timeout_ms

    # ── calls ──
    def call(self, method: str, *args: Any) -> Any:
        """Invoke a method; returns its single out value, a tuple, or None."""
        sig = SIGNATURES.get(method)
        if sig is None:
            raise ValueError(f"unknown method {method!r}")
        if method == "SetSetting" and len(args) == 2:
            # Always a Python value: "5" stays the string "5", 5 is the number.
            args = (args[0], json.dumps(args[1]))
        params = GLib.Variant(sig, tuple(args))
        try:
            reply = self.connection.call_sync(
                self.bus_name, OBJECT_PATH, INTERFACE, method, params, None,
                Gio.DBusCallFlags.NONE, self.timeout_ms, None)
        except GLib.Error as exc:
            raise _wrap(exc) from None
        out = reply.unpack() if reply is not None else ()
        if not out:
            return None
        return out[0] if len(out) == 1 else out

    def set_setting_json(self, key: str, json_value: str) -> None:
        """SetSetting with raw JSON text, sent as-is (the service decodes it)."""
        try:
            self.connection.call_sync(
                self.bus_name, OBJECT_PATH, INTERFACE, "SetSetting",
                GLib.Variant("(ss)", (key, json_value)), None,
                Gio.DBusCallFlags.NONE, self.timeout_ms, None)
        except GLib.Error as exc:
            raise _wrap(exc) from None

    def get_state(self, sections: list[str] | tuple[str, ...] | None = None) -> dict:
        return json.loads(self.call("GetState", list(sections or [])))

    @property
    def version(self) -> int:
        try:
            reply = self.connection.call_sync(
                self.bus_name, OBJECT_PATH, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (INTERFACE, "Version")), GLib.VariantType("(v)"),
                Gio.DBusCallFlags.NONE, self.timeout_ms, None)
        except GLib.Error as exc:
            raise _wrap(exc) from None
        return int(reply.unpack()[0])

    # ── signals (delivered on the caller's thread-default main context) ──
    def _subscribe(self, signal: str, handler: Callable[[tuple], None]) -> Callable[[], None]:
        def on_signal(_conn, _sender, _path, _iface, _name, params):
            # Never raise into the GLib loop, whatever the sender put on the bus.
            try:
                handler(params.unpack())
            except Exception:
                log.exception("%s handler failed", signal)

        sub = self.connection.signal_subscribe(
            self.bus_name, INTERFACE, signal, OBJECT_PATH, None,
            Gio.DBusSignalFlags.NONE, on_signal)
        done = []

        def undo() -> None:
            if not done:
                done.append(True)
                self.connection.signal_unsubscribe(sub)
        return undo

    def watch_changed(self, callback: Callable[[list[str]], None]) -> Callable[[], None]:
        return self._subscribe("Changed", lambda p: callback(list(p[0])))

    def watch_events(self, callback: Callable[[str, dict], None]) -> Callable[[], None]:
        return self._subscribe("Event", lambda p: callback(p[0], _loads_dict(p[1])))


def _loads_dict(text: str) -> dict:
    try:
        data = json.loads(text or "{}")
    except ValueError:
        log.warning("Event data is not JSON: %.80r", text)
        return {}
    return data if isinstance(data, dict) else {}
