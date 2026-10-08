from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

from smartpanel.theme.loader import load_theme

log = logging.getLogger("smartpanel")


def _resolve_theme(value: str) -> Path:
    p = Path(value)
    if p.exists():
        return p
    preset = Path(__file__).parent / "theme" / "presets" / f"{value}.yaml"
    if preset.exists():
        return preset
    raise SystemExit(f"smartpanel: theme not found: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smartpanel", description=__doc__)
    parser.add_argument("--theme", default="default",
                        help="Theme preset name or path to a .yaml file")
    parser.add_argument("--praise-report", action="store_true",
                        help="Print the gratification report (what compliments cost) "
                             "and exit. Needs no display server.")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    level = logging.DEBUG if args.debug else (logging.INFO if args.verbose else logging.WARNING)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")

    if args.praise_report:
        # Deliberately before the theme + GTK work: this path is usable over SSH.
        from smartpanel.claude_stats.aggregator import Aggregator
        from smartpanel.claude_stats.report import praise_report
        print(praise_report(Aggregator().refresh()))
        return 0

    theme_path = _resolve_theme(args.theme)
    theme = load_theme(theme_path)
    log.info("loaded theme %s (%d metrics)", theme_path.name, len(theme.metrics))

    # Import GTK only here so that --help and tests don't require a display.
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import Gtk, GLib  # noqa: F401
    except (ImportError, ValueError) as e:
        print(f"smartpanel: missing GTK / AppIndicator bindings: {e}", file=sys.stderr)
        print("Install: sudo apt install python3-gi gir1.2-gtk-3.0 "
              "gir1.2-ayatanaappindicator3-0.1", file=sys.stderr)
        return 2

    from smartpanel.daemon import Daemon
    from smartpanel.indicator import Indicator
    from smartpanel.sensors import sessions as sessions_sensor
    from smartpanel.sessions import activity, focus, tmux
    from smartpanel.uiformat import session_heading, sessions_status

    indicator = Indicator(theme)
    daemon = Daemon(theme, indicator)
    daemon.start()

    # Interactive Sessions submenu: refresh on its own timer, independent of the
    # theme. tmux list/kill are fast local calls, fine on the main loop here;
    # the slow path (journal generation) lands in a later slice and must go
    # off-loop. Kills bust the sensor cache so the top-bar count updates too.
    # Terminal title last pushed per session, so a tick only talks to tmux when
    # a heading actually changed. Recorded even if the push failed, so a broken
    # session logs once instead of every 2s; its ⟳ button retries.
    pushed_titles: dict[str, str] = {}

    def sync_terminal_titles(live) -> None:
        alive = {s.name for s in live}
        for name in [n for n in pushed_titles if n not in alive]:
            del pushed_titles[name]
        for s in live:
            heading = session_heading(s)
            if pushed_titles.get(s.name) != heading:
                tmux.set_terminal_title(s.name, heading)
                pushed_titles[s.name] = heading

    def refresh_sessions() -> bool:
        # `annotate` costs one capture-pane per session (~2.4ms) on top of the
        # single list-sessions call — a handful of ms per tick, inside the
        # main-loop budget, and the only way to see a session that is blocked on
        # a permission prompt (see sessions/activity.py).
        live = activity.annotate(tmux.list_sessions())
        sync_terminal_titles(live)
        indicator.update_sessions(live, on_close=close_one, on_close_all=close_all,
                                  on_refresh=refresh_one, on_focus=focus_one)
        # Feed the top bar too, so `{SESSIONS.COUNT}` works without the category
        # being a theme metric. update() iterates this category's metrics (none)
        # then refreshes the top-bar label from self.values.
        names = ", ".join(s.project for s in live) if live else "—"
        tally = activity.counts(live)
        indicator.update("SESSIONS", {
            "COUNT": float(len(live)),
            "NAMES": names,
            "STATUS": sessions_status(live),
            "WAITING": float(tally[activity.WAITING]),
            "RUNNING": float(tally[activity.RUNNING]),
        })
        return True

    def focus_one(name: str) -> None:
        """Clicking a session row: bring its terminal forward.

        The title is the handle the window managers match on, and it is exactly
        what `sync_terminal_titles` pushed, so the two can never drift.
        """
        match = next((s for s in tmux.list_sessions() if s.name == name), None)
        title = session_heading(match) if match else name
        result = focus.focus_session(name, title)
        if result == focus.REATTACHED:
            # The old window is gone with its client; pick the new title up on
            # the next tick rather than leaving a stale entry behind.
            pushed_titles.pop(name, None)
        elif result == focus.FAILED:
            log.warning("no way to focus %s on this desktop; see focus.py", name)

    def refresh_one(name: str) -> None:
        """⟳ button: re-read the session from tmux and re-push its title."""
        pushed_titles.pop(name, None)
        refresh_sessions()

    def close_one(name: str) -> None:
        tmux.kill_session(name)
        sessions_sensor.invalidate()
        refresh_sessions()

    def close_all() -> None:
        n = tmux.kill_all()
        log.info("closed %d session(s)", n)
        sessions_sensor.invalidate()
        refresh_sessions()

    refresh_sessions()
    sessions_timer = GLib.timeout_add_seconds(2, refresh_sessions)

    signal.signal(signal.SIGINT, lambda *_: Gtk.main_quit())
    signal.signal(signal.SIGTERM, lambda *_: Gtk.main_quit())

    try:
        Gtk.main()
    finally:
        GLib.source_remove(sessions_timer)
        daemon.stop()
    return 0
