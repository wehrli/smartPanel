"""Centered dashboard window — the full read-only view of everything smartPanel
tracks, shown on demand from the tray icon.

The GNOME panel renders the AppIndicator's dropdown menu itself, and a long menu
can overflow the screen. This window is the roomy alternative: a scrollable,
center-screen surface that holds every metric plus the live Claude session list.
It is opened from a tray menu item (and middle-click) and hidden — not
destroyed — on close, so it reopens instantly with current data.

Updates are pushed from `Indicator.update()` / `update_sessions()`, so the window
shows the same values as the menu without polling anything itself.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Callable

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from smartpanel.alerts import Level
from smartpanel.render import format_value, render_bar
from smartpanel.sessions import journal, tmux
from smartpanel.sessions.launch import DEFAULT_TERMINAL, default_dir, launch_session
from smartpanel.theme.model import MetricRender, Theme
from smartpanel.uiformat import (
    MULTI_ROW_METRICS,
    SECTION_HEADERS,
    activity_style,
    session_label,
)

log = logging.getLogger(__name__)

_NAME_WIDTH = 16  # chars reserved for the metric name column

# Sessions younger than this aren't worth journaling — close them straight away
# without the prompt. Override with SMARTPANEL_JOURNAL_MIN_AGE (seconds).
JOURNAL_MIN_AGE_S = int(os.environ.get("SMARTPANEL_JOURNAL_MIN_AGE", "120"))
# Seconds to wait for a journal answer before auto-generating.
JOURNAL_CONFIRM_TIMEOUT_S = 5


class Dashboard:
    """A reusable center-screen window mirroring the tray data."""

    def __init__(self, theme: Theme):
        self.theme = theme
        self.values: dict[str, dict[str, float | str | None]] = {}

        self.window = Gtk.Window(title="smartPanel")
        self.window.set_default_size(440, 560)
        self.window.set_position(Gtk.WindowPosition.CENTER_ALWAYS)
        self.window.set_keep_above(True)
        # Hide instead of destroy so the same window reopens with live data.
        self.window.connect("delete-event", self._on_close)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.window.add(scroller)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.box.set_margin_top(12)
        self.box.set_margin_bottom(12)
        self.box.set_margin_start(14)
        self.box.set_margin_end(14)
        scroller.add(self.box)

        self._value_labels: dict[tuple[str, str], Gtk.Label] = {}
        self._alert_banner: Gtk.Box | None = None
        self._alert_label: Gtk.Label | None = None
        self._alert_sig: tuple | None = None
        self._sessions_box: Gtk.Box | None = None
        self._on_close: Callable[[str], None] | None = None
        self._on_refresh: Callable[[str], None] | None = None
        self._on_close_all: Callable[[], None] | None = None
        self._on_focus: Callable[[str], None] | None = None
        self._last_sessions_sig: tuple | None = None
        self._current_sessions: list = []
        # Sessions whose journal is running: they stay listed (and un-closable)
        # until the skill answers, because the close now waits on it.
        self._journaling: set[str] = set()

        self._build_structure()

    # ---- structure (built once) ----

    def _build_structure(self) -> None:
        # Alert banner above everything: when swap is nearly full it is the most
        # important thing on screen, and it must not be something you scroll to.
        # Built once and hidden; `set_alert` only toggles visibility and text,
        # so it never disturbs the layout below it.
        self._alert_banner, self._alert_label = _build_banner()
        self.box.pack_start(self._alert_banner, False, False, 4)

        # Sessions section FIRST so it's visible (and closable) the instant the
        # window opens from the tray — no scrolling past metrics to reach it.
        self._add_header(SECTION_HEADERS["SESSIONS"], first=True)
        new_btn = Gtk.Button(label="➕  New session")
        new_btn.connect("clicked", self.new_session)
        self.box.pack_start(new_btn, False, False, 2)
        self._sessions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.box.pack_start(self._sessions_box, False, False, 0)
        self._render_sessions([])

        # Then the metric sections.
        by_cat = self.theme.metrics_by_category()
        for category in self.theme.categories_in_order():
            metrics = by_cat.get(category) or []
            if not metrics:
                continue
            self._add_header(SECTION_HEADERS.get(category, category.title()), first=False)
            for m in metrics:
                self._add_metric_row(m)

        # Footer: quit.
        self.box.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 8
        )
        quit_btn = Gtk.Button(label="Quit smartPanel")
        quit_btn.connect("clicked", lambda *_: Gtk.main_quit())
        self.box.pack_start(quit_btn, False, False, 0)

    def _add_header(self, text: str, first: bool) -> None:
        if not first:
            self.box.pack_start(
                Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 6
            )
        header = Gtk.Label()
        header.set_markup(f"<b>{GLib.markup_escape_text(text)}</b>")
        header.set_xalign(0.0)
        self.box.pack_start(header, False, False, 2)

    def _add_metric_row(self, m: MetricRender) -> None:
        if m.metric in MULTI_ROW_METRICS:
            # Multi-line block rendered into a single label.
            label = _left_label("—")
            self.box.pack_start(label, False, False, 0)
            self._value_labels[(m.category, m.metric)] = label
            return
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = _left_label(m.label or m.key)
        name.set_width_chars(_NAME_WIDTH)
        value = _left_label("…")
        row.pack_start(name, False, False, 0)
        row.pack_start(value, False, False, 0)
        self.box.pack_start(row, False, False, 0)
        self._value_labels[(m.category, m.metric)] = value

    # ---- updates (labels only) ----

    def update(self, category: str, values: dict[str, float | str | None]) -> None:
        self.values[category] = values
        for m in self.theme.metrics:
            if m.category != category:
                continue
            label = self._value_labels.get((m.category, m.metric))
            if label is None:
                continue
            label.set_text(_metric_text(m, values.get(m.metric)))

    def set_alert(self, level: Level, headline: str, detail: str) -> None:
        """Show/hide the banner. Called from `Indicator._check_swap` each tick,
        so it dedupes — repainting markup 6x a minute would fight text selection
        and flicker the window while it's open."""
        banner, label = self._alert_banner, self._alert_label
        if banner is None or label is None:
            return
        sig = (level, headline, detail)
        if sig == self._alert_sig:
            return
        self._alert_sig = sig

        if level is Level.OK:
            banner.hide()
            banner.set_no_show_all(True)  # survive the next window show_all()
            return
        color = "#c0392b" if level is Level.CRITICAL else "#b9770e"
        label.set_markup(
            f"<span foreground='{color}'><b>{GLib.markup_escape_text(headline)}</b>\n"
            f"{GLib.markup_escape_text(detail)}</span>"
        )
        banner.set_no_show_all(False)
        banner.show_all()

    def update_sessions(
        self,
        sessions,
        on_close: Callable[[str], None] | None = None,
        on_close_all: Callable[[], None] | None = None,
        on_refresh: Callable[[str], None] | None = None,
        on_focus: Callable[[str], None] | None = None,
    ) -> None:
        if on_close is not None:
            self._on_close = on_close
        if on_close_all is not None:
            self._on_close_all = on_close_all
        if on_refresh is not None:
            self._on_refresh = on_refresh
        if on_focus is not None:
            self._on_focus = on_focus
        sessions = list(sessions)
        # Skip needless rebuilds (see Indicator.update_sessions for rationale).
        # The journaling set is part of the signature: a session entering or
        # leaving that state has to repaint even though the list is unchanged.
        sig = (tuple((s.name, bool(getattr(s, "attached", False)),
                      getattr(s, "title", ""), getattr(s, "activity", ""))
                     for s in sessions),
               frozenset(self._journaling))
        if sig == self._last_sessions_sig:
            return
        self._last_sessions_sig = sig
        self._current_sessions = sessions
        self._render_sessions(sessions)

    def _render_sessions(self, sessions) -> None:
        box = self._sessions_box
        if box is None:
            return
        for child in box.get_children():
            box.remove(child)

        if not sessions:
            box.pack_start(_left_label("No active sessions"), False, False, 0)
            box.show_all()
            return

        for s in sessions:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            pending = s.name in self._journaling
            close = Gtk.Button(label="⏳" if pending else "✕")
            close.set_sensitive(not pending)
            if not pending:
                close.connect("clicked", self._make_close_handler(s))
            row.pack_start(close, False, False, 0)
            refresh = Gtk.Button(label="⟳")
            refresh.set_tooltip_text("Refresh the name here and in the terminal title")
            refresh.connect("clicked", self._make_refresh_handler(s))
            row.pack_start(refresh, False, False, 0)
            text = session_label(s)
            if pending:
                text = f"{text} — journaling…"
            row.pack_start(self._session_button(s, text), True, True, 0)
            box.pack_start(row, False, False, 0)

        close_all = Gtk.Button(label=f"✕  Close all ({len(sessions)})")
        close_all.set_sensitive(not self._journaling)
        close_all.connect("clicked", lambda *_: self._confirm_then_close_all())
        box.pack_start(close_all, False, False, 4)
        box.show_all()

    def _session_button(self, session, text: str) -> "Gtk.Button":
        """The row's label, as a flat button that brings its terminal forward.

        A button rather than a label because clicking the session is how you get
        to it — the whole row text is the target, so there is nothing small to
        aim at. `relief=NONE` keeps it reading as a row, not a control. The
        activity glyph is coloured here rather than in `session_label`, which
        stays GTK-free markup-free so it can also be a tmux window title.
        """
        glyph, colour, tooltip = activity_style(getattr(session, "activity", None))
        label = _left_label("")
        head, _, rest = text.partition(" ")
        label.set_markup(
            f"<span foreground='{colour}'>{GLib.markup_escape_text(head)}</span> "
            f"{GLib.markup_escape_text(rest)}"
        )
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.add(label)
        button.set_tooltip_text(f"{tooltip} · click to open this session's terminal")
        button.connect("clicked", self._make_focus_handler(session))
        return button

    def _make_focus_handler(self, session):
        return lambda *_: self._focus_session(getattr(session, "name", ""))

    def _focus_session(self, name: str) -> None:
        if self._on_focus is None or not name:
            return
        self._on_focus(name)

    def _make_close_handler(self, session):
        return lambda *_: self._confirm_then_close(session)

    def _make_refresh_handler(self, session):
        return lambda *_: self._refresh_session(session.name)

    def _refresh_session(self, name: str) -> None:
        if self._on_refresh is None:
            return
        self._last_sessions_sig = None  # repaint even if nothing changed
        self._on_refresh(name)

    # ---- close + journal flow ----

    def _confirm_then_close(self, session) -> None:
        """Close one session, offering a journal log if it ran long enough."""
        name = getattr(session, "name", None)
        if name is None or name in self._journaling:
            return
        created = getattr(session, "created", 0) or 0
        age = time.time() - created if created else 0
        if not journal.SKILL_COMMAND or not created or age < JOURNAL_MIN_AGE_S:
            self._do_close(name)  # journaling off, or too short — close now
            return

        cwd = tmux.session_cwd(name)  # capture while the session is still alive
        project = getattr(session, "project", name)
        if not self._ask_journal(project):
            self._do_close(name)
            return
        self._start_journal(name, project, cwd, age)

    def _confirm_then_close_all(self) -> None:
        sessions = list(self._current_sessions)
        if not sessions or self._journaling:
            return
        worth = [s for s in sessions
                 if journal.SKILL_COMMAND
                 and (getattr(s, "created", 0) or 0)
                 and (time.time() - s.created) >= JOURNAL_MIN_AGE_S]
        cwds = {s.name: tmux.session_cwd(s.name) for s in worth}  # while alive
        make_journal = self._ask_journal(f"{len(worth)} session(s)") if worth else False
        if not make_journal:
            if self._on_close_all is not None:
                self._on_close_all()
            return
        # Journaled sessions each close themselves once their entry is written;
        # `_SKILL_LOCK` in journal.py serializes the runs, so they queue rather
        # than clobbering the shared monthly log. Everything else goes now.
        keep = {s.name for s in worth}
        for s in sessions:
            if s.name not in keep:
                self._do_close(s.name)
        for s in worth:
            self._start_journal(s.name, getattr(s, "project", s.name),
                                cwds.get(s.name), time.time() - s.created)

    def _start_journal(self, name: str, project: str, cwd, age: float) -> None:
        """Journal the live session, then close it — off the GLib loop.

        The command is typed into the running session, so the kill has to wait
        for the skill to answer. Callbacks come back from the worker thread and
        are bounced onto the main loop with `idle_add`.
        """
        self._journaling.add(name)
        self._render_sessions(self._current_sessions)
        journal.write_journal_async(
            project, cwd, duration_s=age, session_name=name,
            close_session=lambda: GLib.idle_add(self._do_close, name),
            on_done=lambda path: GLib.idle_add(self._journal_done, name, path),
        )

    def _journal_done(self, name: str, path) -> bool:
        self._journaling.discard(name)
        log.info("journal for %s: %s", name, path or "failed")
        self._render_sessions(self._current_sessions)
        return False  # one-shot idle callback

    def _do_close(self, name: str) -> None:
        if self._on_close is not None:
            self._on_close(name)

    def _ask_journal(self, label: str) -> bool:
        """Yes/No dialog; auto-answers Yes after JOURNAL_CONFIRM_TIMEOUT_S."""
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Run {journal.SKILL_COMMAND} for {label}?",
        )
        dialog.set_default_response(Gtk.ResponseType.YES)
        remaining = {"s": JOURNAL_CONFIRM_TIMEOUT_S}
        state = {"src": None, "fired": False}
        dialog.format_secondary_text(f"Auto-generating in {remaining['s']}s…")

        def tick() -> bool:
            remaining["s"] -= 1
            if remaining["s"] <= 0:
                state["fired"] = True
                dialog.response(Gtk.ResponseType.YES)
                return False
            dialog.format_secondary_text(f"Auto-generating in {remaining['s']}s…")
            return True

        state["src"] = GLib.timeout_add_seconds(1, tick)
        response = dialog.run()
        if not state["fired"] and state["src"] is not None:
            GLib.source_remove(state["src"])
        dialog.destroy()
        return response == Gtk.ResponseType.YES

    # ---- visibility ----

    def present_centered(self) -> None:
        self.window.set_position(Gtk.WindowPosition.CENTER_ALWAYS)
        self.window.show_all()
        self.window.present()

    def _on_close(self, *_args) -> bool:
        self.window.hide()
        return True  # stop the default destroy

    # ---- new session ----

    def new_session(self, *_args) -> None:
        """Pick a folder, then open a terminal running a new Claude session."""
        dialog = Gtk.FileChooserDialog(
            title="New Claude session — choose a project folder",
            parent=self.window,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Open session", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        try:
            dialog.set_current_folder(str(default_dir()))
        except Exception:  # noqa: BLE001 — non-fatal cosmetic default
            pass

        response = dialog.run()
        folder = dialog.get_filename()
        dialog.destroy()

        if response == Gtk.ResponseType.ACCEPT and folder:
            if not launch_session(folder):
                self._error(
                    f"Couldn't open a session.\n\n"
                    f"Make sure '{DEFAULT_TERMINAL}' and tmux are installed:\n"
                    f"    sudo apt install {DEFAULT_TERMINAL} tmux"
                )

    def _error(self, message: str) -> None:
        dlg = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="smartPanel",
        )
        dlg.format_secondary_text(message)
        dlg.run()
        dlg.destroy()


# ---- pure helpers ----

def _metric_text(m: MetricRender, raw) -> str:
    if m.metric in MULTI_ROW_METRICS:
        if not isinstance(raw, str) or raw in ("—", ""):
            return "—"
        return "\n".join("  " + e for e in raw.split(" · "))
    if raw is None:
        return "—"
    text = format_value(m.key, raw, m.show_unit)
    if m.show_graph and isinstance(raw, (int, float)):
        text = f"{text}  {render_bar(float(raw), m.min_value, m.max_value)}"
    return text


def _build_banner() -> tuple["Gtk.Box", "Gtk.Label"]:
    """The alert strip: a framed, wrapping label, hidden until an alert fires."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    frame = Gtk.Frame()
    label = _left_label("")
    label.set_line_wrap(True)
    label.set_margin_top(6)
    label.set_margin_bottom(6)
    label.set_margin_start(8)
    label.set_margin_end(8)
    frame.add(label)
    box.pack_start(frame, False, False, 0)
    box.set_no_show_all(True)  # stays hidden through the window's show_all()
    return box, label


def _left_label(text: str) -> "Gtk.Label":
    label = Gtk.Label(label=text)
    label.set_xalign(0.0)
    label.set_selectable(False)
    return label
