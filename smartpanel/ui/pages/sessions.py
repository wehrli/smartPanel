"""Dashboard page 'sessions' (B2 Cockpit, B3 Board): SP-1210, SP-1211, SP-1214,
SP-1305/1306/1307, SP-1404, SP-1504, SP-1605, SP-1607, SP-1703, SP-2606, SP-2607.

Layout (prototype `sessionsHTML`)::

    [Cockpit|Board]                       Close all…   [＋ New session]
    ┌ one of ──────────────────────────────────────────────────────┐
    │ cockpit: a card per session, display order                   │
    │ board:   Needs you | Running | Idle  +  journal & close zone │
    │ error:   tmux isn’t installed · sudo apt install tmux [Copy]  │
    │ empty:   No Claude sessions running · ＋ New session          │
    └──────────────────────────────────────────────────────────────┘

The page subscribes once and updates in place: cards are kept per session name
and only re-ordered / re-labelled, so scroll position, keyboard focus and the
blinking "running" dot survive every tick. Ages tick on a slow timer while the
page is mapped (the session refresh dedupes unchanged lists, so the store alone
would leave "4m waiting" frozen). Every button calls the Actions object.

API: build(store, actions, config) -> Gtk.Widget; show_detail(widget, 'board'|'cockpit').
"""
from __future__ import annotations

import logging
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from smartpanel.ui.widgets.sessions_board import BoardView  # noqa: E402
from smartpanel.ui.widgets.sessions_card import (CardCallbacks, CardContext,  # noqa: E402
                                                 CockpitCard)
from smartpanel.ui.widgets import sessions_css  # noqa: E402
from smartpanel.ui.widgets.sessions_model import (BOARD, COCKPIT, EMPTY_MARKUP,  # noqa: E402
                                                  EMPTY_TITLE, ERROR_TEXT, ERROR_TITLE,
                                                  TMUX_INSTALL, VIEWS, busy,
                                                  close_all_progress)
from smartpanel.ui.widgets.sessions_steps import set_text, sync_children  # noqa: E402

log = logging.getLogger(__name__)

SCROLLS = True          # the page owns its scrollers (board columns scroll on their own)
AGE_TICK_S = 15
RETITLE_TOAST = "Title re-read from tmux"


def _state_icon(glyph: str) -> Gtk.Widget:
    box = Gtk.Box(halign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.PRESENTATION)
    box.add_css_class("sp-empty-icon")
    label = Gtk.Label(label=glyph, hexpand=True, vexpand=True)
    box.append(label)
    return box


class SessionsPage(Gtk.Box):
    def __init__(self, store, actions, config):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True, hexpand=True)
        sessions_css.install()
        self.store, self.actions, self.config = store, actions, config
        self.add_css_class("sps-page")
        self.view = COCKPIT
        self.cards: dict[str, CockpitCard] = {}
        self._mode: str | None = None
        self._age_timer = 0

        self._build_toolbar()
        self.stack = Gtk.Stack(vexpand=True, hexpand=True, hhomogeneous=False,
                               transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=100)
        self.append(self.stack)

        # cockpit
        self.cockpit = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9,
                               valign=Gtk.Align.START)
        self.cockpit.update_property([Gtk.AccessibleProperty.LABEL], ["Sessions"])
        self.cockpit_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                   vexpand=True)
        self.cockpit_scroller.set_child(self.cockpit)
        self.stack.add_named(self.cockpit_scroller, COCKPIT)

        # board
        self.board = BoardView(on_drop=self._close, on_focus=self._focus)
        # Three columns don't fit the window's minimum width: scroll sideways
        # there rather than force the window wider.
        board_scroller = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER,
                                            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                            vexpand=True)
        board_scroller.set_child(self.board)
        self.stack.add_named(board_scroller, BOARD)

        self.stack.add_named(self._wrap(self._build_error()), "error")
        self.stack.add_named(self._wrap(self._build_empty()), "empty")

        self._unsub = store.subscribe(self._on_store)
        self._unsub_events = store.on_event(self._on_event)
        self.connect("map", lambda *_: self._start_ticker())
        self.connect("unmap", lambda *_: self._stop_ticker())
        self.connect("destroy", lambda *_: self._teardown())
        self.refresh()

    # ───────────────────────── construction ─────────────────────────

    def _build_toolbar(self) -> None:
        # A WrapBox, so at the window's minimum width the buttons drop under the
        # view switch instead of forcing the window wider.
        bar = Adw.WrapBox(child_spacing=8, line_spacing=8, justify=Adw.JustifyMode.SPREAD,
                          justify_last_line=True)
        bar.add_css_class("sps-toolbar")
        right = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        self.toggle = Adw.ToggleGroup()
        for key, label, tip in ((COCKPIT, "Cockpit", "One card per session, with answers"),
                                (BOARD, "Board", "Columns by state; drag to journal & close")):
            t = Adw.Toggle(name=key, label=label, tooltip=tip)
            self.toggle.add(t)
        self.toggle.set_active_name(COCKPIT)
        self.toggle.update_property([Gtk.AccessibleProperty.LABEL], ["Sessions view"])
        self.toggle.connect("notify::active-name", self._on_toggle)
        bar.append(self.toggle)

        self.progress = Gtk.Box(spacing=6, visible=False)
        self.progress.add_css_class("sps-progress")
        sp = Adw.Spinner()
        sp.set_size_request(14, 14)
        self.progress_label = Gtk.Label()
        self.progress.append(sp)
        self.progress.append(self.progress_label)
        right.append(self.progress)

        self.close_all_btn = Gtk.Button(label="Close all…")
        self.close_all_btn.add_css_class("flat")
        self.close_all_btn.set_tooltip_text("Close every session, journaling the older ones")
        self.close_all_btn.connect("clicked", lambda *_: self._call("request_close_all"))
        right.append(self.close_all_btn)

        self.new_btn = Gtk.Button(label="＋ New session")
        self.new_btn.add_css_class("suggested-action")
        self.new_btn.set_tooltip_text("Start Claude in a folder, inside tmux")
        self.new_btn.update_property([Gtk.AccessibleProperty.LABEL], ["New session"])
        self.new_btn.connect("clicked", lambda *_: self._call("request_new_session"))
        right.append(self.new_btn)
        bar.append(right)
        self.append(bar)

    def _wrap(self, child: Gtk.Widget) -> Gtk.Widget:
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        sc.set_child(child)
        return sc

    def _build_error(self) -> Gtk.Widget:
        """SP-2606: tmux missing."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, vexpand=True)
        box.add_css_class("sp-empty")
        box.append(_state_icon("!"))
        title = Gtk.Label(label=ERROR_TITLE, wrap=True, justify=Gtk.Justification.CENTER)
        title.add_css_class("sp-empty-title")
        box.append(title)
        text = Gtk.Label(label=ERROR_TEXT, wrap=True, max_width_chars=44,
                         justify=Gtk.Justification.CENTER)
        text.add_css_class("sp-empty-text")
        box.append(text)
        code = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        code.add_css_class("sps-code")
        cmd = Gtk.Label(label=TMUX_INSTALL, selectable=True)
        code.append(cmd)
        copy = Gtk.Button(label="Copy")
        copy.set_tooltip_text("Copy the install command")
        copy.update_property([Gtk.AccessibleProperty.LABEL], ["Copy “sudo apt install tmux”"])
        copy.connect("clicked", lambda *_: self._copy(TMUX_INSTALL))
        code.append(copy)
        box.append(code)
        again = Gtk.Button(label="Check again", halign=Gtk.Align.CENTER)
        again.add_css_class("pill")
        again.add_css_class("suggested-action")
        again.connect("clicked", lambda *_: self._call("recheck_environment"))
        self.check_again_btn = again
        box.append(again)
        self.error_box = box
        return box

    def _build_empty(self) -> Gtk.Widget:
        """SP-2607: tmux fine, nothing running."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, vexpand=True)
        box.add_css_class("sp-empty")
        box.append(_state_icon("⌁"))
        title = Gtk.Label(label=EMPTY_TITLE, wrap=True, justify=Gtk.Justification.CENTER)
        title.add_css_class("sp-empty-title")
        box.append(title)
        text = Gtk.Label(wrap=True, max_width_chars=48, justify=Gtk.Justification.CENTER)
        text.set_markup(EMPTY_MARKUP)
        text.add_css_class("sp-empty-text")
        box.append(text)
        new = Gtk.Button(label="＋ New session", halign=Gtk.Align.CENTER)
        new.add_css_class("pill")
        new.add_css_class("suggested-action")
        new.update_property([Gtk.AccessibleProperty.LABEL], ["New session"])
        new.connect("clicked", lambda *_: self._call("request_new_session"))
        self.empty_new_btn = new
        box.append(new)
        return box

    # ───────────────────────── routing ─────────────────────────

    def set_view(self, view: str) -> None:
        view = view if view in VIEWS else COCKPIT
        if view != self.view:
            self.view = view
        if self.toggle.get_active_name() != view:
            self.toggle.set_active_name(view)
        self._apply_mode()

    def _on_toggle(self, *_):
        name = self.toggle.get_active_name()
        if name in VIEWS and name != self.view:
            self.view = name
            self._apply_mode()

    # ───────────────────────── live updates ─────────────────────────

    def _on_store(self, sections: set[str]) -> None:
        if sections & {"sessions", "journal", "settings"}:
            self.refresh()

    def _on_event(self, event) -> None:
        if event.kind != "toast":
            return
        # Any toast ends a ⟳ in flight: the controller answers a re-title with
        # "Title re-read from tmux", or with an error toast when it couldn't.
        for card in self.cards.values():
            if card.retitling:
                card.retitle_done()

    def _ctx(self) -> CardContext:
        return CardContext(now=time.time(), terminal=self._cfg("sessions.terminal"),
                           monthly_log=self.store.state.journal.monthly_log_path)

    def _cfg(self, key: str):
        try:
            return self.config.get(key) if self.config is not None else None
        except Exception:  # noqa: BLE001
            return None

    def refresh(self) -> None:
        sess = self.store.state.sessions
        items = list(sess.items)
        ctx = self._ctx()

        # toolbar
        closing = close_all_progress(sess.close_all)
        self.progress.set_visible(bool(closing))
        set_text(self.progress_label, closing)
        can_close = sess.tmux_ok and any(not busy(s) for s in items) and not closing
        self.close_all_btn.set_sensitive(can_close)
        self.new_btn.set_sensitive(sess.tmux_ok)
        self.new_btn.set_tooltip_text("Start Claude in a folder, inside tmux" if sess.tmux_ok
                                      else "Install tmux first")

        # cockpit cards, kept per session
        live = {s.name for s in items}
        for gone in [n for n in self.cards if n not in live]:
            self.cards.pop(gone).dispose_timers()
        ordered = []
        cb = CardCallbacks(focus=self._focus, retitle=self._retitle, close=self._close,
                           answer=self._answer)
        for s in items:
            card = self.cards.get(s.name)
            if card is None:
                card = self.cards[s.name] = CockpitCard(s.name, cb)
            card.update(s, ctx)
            ordered.append(card)
        sync_children(self.cockpit, ordered)

        self.board.update(items, ctx.now, ctx.monthly_log)
        self._apply_mode()

    def _apply_mode(self) -> None:
        sess = self.store.state.sessions
        if not sess.tmux_ok:
            mode = "error"
        elif not sess.items:
            mode = "empty"
        else:
            mode = self.view
        self.toggle.set_sensitive(mode in VIEWS)
        if mode != self._mode:
            self._mode = mode
            self.stack.set_visible_child_name(mode)

    @property
    def mode(self) -> str | None:
        return self._mode

    def _tick(self) -> bool:
        ctx = self._ctx()
        for s in self.store.state.sessions.items:
            card = self.cards.get(s.name)
            if card is not None:
                card.update(s, ctx)
        self.board.update(self.store.state.sessions.items, ctx.now, ctx.monthly_log)
        return GLib.SOURCE_CONTINUE

    def _start_ticker(self) -> None:
        if not self._age_timer:
            self.refresh()
            self._age_timer = GLib.timeout_add_seconds(AGE_TICK_S, self._tick)

    def _stop_ticker(self) -> None:
        if self._age_timer:
            GLib.source_remove(self._age_timer)
            self._age_timer = 0

    def _teardown(self) -> None:
        self._stop_ticker()
        for fn in (self._unsub, self._unsub_events):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
        for card in self.cards.values():
            card.dispose_timers()

    # ───────────────────────── actions ─────────────────────────

    def _call(self, verb: str, *args) -> None:
        try:
            getattr(self.actions, verb)(*args)
        except Exception:  # noqa: BLE001 — an action failure must not break the page
            log.exception("%s%r failed", verb, args)
            self.store.toast("That didn’t work. See the log for details.", error=True)

    def _focus(self, name: str) -> None:
        self._call("focus_session", name)

    def _retitle(self, name: str) -> None:
        self._call("retitle_session", name)

    def _close(self, name: str) -> None:
        self._call("request_close", name)

    def _answer(self, name: str, option: int, fingerprint: str) -> None:
        self._call("answer_prompt", name, option, fingerprint)

    def _copy(self, text: str) -> None:
        try:
            self.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(text))
            self.store.toast(f"Copied “{text}”")
        except Exception:  # noqa: BLE001
            log.exception("clipboard failed")
            self.store.toast("Couldn’t copy to the clipboard", error=True)


def build(store, actions, config):
    return SessionsPage(store, actions, config)


def show_detail(widget, detail: str) -> None:
    """Route 'sessions:board' / 'sessions:cockpit'."""
    if isinstance(widget, SessionsPage):
        widget.set_view((detail or "").strip().lower())
