"""The smartPanel process: one held Adw.Application (architecture v2, "Process model").

    SmartPanelApp  id dev.omdev.SmartPanel, HANDLES_COMMAND_LINE, held, no window at start

- **Single instance (SP-0011).** GApplication forwards a second `smartpanel …`
  to this process's `do_command_line`; its exit status goes back to the caller.
- **Wiring** happens on the first (local) command line, because it depends on
  `--demo` / `--theme`: Config → Store → collectors and services (or the demo
  controller) → tray. `do_dbus_register` exports the D-Bus API on the app's own
  connection, against an `ActionsProxy` that starts forwarding once wiring is done.
- **Events** from the store become windows: `open_dashboard`, `open_palette`,
  `toast` (in-window, or a desktop notification when no window is showing),
  `confirm_close` / `confirm_close_all` / `new_session` (dialogs) and `quit`.
"""
from __future__ import annotations

import argparse
import inspect
import logging
import signal

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402

from smartpanel import __version__, cli  # noqa: E402
from smartpanel.state import Event, Store  # noqa: E402
from smartpanel.state.model import SECTIONS  # noqa: E402

log = logging.getLogger(__name__)

APP_ID = "dev.omdev.SmartPanel"


def app_id() -> str:
    """The bus name. `SMARTPANEL_APP_ID` overrides it for side-by-side dev runs
    (the HUD extension and other clients only ever talk to the default)."""
    import os
    return os.environ.get("SMARTPANEL_APP_ID") or APP_ID


class ActionsProxy:
    """Stands in for the Actions implementation until wiring is done.

    D-Bus is registered before the first command line is parsed (that's when we
    learn about --demo), so the exported service gets this proxy; it forwards to
    the real controller as soon as there is one and drops calls (with a log
    line) before that.
    """

    def __init__(self):
        self.target = None

    def __getattr__(self, name):
        target = self.__dict__.get("target")
        if target is not None:
            return getattr(target, name)

        def _not_ready(*_a, **_k):
            log.info("action %s ignored: still starting", name)
            return [] if name == "list_start_folders" else None
        return _not_ready


def _own_method(obj, name: str):
    """`obj.name` only if the class really defines it — not something a
    catch-all `__getattr__` (stub controllers, ActionsProxy) makes up."""
    if obj is None:
        return None
    try:
        inspect.getattr_static(obj, name)
    except AttributeError:
        return None
    fn = getattr(obj, name, None)
    return fn if callable(fn) else None


def _platform_token(command_line) -> str | None:
    """Activation token a forwarded command line carries, if any."""
    try:
        data = command_line.get_platform_data()
        unpacked = data.unpack() if data is not None else {}
    except Exception:  # noqa: BLE001 — a missing token only costs focus
        return None
    return cli.activation_token(unpacked)


def _accepts_kwarg(cls, name: str) -> bool:
    try:
        return name in inspect.signature(cls).parameters
    except (TypeError, ValueError):
        return False


class SmartPanelApp(Adw.Application):
    def __init__(self, application_id: str | None = None):
        super().__init__(application_id=application_id or app_id(),
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.store = Store()
        self.actions = ActionsProxy()
        self.config = None
        self.theme = None
        self.window = None
        self.palette = None
        self.tray = None
        self.dbus_service = None
        self._connection = None
        self._services: list = []
        self._wired = False
        self._held = False
        self._unsubs: list = []
        self._timing = False

    # ───────────────────────── GApplication vfuncs ─────────────────────────

    def do_dbus_register(self, connection, object_path) -> bool:
        if not Adw.Application.do_dbus_register(self, connection, object_path):
            return False
        # Also called in a process that turns out to be a remote; that one gets
        # do_dbus_unregister right after, before it ever reaches command_line.
        self._connection = connection
        try:
            from smartpanel.dbus.service import PanelService
            self.dbus_service = PanelService(self.store, self.actions)
            self.dbus_service.export(connection)
        except Exception:  # noqa: BLE001 — the panel works without its D-Bus API
            log.exception("D-Bus service export failed")
            self.dbus_service = None
        return True

    def do_dbus_unregister(self, connection, object_path) -> None:
        if self.dbus_service is not None:
            try:
                self.dbus_service.unexport()
            except Exception:  # noqa: BLE001
                log.exception("D-Bus service unexport failed")
            self.dbus_service = None
        self._connection = None
        Adw.Application.do_dbus_unregister(self, connection, object_path)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        from smartpanel.ui import tokens
        tokens.install()
        try:
            gi.require_version("GLibUnix", "2.0")
            from gi.repository import GLibUnix
            add_signal = GLibUnix.signal_add
        except (ImportError, ValueError):
            add_signal = GLib.unix_signal_add
        for sig in (signal.SIGINT, signal.SIGTERM):
            add_signal(GLib.PRIORITY_DEFAULT, sig, self._on_signal)

    def do_command_line(self, command_line) -> int:
        argv = list(command_line.get_arguments()[1:])
        remote = command_line.get_is_remote()
        try:
            ns = cli.parse_args(argv)
        except cli.CliError as e:
            command_line.printerr_literal(f"{e}\n")
            return 2
        # Only the invocation that starts the service sets its verbosity; a
        # forwarded `smartpanel -v --palette` must not change the daemon's log level.
        if (ns.verbose or ns.debug) and not remote and not self._wired:
            logging.getLogger().setLevel(cli.log_level(ns))

        if not self._wired:
            try:
                self._wire(ns)
            except cli.CliError as e:
                command_line.printerr_literal(f"{e}\n")
                self._abort_start()
                return 2
            except Exception as e:  # noqa: BLE001 — SP-0011: a failed start is rc 1
                log.exception("startup failed")
                command_line.printerr_literal(f"smartpanel: failed to start: {e}\n")
                self._abort_start()
                return 1
            if not self._held:
                self.hold()
                self._held = True
        elif remote:
            ignored = cli.ignored_when_remote(ns)
            if ignored:
                command_line.printerr_literal(
                    f"smartpanel: already running; {', '.join(ignored)} only applies "
                    f"at startup (restart the service to change it)\n")

        token = _platform_token(command_line)
        for req in cli.requests_from(ns, remote=remote):
            self._run_request(req, token=token)
        return 0

    def do_shutdown(self) -> None:
        if self._timing:
            from smartpanel import timing
            report = timing.report()
            log.warning("main-loop timing report:\n%s", report)
            import sys
            print(report, file=sys.stderr)
            timing.uninstall()
            self._timing = False
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:  # noqa: BLE001
                pass
        for svc in [self.tray, *reversed(self._services)]:
            stop = _own_method(svc, "stop")
            if stop is not None:
                try:
                    stop()
                except Exception:  # noqa: BLE001
                    log.exception("stopping %r failed", svc)
        if self.window is not None:
            self.window.shutdown()
            self.window = None
        Adw.Application.do_shutdown(self)

    # ───────────────────────── wiring ─────────────────────────

    def _abort_start(self) -> None:
        """Undo a half-done `_wire` so the process exits instead of idling
        un-wired (and so nothing is subscribed twice)."""
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:  # noqa: BLE001
                pass
        self._unsubs.clear()
        for svc in reversed(self._services):
            stop = _own_method(svc, "stop")
            if stop is not None:
                try:
                    stop()
                except Exception:  # noqa: BLE001
                    log.exception("stopping %r failed", svc)
        self._services.clear()
        if not self._held:
            self.quit()

    def _wire(self, ns: argparse.Namespace) -> None:
        from smartpanel.config import Config
        from smartpanel.theme.loader import load_theme

        if getattr(ns, "debug_timing", False):
            from smartpanel import timing
            timing.install()
            self._timing = True
        self.theme = load_theme(cli.resolve_theme(ns.theme))
        self.config = Config.load()

        self._apply_style(self.config.get("appearance.style"))
        self._unsubs.append(self.config.subscribe(self._on_setting))
        self._unsubs.append(self.store.on_event(self._on_event))

        if ns.demo:
            from smartpanel import demo
            state = demo.build_state(ns.demo)
            state.app.demo = ns.demo
            if not state.app.version:
                state.app.version = __version__
            self.store.state = state
            # The whole state was replaced: every section changed (D-Bus
            # Changed(sections) listeners exported earlier must hear all of them).
            with self.store.batch():
                for section in SECTIONS:
                    self.store.touch(section)
        else:
            self.store.update("app", version=__version__)
        # After the demo state swap (so the settings section it fills survives)
        # and before anything starts reading the store.
        if hasattr(self.config, "bind_store"):
            self.config.bind_store(self.store)

        if ns.demo:
            controller = demo.DemoController(self.store, self.config)
            started = [controller]
        else:
            from smartpanel.collect.system import SystemCollector
            from smartpanel.services.controller import Controller
            from smartpanel.services.spend import SpendService
            collector = SystemCollector(self.store, self.config, self.theme)
            spend = SpendService(self.store, self.config)
            controller = Controller(self.store, self.config, spend)
            started = [collector, spend, controller]
        self._services += started
        for svc in started:
            start = _own_method(svc, "start")
            if start is None:
                continue
            try:
                start()
            except Exception:  # noqa: BLE001 — degrade, never die at startup
                log.exception("failed to start %s", type(svc).__name__)
        self.actions.target = controller
        self._wired = True
        self._start_tray()

    def _start_tray(self) -> None:
        if self._connection is None:
            log.warning("no D-Bus connection; tray not started")
            return
        try:
            from smartpanel.tray.tray import Tray
            kwargs = {"theme": self.theme} if _accepts_kwarg(Tray, "theme") else {}
            self.tray = Tray(self.store, self.actions, self.config, **kwargs)
            self.tray.start(self._connection)
        except Exception:  # noqa: BLE001 — no tray is bad, a dead app is worse
            log.exception("tray failed to start")

    # ───────────────────────── requests / events ─────────────────────────

    def _run_request(self, req: cli.Request, token: str | None = None) -> None:
        if req.verb == "dashboard":
            self.show_dashboard(req.arg)
        elif req.verb == "palette":
            self.show_palette(token)
        elif req.verb == "hud":
            current = bool(self.config.get("hud.enabled")) if self.config else False
            self.actions.set_hud(cli.hud_target(req.arg, current))

    def _on_event(self, event: Event) -> None:
        kind, data = event.kind, event.data or {}
        if kind == "open_dashboard":
            self.show_dashboard(str(data.get("page") or ""))
        elif kind == "open_palette":
            self.show_palette()
        elif kind == "toast":
            self.show_toast(data)
        elif kind in ("confirm_close", "confirm_close_all", "new_session"):
            self._open_session_dialog(kind, data)
        elif kind == "quit":
            self.quit()

    def ensure_window(self):
        if self.window is None:
            from smartpanel.ui.window import MainWindow
            self.window = MainWindow(self, self.store, self.actions, self.config)
        return self.window

    def show_dashboard(self, page: str = "") -> None:
        self.ensure_window().present_page(page)

    def show_palette(self, token: str | None = None) -> None:
        if self.palette is None:
            from smartpanel.ui.palette import PaletteWindow
            self.palette = PaletteWindow(self, self.store, self.actions, self.config)
        if token:
            self.palette.present(token)
        else:
            self.palette.present()

    def window_showing(self) -> bool:
        return self.window is not None and self.window.get_visible()

    def show_toast(self, data: dict) -> None:
        if self.window_showing():
            self.window.toast(data)
            return
        from smartpanel import notify
        from smartpanel.ui import shellmodel
        model = shellmodel.toast_model(data)
        if model is not None:
            # Always "normal": GNOME pins "critical" bubbles until dismissed, and a
            # routine error toast must not become a sticky banner. (The swap
            # alert, which owns critical urgency, goes through collect/system.py.)
            notify.send("smartPanel", model.text, urgency="normal",
                        key="smartpanel-toast")

    def _open_session_dialog(self, kind: str, data: dict) -> None:
        from smartpanel.ui import session_dialogs
        window = self.ensure_window()
        if not window.get_visible():
            window.present_page("sessions")
        try:
            if kind == "confirm_close":
                session_dialogs.confirm_close(window, self.store, self.actions, self.config,
                                              str(data.get("name") or ""))
            elif kind == "confirm_close_all":
                session_dialogs.confirm_close_all(window, self.store, self.actions,
                                                  self.config, data=data)
            else:
                session_dialogs.new_session(window, self.store, self.actions, self.config)
        except Exception:  # noqa: BLE001
            log.exception("%s dialog failed", kind)

    # ───────────────────────── settings ─────────────────────────

    def _on_setting(self, key: str, value) -> None:
        if key == "appearance.style":
            self._apply_style(value)

    @staticmethod
    def _apply_style(style) -> None:
        from smartpanel.ui import tokens
        Adw.StyleManager.get_default().set_color_scheme(tokens.color_scheme_for(style))

    def _on_signal(self) -> bool:
        log.info("signal received; quitting")
        self.quit()
        return GLib.SOURCE_REMOVE
