"""smartpanel command line (SP-0011).

    smartpanel                      start the panel (or, if running, open the dashboard)
    smartpanel --dashboard [PAGE]   open the dashboard on PAGE (sessions:board, journal:<id>)
    smartpanel --palette            open the command palette
    smartpanel --hud on|off|toggle  show / hide the desktop HUD
    smartpanel --demo SCENARIO      run on simulated data (busy, calm, alert, …)
    smartpanel --theme NAME|PATH    metric theme (preset name or .yaml path)
    smartpanel --praise-report      print the gratification report (no GTK, works over SSH)
    smartpanel --snapshot PAGE --out FILE.png [--demo busy]   render a page to PNG
    smartpanel --gallery  (or --demo gallery)                 widget kit gallery

Single instance comes from Gio.Application (`HANDLES_COMMAND_LINE`): a second
invocation forwards its arguments to the running instance and exits with that
instance's status. Arguments are validated *here*, in the invoking process, so a
typo prints usage locally instead of reaching (and never killing) the service.

Everything above `main()` is GTK-free and unit-tested.
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("smartpanel")

SCENARIOS = ("busy", "calm", "alert", "empty", "many", "cold", "first", "notmux", "noswap")
HUD_CHOICES = ("on", "off", "toggle")
SNAPSHOT_TARGETS = ("window", "gallery")   # plus every page / page:detail
GALLERY = "gallery"   # `--demo gallery` = `--gallery` (SP-0009's wording)


class CliError(Exception):
    """Bad arguments. Carries the usage-prefixed message; exit status 2."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # never sys.exit inside the running service
        raise CliError(f"{self.format_usage()}{self.prog}: error: {message}")

    def exit(self, status: int = 0, message: str | None = None):
        raise CliError(message or "")


def _scenarios() -> tuple[str, ...]:
    try:
        from smartpanel.demo import SCENARIOS as live
        return tuple(live)
    except Exception:  # noqa: BLE001 — demo package broken mid-wave: keep the CLI usable
        return SCENARIOS


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="smartpanel", add_help=False,
                description="System metrics, Claude Code spend and session control "
                            "for the GNOME panel.")
    p.add_argument("-h", "--help", action="store_true", help="Show this help and exit")
    verbs = p.add_argument_group("verbs (forwarded to the running instance)")
    verbs.add_argument("--dashboard", nargs="?", const="", default=None, metavar="PAGE",
                       help="Open the dashboard, optionally on PAGE "
                            "(overview, sessions, sessions:board, spend, system, "
                            "journal, journal:<id>, settings)")
    verbs.add_argument("--palette", action="store_true", help="Open the command palette")
    verbs.add_argument("--hud", choices=HUD_CHOICES, help="Show, hide or toggle the desktop HUD")
    start = p.add_argument_group("startup (first instance only)")
    start.add_argument("--demo", choices=(*_scenarios(), GALLERY), metavar="SCENARIO",
                       help="Run on simulated data: " + ", ".join(_scenarios())
                            + f" ('{GALLERY}' opens the widget gallery)")
    start.add_argument("--theme", default=None, metavar="NAME|PATH",
                       help="Theme preset name or path to a .yaml file (default: default)")
    start.add_argument("--debug-timing", action="store_true",
                       help="Log every main-loop callback over 50 ms and print a "
                            "timing report on exit")
    tools = p.add_argument_group("one-shot tools (no running instance needed)")
    tools.add_argument("--praise-report", action="store_true",
                       help="Print the gratification report and exit. Needs no display.")
    tools.add_argument("--snapshot", metavar="PAGE",
                       help="Render PAGE (a page, page:detail, 'window' or 'gallery') "
                            "offscreen to --out and exit")
    tools.add_argument("--out", metavar="FILE.png", help="Output file for --snapshot")
    tools.add_argument("--size", default="900x680", metavar="WxH",
                       help="Window size for --snapshot (default 900x680)")
    tools.add_argument("--style", choices=("light", "dark"),
                       help="Force light or dark for --snapshot / --gallery")
    tools.add_argument("--on-display", action="store_true",
                       help="--snapshot on the current display instead of a private "
                            "headless one")
    tools.add_argument("--gallery", action="store_true",
                       help="Open a window showing every widget of the kit")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--debug", action="store_true")
    return p


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse and cross-validate. Raises CliError; never exits."""
    ns = build_parser().parse_args(argv)
    if ns.help:
        return ns
    if ns.demo == GALLERY:
        ns.demo = None
        ns.gallery = True
    if ns.snapshot is not None and not ns.out:
        raise CliError("smartpanel: error: --snapshot needs --out FILE.png")
    if ns.out and ns.snapshot is None:
        raise CliError("smartpanel: error: --out only goes with --snapshot")
    if ns.snapshot is not None:
        parse_size(ns.size)
    return ns


def parse_size(text: str) -> tuple[int, int]:
    try:
        w, h = (int(x) for x in text.lower().split("x"))
    except ValueError:
        raise CliError(f"smartpanel: error: --size must look like 900x680, not {text!r}")
    if not (200 <= w <= 8000 and 200 <= h <= 8000):
        raise CliError("smartpanel: error: --size out of range (200..8000)")
    return w, h


def log_level(ns: argparse.Namespace) -> int:
    return logging.DEBUG if ns.debug else (logging.INFO if ns.verbose else logging.WARNING)


@dataclass(frozen=True)
class Request:
    """One thing a command line asks the running instance to do."""
    verb: str            # "dashboard" | "palette" | "hud"
    arg: str = ""


def requests_from(ns: argparse.Namespace, *, remote: bool) -> list[Request]:
    """What to do for this invocation, in order.

    A bare `smartpanel` sent to an already-running instance opens the dashboard
    (the usual "launching an app again shows its window"); on first start it
    just starts the service.
    """
    out: list[Request] = []
    if ns.dashboard is not None:
        out.append(Request("dashboard", ns.dashboard))
    if ns.palette:
        out.append(Request("palette"))
    if ns.hud:
        out.append(Request("hud", ns.hud))
    if not out and remote:
        out.append(Request("dashboard", ""))
    return out


def hud_target(arg: str, current: bool) -> bool:
    return {"on": True, "off": False}.get(arg, not current)


def ignored_when_remote(ns: argparse.Namespace) -> list[str]:
    """Startup-only options a second invocation can't apply."""
    out = []
    if ns.demo:
        out.append("--demo")
    if ns.theme:
        out.append("--theme")
    if getattr(ns, "debug_timing", False):
        out.append("--debug-timing")
    return out


def activation_token(platform_data) -> str | None:
    """The launcher's activation token from a forwarded command line's platform
    data (`activation-token` on Wayland, `desktop-startup-id` on X11), so the
    palette can be raised with focus instead of flashing "is ready"."""
    if not isinstance(platform_data, dict):
        return None
    for key in ("activation-token", "desktop-startup-id"):
        value = platform_data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def resolve_theme(value: str | None) -> Path:
    value = value or "default"
    p = Path(value).expanduser()
    if p.exists():
        return p
    preset = Path(__file__).parent / "theme" / "presets" / f"{value}.yaml"
    if preset.exists():
        return preset
    raise CliError(f"smartpanel: theme not found: {value}")


def check_theme(value: str | None) -> Path:
    """Resolve *and parse* the theme, so a malformed file is a usage error
    (exit 2) in the invoking process rather than a crash inside the service."""
    path = resolve_theme(value)
    try:
        from smartpanel.theme.loader import load_theme
        load_theme(path)
    except Exception as e:  # noqa: BLE001 — any parse/shape error is the user's file
        raise CliError(f"smartpanel: bad theme {str(path)!r}: {e}") from e
    return path


# ───────────────────────── entry point ─────────────────────────

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        ns = parse_args(argv)
        if ns.theme:
            check_theme(ns.theme)
    except CliError as e:
        msg = str(e)
        if msg:
            print(msg, file=sys.stderr)
        return 2
    if ns.help:
        build_parser().print_help()
        return 0

    logging.basicConfig(level=log_level(ns), format="%(levelname)s %(name)s: %(message)s")

    if ns.praise_report:
        # Before any GTK import: this path must work over SSH.
        from smartpanel.claude_stats.aggregator import Aggregator
        from smartpanel.claude_stats.report import praise_report
        print(praise_report(Aggregator().refresh()))
        return 0

    if ns.snapshot is not None:
        from smartpanel.ui.snapshot import run_snapshot_cli
        return run_snapshot_cli(ns)

    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk  # noqa: F401
    except (ImportError, ValueError) as e:
        print(f"smartpanel: missing GTK 4 / libadwaita bindings: {e}", file=sys.stderr)
        print("Install: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1",
              file=sys.stderr)
        return 2

    if ns.gallery:
        from smartpanel.ui.gallery import run_gallery
        return run_gallery(ns)

    from smartpanel.app import SmartPanelApp
    app = SmartPanelApp()
    return app.run([sys.argv[0] if sys.argv else "smartpanel", *argv])
