"""Render the dashboard (or the widget gallery) to a PNG, for visual QA.

    smartpanel --demo busy --snapshot overview --out /tmp/overview.png
    smartpanel --demo alert --snapshot sessions:board --out board.png --style light
    smartpanel --snapshot gallery --out gallery.png

By default the window is drawn on a private, headless **Broadway** display
(`gtk4-broadwayd`, bound to 127.0.0.1, killed afterwards), so nothing flashes on
the user's screen and it works over SSH. `--on-display` uses the current display
instead. The capture itself is `Gtk.WidgetPaintable` → `Gtk.Snapshot` → the
window's own `Gsk.Renderer.render_texture` → PNG.

Broadway limitation: its frame clock stalls without a browser attached, so a
transition that starts after the first frames never finishes there. That
includes Adw dialogs, which stay invisible; snapshot those with `--on-display`.
Animations are switched off in both modes so captures are deterministic.

Never touches the running instance: a separate, non-unique application id, no
D-Bus export, no collectors, no window-state persistence.
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

SNAPSHOT_APP_ID = "dev.omdev.SmartPanel.Snapshot"
SETTLE_MS = 400          # after the first frame: let lazy pages / fonts settle
WATCHDOG_S = 20
RETRY_MS = 100
RETRIES = 50


# ───────────────────────── headless display ─────────────────────────

def _die_with_parent() -> None:  # pragma: no cover - runs in the child
    """Linux: SIGTERM the broadway daemon if we die first (e.g. under `timeout`)."""
    try:
        import ctypes
        import signal
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except Exception:  # noqa: BLE001
        pass


def socket_path(runtime: Path, display: int) -> Path:
    """gtk4-broadwayd :N listens on broadway<N+1>.socket (verified on GTK 4.22)."""
    return runtime / f"broadway{display + 1}.socket"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class HeadlessDisplay:
    """A private gtk4-broadwayd. Use as a context manager; sets GDK_BACKEND."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.display: str | None = None
        self._saved: dict[str, str | None] = {}
        self._socket: Path | None = None

    @staticmethod
    def available() -> bool:
        return shutil.which("gtk4-broadwayd") is not None

    def __enter__(self) -> "HeadlessDisplay":
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
        for n in range(60, 100):
            sock = socket_path(runtime, n)
            if sock.exists() or socket_path(runtime, n - 1).exists():
                continue
            proc = subprocess.Popen(
                ["gtk4-broadwayd", f":{n}", "--address", "127.0.0.1",
                 "--port", str(_free_port())],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                preexec_fn=_die_with_parent)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and proc.poll() is None and not sock.exists():
                time.sleep(0.02)
            if proc.poll() is None and sock.exists():
                self.proc, self.display, self._socket = proc, f":{n}", sock
                break
            proc.kill()
            proc.wait()
            sock.unlink(missing_ok=True)
        if self.proc is None:
            raise RuntimeError("could not start gtk4-broadwayd")
        for key, value in (("GDK_BACKEND", "broadway"), ("BROADWAY_DISPLAY", self.display)):
            self._saved[key] = os.environ.get(key)
            os.environ[key] = value
        return self

    def __exit__(self, *_exc) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
            self.proc = None
        if self._socket is not None:
            self._socket.unlink(missing_ok=True)  # the daemon leaves it behind
            self._socket = None


# ───────────────────────── capture ─────────────────────────

def capture(widget, path: str | Path) -> bool:
    """Draw a mapped widget into a PNG. Returns False if nothing was drawn."""
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Graphene", "1.0")
    from gi.repository import Graphene, Gtk

    w, h = widget.get_width(), widget.get_height()
    if w <= 0 or h <= 0:
        return False
    paintable = Gtk.WidgetPaintable.new(widget)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        return False
    renderer = widget.get_native().get_renderer()
    texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, w, h))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return bool(texture.save_to_png(str(path)))


def disable_animations() -> None:
    """Jump every transition to its end state. Needed on Broadway, whose frame
    clock stalls without a browser attached (dialogs would stay invisible), and
    it makes snapshots deterministic anyway."""
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    settings = Gtk.Settings.get_default()
    if settings is not None:
        settings.set_property("gtk-enable-animations", False)


class _InertActions:
    """Actions for a snapshot without --demo: every verb does nothing."""

    def __getattr__(self, name):
        return lambda *_a, **_k: [] if name == "list_start_folders" else None


def snapshot(target: str, out: str | Path, *, state=None, demo: str | None = None,
             size: tuple[int, int] = (900, 680), style: str | None = None) -> int:
    """Render `target` ('window', 'gallery', a page or 'page:detail') to `out`."""
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib

    from smartpanel import __version__
    from smartpanel.config import Config
    from smartpanel.state import PanelState, Store
    from smartpanel.ui import tokens

    if state is None and demo:
        from smartpanel import demo as demo_mod
        state = demo_mod.build_state(demo)
        state.app.demo = demo
    state = state or PanelState()
    if not state.app.version:
        state.app.version = __version__
    store = Store(state)
    config = Config()
    if demo:
        from smartpanel.demo import DemoController
        actions = DemoController(store, config)
    else:
        actions = _InertActions()

    app = Adw.Application(application_id=SNAPSHOT_APP_ID,
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    result = {"rc": 1}

    def finish(win, ok: bool):
        if result.get("done"):
            return
        result["done"] = True
        result["rc"] = 0 if ok else 1
        win.destroy()
        app.quit()

    def activate(a):
        disable_animations()
        tokens.install()
        Adw.StyleManager.get_default().set_color_scheme(tokens.color_scheme_for(style))
        if target == "gallery":
            from smartpanel.ui.gallery import build_gallery
            win = Adw.ApplicationWindow(application=a, title="gallery")
            win.set_content(build_gallery())
        else:
            from smartpanel.ui.window import MainWindow
            win = MainWindow(a, store, actions, config, state_path=None)
            win.show_page("overview" if target == "window" else target)
        win.set_default_size(*size)
        tries = {"n": 0}

        def shoot():
            if result.get("done"):
                return GLib.SOURCE_REMOVE
            try:
                ok = capture(win, out)
            except Exception:  # noqa: BLE001
                log.exception("snapshot failed")
                finish(win, False)
                return GLib.SOURCE_REMOVE
            if ok:
                finish(win, True)
                return GLib.SOURCE_REMOVE
            tries["n"] += 1          # first frames can still be empty: retry briefly
            if tries["n"] >= RETRIES:
                log.error("snapshot: nothing was drawn")
                finish(win, False)
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        def after_map(*_):
            GLib.timeout_add(SETTLE_MS, lambda: (
                GLib.timeout_add(RETRY_MS, shoot), GLib.SOURCE_REMOVE)[1])

        win.connect("map", after_map)
        win.present()

        def watchdog():
            if not result.get("done"):
                log.error("snapshot: timed out")
                finish(win, False)
            return GLib.SOURCE_REMOVE
        GLib.timeout_add_seconds(WATCHDOG_S, watchdog)

    app.connect("activate", activate)
    app.run([])
    return result["rc"]


def run_snapshot_cli(ns) -> int:
    """`--snapshot` entry from cli.main (args already validated)."""
    from smartpanel.cli import parse_size
    size = parse_size(ns.size)
    if ns.on_display or not HeadlessDisplay.available():
        if not ns.on_display:
            log.warning("gtk4-broadwayd not found; snapshotting on the current display")
        rc = snapshot(ns.snapshot, ns.out, demo=ns.demo, size=size, style=ns.style)
    else:
        with HeadlessDisplay():
            rc = snapshot(ns.snapshot, ns.out, demo=ns.demo, size=size, style=ns.style)
    if rc == 0:
        print(ns.out)
    return rc
