"""Cockpit card (SP-1210, SP-1305, SP-1605, SP-1404, SP-1703).

One card per session::

    ❓  Fix swap alert hysteresis                     [Needs you]
    smartPanel   1h02   $2.90   ● attached
    ┌ Do you want to make this edit to alerts.py? ─────────────┐
    │ ❯ 1. Yes                                                 │
    │   2. Yes, allow all edits this session                   │
    └──────────────────────────────────────────────────────────┘
    [1 · Yes] [2 · Yes] [3 · No] [Open terminal]

`update(session, ctx)` changes only what differs, so the card is kept across
store notifications (focus, hover and the blinking dot survive every tick).
Every button goes through the callbacks in `CardCallbacks`, which the page
binds to the Actions object.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from smartpanel.state.model import PHASE_JOURNALING, PHASE_LAUNCHING, RUNNING, WAITING
from smartpanel.ui.widgets._gi import Adw, GLib, Gtk
from smartpanel.ui.widgets.labels import ActivityGlyph, Pill
from smartpanel.ui.widgets.sessions_model import (WAITING_UNPARSED, display_state, has_prompt,
                                                  journal_steps, meta_parts, option_labels,
                                                  prompt_lines, starting_text)
from smartpanel.ui.widgets.sessions_steps import JournalSteps, set_class, set_text
from smartpanel.uiformat import session_title

ANSWER_LOCK_S = 4        # answer buttons stay disabled this long unless the prompt changes
RETITLE_MIN_MS = 450     # the ⟳ spinner is shown at least this long
RETITLE_MAX_MS = 4000    # …and at most this long if no toast ever comes back


@dataclass
class CardCallbacks:
    focus: Callable[[str], None]
    retitle: Callable[[str], None]
    close: Callable[[str], None]
    answer: Callable[[str, int, str], None]


@dataclass
class CardContext:
    now: float
    terminal: str | None = "terminator"
    monthly_log: str | None = None


def _button(label: str, on_click, *, css: tuple[str, ...] = (), tooltip: str | None = None,
            a11y: str | None = None) -> Gtk.Button:
    b = Gtk.Button(label=label)
    for c in css:
        b.add_css_class(c)
    if tooltip:
        b.set_tooltip_text(tooltip)
    if a11y or tooltip:
        b.update_property([Gtk.AccessibleProperty.LABEL], [a11y or tooltip])
    b.connect("clicked", lambda *_: on_click())
    return b


class CockpitCard(Gtk.Box):
    def __init__(self, name: str, callbacks: CardCallbacks):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=7,
                         accessible_role=Gtk.AccessibleRole.GROUP)
        self.name = name
        self.cb = callbacks
        self.add_css_class("sps-card")
        self.state: str | None = None
        self._title_text = ""

        # ── header ──
        top = Gtk.Box(spacing=10)
        self.glyph = ActivityGlyph()
        self.title = Gtk.Label(xalign=0, hexpand=True, ellipsize=3)  # END
        self.title.add_css_class("sps-card-title")
        self.retitle_spinner = Adw.Spinner(visible=False)
        self.retitle_spinner.set_size_request(14, 14)
        self.retitle_spinner.set_tooltip_text("Re-reading the title from tmux")
        self.retitle_spinner.update_property([Gtk.AccessibleProperty.LABEL],
                                             ["Re-reading the title from tmux"])
        self.pill = Pill()
        for w in (self.glyph, self.title, self.retitle_spinner, self.pill):
            top.append(w)
        self.append(top)

        # ── meta ──
        self.meta = Gtk.Box(spacing=12)
        self.meta.add_css_class("sps-meta")
        self._meta_labels: list[Gtk.Label] = []
        self.append(self.meta)

        # ── waiting: prompt block ──
        self.prompt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.prompt_box.add_css_class("sps-prompt")
        self.question = Gtk.Label(xalign=0, wrap=True, selectable=False)
        self.prompt_box.append(self.question)
        self.options_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.prompt_box.append(self.options_box)
        self.append(self.prompt_box)
        self.waiting_text = Gtk.Label(label=WAITING_UNPARSED, xalign=0, wrap=True)
        self.append(self.waiting_text)

        # ── running: doing line ──
        self.doing_row = Gtk.Box(spacing=6)
        dot = Gtk.Label(label="●", accessible_role=Gtk.AccessibleRole.PRESENTATION)
        dot.add_css_class("sps-blink")
        self.doing = Gtk.Label(xalign=0, ellipsize=3, hexpand=True)
        self.doing.add_css_class("sps-doing")
        self.doing_row.append(dot)
        self.doing_row.append(self.doing)
        self.append(self.doing_row)

        # ── journaling: checklist ──
        self.steps = JournalSteps()
        self.append(self.steps)

        # ── starting: spinner ──
        self.starting_row = Gtk.Box(spacing=8)
        self.starting_row.add_css_class("sps-starting")
        sp = Adw.Spinner()
        sp.set_size_request(12, 12)
        self.starting_label = Gtk.Label(xalign=0)
        self.starting_row.append(sp)
        self.starting_row.append(self.starting_label)
        self.append(self.starting_row)

        # ── buttons ──
        self.answers = Adw.WrapBox(child_spacing=6, line_spacing=6)
        self.answer_buttons: list[Gtk.Button] = []
        self.open_terminal = _button("Open terminal", lambda: self.cb.focus(self.name),
                                     css=("flat",), tooltip="Bring this session's terminal "
                                     "forward to answer there")
        self.answers.append(self.open_terminal)
        self.append(self.answers)
        self._answer_key: tuple | None = None
        self._answer_lock = 0

        self.controls = Gtk.Box(spacing=6)
        self.focus_btn = _button("Focus", lambda: self.cb.focus(self.name),
                                 tooltip="Bring this session's terminal forward")
        self.retitle_btn = _button("⟳ Title", self._on_retitle, css=("flat",),
                                   tooltip="Re-read the title from tmux",
                                   a11y="Re-read the title")
        self.close_btn = _button("Close…", lambda: self.cb.close(self.name), css=("flat",),
                                 tooltip="Close this session (journal first)")
        for b in (self.focus_btn, self.retitle_btn, self.close_btn):
            self.controls.append(b)
        self.append(self.controls)

        self._retitle_timer = 0
        self._retitle_started = 0.0

    # ───────────────────────── update ─────────────────────────

    def update(self, s, ctx: CardContext) -> None:
        state = display_state(s)
        if state != self.state:
            self.state = state
            set_class(self, "sps-st-", state)
        self.glyph.set_state(state)
        self.pill.set_activity(state)
        title = session_title(s)
        if title != self._title_text:
            self._title_text = title
            self.title.set_text(title)
            self.title.set_tooltip_text(title)
        self._set_meta(meta_parts(s, ctx.now))

        waiting = state == WAITING
        parsed = waiting and has_prompt(s)
        self.prompt_box.set_visible(parsed)
        self.waiting_text.set_visible(waiting and not parsed)
        self.answers.set_visible(waiting)
        if parsed:
            self._set_prompt(s)
        elif waiting:
            self._set_answer_buttons(None)

        self.doing_row.set_visible(state == RUNNING)
        if state == RUNNING:
            set_text(self.doing, s.doing or "Working")
            self.doing.set_tooltip_text(s.doing or None)

        self.steps.set_visible(state == PHASE_JOURNALING)
        if state == PHASE_JOURNALING:
            self.steps.set_steps(journal_steps(s, ctx.monthly_log))

        self.starting_row.set_visible(state == PHASE_LAUNCHING)
        if state == PHASE_LAUNCHING:
            set_text(self.starting_label, starting_text(ctx.terminal))

        self.controls.set_visible(state not in (WAITING, PHASE_JOURNALING, PHASE_LAUNCHING))
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"{title}, {self.pill.get_text()}"])

    def _set_meta(self, parts: list[str]) -> None:
        while len(self._meta_labels) < len(parts):
            lab = Gtk.Label(xalign=0)
            self._meta_labels.append(lab)
            self.meta.append(lab)
        for i, lab in enumerate(self._meta_labels):
            if i < len(parts):
                set_text(lab, parts[i])
                lab.set_visible(True)
            else:
                lab.set_visible(False)

    def _set_prompt(self, s) -> None:
        p = s.prompt
        set_text(self.question, p.question)
        lines = prompt_lines(p)
        rows: list[Gtk.Box] = []
        child = self.options_box.get_first_child()
        while child is not None:
            rows.append(child)
            child = child.get_next_sibling()
        while len(rows) < len(lines):
            row = Gtk.Box(spacing=0)
            cur = Gtk.Label(label="❯", width_chars=2, xalign=0,
                            accessible_role=Gtk.AccessibleRole.PRESENTATION)
            cur.add_css_class("sps-cursor")
            row.append(cur)
            row.append(Gtk.Label(xalign=0, wrap=True, hexpand=True))
            self.options_box.append(row)
            rows.append(row)
        for i, row in enumerate(rows):
            if i < len(lines):
                text, on = lines[i]
                cur, lab = row.get_first_child(), row.get_last_child()
                set_text(lab, text)
                cur.set_opacity(1.0 if on else 0.0)
                row.update_property([Gtk.AccessibleProperty.LABEL],
                                    [f"{text}{', selected' if on else ''}"])
                row.set_visible(True)
            else:
                row.set_visible(False)
        self._set_answer_buttons(p)

    def _set_answer_buttons(self, prompt) -> None:
        key = None if prompt is None else (prompt.fingerprint, tuple(prompt.options))
        if key == self._answer_key:
            return
        self._answer_key = key
        self._unlock_answers()
        for b in self.answer_buttons:
            self.answers.remove(b)
        self.answer_buttons = []
        if prompt is None:
            return
        fp = prompt.fingerprint
        for i, label in enumerate(option_labels(prompt)):
            full = prompt.options[i]
            b = _button(label, (lambda i=i: self._on_answer(i, fp)),
                        css=("suggested-action",) if i == 0 else (),
                        tooltip=f"Answer “{full}”", a11y=f"Answer {i + 1}: {full}")
            self.answers.insert_child_after(b, self.answer_buttons[-1]
                                            if self.answer_buttons else None)
            self.answer_buttons.append(b)

    # ───────────────────────── answer / retitle feedback ─────────────────────────

    def _on_answer(self, index: int, fingerprint: str) -> None:
        # One answer per prompt: the keys go into a live session, so a double
        # click must not send "1" twice. Unlocked when the prompt changes.
        for b in self.answer_buttons:
            b.set_sensitive(False)
        if self._answer_lock:
            GLib.source_remove(self._answer_lock)
        self._answer_lock = GLib.timeout_add_seconds(ANSWER_LOCK_S, self._lock_expired)
        self.cb.answer(self.name, index, fingerprint)

    def _lock_expired(self) -> bool:
        self._answer_lock = 0
        self._unlock_answers()
        return GLib.SOURCE_REMOVE

    def _unlock_answers(self) -> None:
        if self._answer_lock:
            GLib.source_remove(self._answer_lock)
            self._answer_lock = 0
        for b in self.answer_buttons:
            b.set_sensitive(True)

    @property
    def answers_locked(self) -> bool:
        return any(not b.get_sensitive() for b in self.answer_buttons)

    def _on_retitle(self) -> None:
        self.set_retitling(True)
        self.cb.retitle(self.name)

    def set_retitling(self, on: bool) -> None:
        """⟳ feedback (SP-1404): a spinner next to the title until the controller's
        toast arrives (`retitle_done`), shown at least RETITLE_MIN_MS."""
        if on:
            self._retitle_started = GLib.get_monotonic_time() / 1000
            self.retitle_spinner.set_visible(True)
            self.retitle_btn.set_sensitive(False)
            self._clear_retitle_timer()
            self._retitle_timer = GLib.timeout_add(RETITLE_MAX_MS, self._retitle_off)
        else:
            self._retitle_off()

    def retitle_done(self) -> None:
        if not self.retitle_spinner.get_visible():
            return
        shown = GLib.get_monotonic_time() / 1000 - self._retitle_started
        if shown >= RETITLE_MIN_MS:
            self._retitle_off()
        else:
            self._clear_retitle_timer()
            self._retitle_timer = GLib.timeout_add(int(RETITLE_MIN_MS - shown),
                                                   self._retitle_off)

    @property
    def retitling(self) -> bool:
        return self.retitle_spinner.get_visible()

    def _clear_retitle_timer(self) -> None:
        if self._retitle_timer:
            GLib.source_remove(self._retitle_timer)
            self._retitle_timer = 0

    def _retitle_off(self) -> bool:
        self._retitle_timer = 0
        self.retitle_spinner.set_visible(False)
        self.retitle_btn.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def dispose_timers(self) -> None:
        self._clear_retitle_timer()
        if self._answer_lock:
            GLib.source_remove(self._answer_lock)
            self._answer_lock = 0
