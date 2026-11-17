"""Board view (SP-1211, SP-1214, SP-1307, SP-1605, SP-1607).

Three columns (Needs you / Running / Idle, starting sessions in Idle), each with
a count, "Nothing here" when empty, and its own scroller so nine-plus sessions
never push the drop zone off the page. Cards move between columns only when the
real state changes: the columns are not drop targets, so dragging a card onto
another column does nothing.

The drop zone under the board ("Drop a card here to journal & close it") runs
the same flow as Close… (`on_drop(name)` -> `actions.request_close`, dialog
included) and lists the sessions being journaled with their checklists.

Drag is not the only way: a focused card closes with Delete and focuses its
terminal with Enter, so the board works from the keyboard.
"""
from __future__ import annotations

from typing import Callable

from smartpanel.state.model import PHASE_LAUNCHING, RUNNING
from smartpanel.ui.widgets._gi import Gtk
from gi.repository import Gdk, GObject  # noqa: E402,I001  (versions pinned by _gi)
from smartpanel.ui.widgets.sessions_model import (BOARD_COLUMNS, DRAG_TOOLTIP, DROP_HINT_MARKUP,
                                                  NOTHING_HERE, board_column, board_detail,
                                                  display_state, draggable, journal_steps)
from smartpanel.ui.widgets.sessions_steps import JournalSteps, set_class, set_text, sync_children
from smartpanel.ui import shellmodel
from smartpanel.uiformat import session_title

DROP_ZONE_MAX_H = 260


class BoardCard(Gtk.Box):
    def __init__(self, name: str, *, on_close: Callable[[str], None],
                 on_focus: Callable[[str], None]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, focusable=True,
                         accessible_role=Gtk.AccessibleRole.GROUP)
        self.name = name
        self.add_css_class("sps-bcard")
        self.state: str | None = None
        self._draggable = False
        self._on_close, self._on_focus = on_close, on_focus

        self.title = Gtk.Label(xalign=0, wrap=True, wrap_mode=2, lines=2, ellipsize=3)
        self.title.add_css_class("sps-bcard-title")
        self.append(self.title)
        sub = Gtk.Box(spacing=8)
        sub.add_css_class("sps-bcard-sub")
        self.project = Gtk.Label(xalign=0, hexpand=True, ellipsize=3)
        self.detail = Gtk.Label(xalign=1)
        sub.append(self.project)
        sub.append(self.detail)
        self.append(sub)
        self.doing = Gtk.Label(xalign=0, ellipsize=3)
        self.doing.add_css_class("sps-doing")
        self.append(self.doing)

        self.drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        self.drag.connect("prepare", self._prepare)
        self.drag.connect("drag-begin", self._drag_begin)
        self.drag.connect("drag-end", lambda *_: self.set_opacity(1.0))
        self.add_controller(self.drag)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    def update(self, s, now: float) -> None:
        state = display_state(s)
        if state != self.state:
            self.state = state
            set_class(self, "sps-st-", state)
        title = session_title(s)
        set_text(self.title, title)
        set_text(self.project, s.project or s.name)
        set_text(self.detail, board_detail(s, now))
        running = state == RUNNING and bool(s.doing)
        self.doing.set_visible(running)
        if running:
            set_text(self.doing, s.doing)
        self._draggable = draggable(s)
        self.drag.set_propagation_phase(Gtk.PropagationPhase.BUBBLE if self._draggable
                                        else Gtk.PropagationPhase.NONE)
        word = shellmodel.activity_meta(state).label
        tip = (f"{DRAG_TOOLTIP} (or press Delete)" if self._draggable else
               ("Starting…" if state == PHASE_LAUNCHING else word))
        if self.get_tooltip_text() != tip:
            self.set_tooltip_text(tip)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"{title}, {word}, {self.detail.get_text()}".rstrip(", ")])

    @property
    def is_draggable(self) -> bool:
        return self._draggable

    def _prepare(self, _src, _x, _y):
        if not self._draggable:
            return None
        return Gdk.ContentProvider.new_for_value(self.name)

    def _drag_begin(self, src, _drag):
        src.set_icon(Gtk.WidgetPaintable.new(self), 12, 12)
        self.set_opacity(0.4)

    def _key(self, _ctl, keyval, _code, _mods) -> bool:
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete) and self._draggable:
            self._on_close(self.name)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space) and self._draggable:
            self._on_focus(self.name)
            return True
        return False


class _Column(Gtk.Box):
    def __init__(self, key: str, heading: str, css: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        self.key = key
        self.heading = heading
        self.add_css_class("sps-col")
        head = Gtk.Box(spacing=6)
        head.add_css_class("sps-col-head")
        label = Gtk.Label(label=heading, xalign=0, hexpand=True)
        label.add_css_class(f"sp-glyph-{css}")
        self.count = Gtk.Label(label="0", xalign=1)
        self.count.add_css_class("sps-col-count")
        head.append(label)
        head.append(self.count)
        self.append(head)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.empty = Gtk.Label(label=NOTHING_HERE, justify=Gtk.Justification.CENTER)
        self.empty.add_css_class("sps-colempty")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.append(self.list)
        inner.append(self.empty)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                           vexpand=True, min_content_height=160)
        self.scroller.set_child(inner)
        self.append(self.scroller)

    def set_cards(self, cards: list[BoardCard]) -> None:
        sync_children(self.list, cards)
        set_text(self.count, str(len(cards)))
        self.empty.set_visible(not cards)
        self.list.set_visible(bool(cards))
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"{self.heading.split(' ', 1)[-1]}, {len(cards)}"])


class _JournalingCard(Gtk.Box):
    def __init__(self, name: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.name = name
        self.add_css_class("sps-card")
        self.title = Gtk.Label(xalign=0, ellipsize=3)
        self.title.add_css_class("sps-card-title")
        self.steps = JournalSteps()
        self.append(self.title)
        self.append(self.steps)

    def update(self, s, monthly_log: str | None) -> None:
        set_text(self.title, session_title(s))
        self.steps.set_steps(journal_steps(s, monthly_log))


class BoardView(Gtk.Box):
    def __init__(self, *, on_drop: Callable[[str], None], on_focus: Callable[[str], None]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, vexpand=True)
        self._on_drop, self._on_focus = on_drop, on_focus
        cols = Gtk.Box(spacing=10, homogeneous=True, vexpand=True)
        self.columns = [_Column(*c) for c in BOARD_COLUMNS]
        for c in self.columns:
            cols.append(c)
        self.append(cols)

        self.dropzone = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.dropzone.add_css_class("sps-dropzone")
        self.dropzone.update_property([Gtk.AccessibleProperty.LABEL],
                                      ["Drop a card here to journal and close it"])
        hint = Gtk.Label(use_markup=True, justify=Gtk.Justification.CENTER, wrap=True)
        hint.set_markup(DROP_HINT_MARKUP)
        self.dropzone.append(hint)
        self.jlist = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        jscroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                     propagate_natural_height=True,
                                     max_content_height=DROP_ZONE_MAX_H)
        jscroll.set_child(self.jlist)
        self.jscroll = jscroll
        self.dropzone.append(jscroll)
        self.append(self.dropzone)

        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("drop", self._drop)
        target.connect("enter", self._enter)
        target.connect("leave", lambda *_: self.dropzone.remove_css_class("sps-over"))
        self.dropzone.add_controller(target)
        self.drop_target = target

        self.cards: dict[str, BoardCard] = {}
        self.jcards: dict[str, _JournalingCard] = {}

    # ── updates ──
    def update(self, items, now: float, monthly_log: str | None = None) -> None:
        live = {s.name for s in items}
        for gone in [n for n in self.cards if n not in live]:
            self.cards.pop(gone)
        for gone in [n for n in self.jcards if n not in live]:
            self.jcards.pop(gone)
        per_col: dict[str, list[BoardCard]] = {c.key: [] for c in self.columns}
        journaling: list[_JournalingCard] = []
        for s in items:
            col = board_column(s)
            if col is None:
                self.cards.pop(s.name, None)
                jc = self.jcards.get(s.name)
                if jc is None:
                    jc = self.jcards[s.name] = _JournalingCard(s.name)
                jc.update(s, monthly_log)
                journaling.append(jc)
                continue
            self.jcards.pop(s.name, None)
            card = self.cards.get(s.name)
            if card is None:
                card = self.cards[s.name] = BoardCard(s.name, on_close=self._on_drop,
                                                      on_focus=self._on_focus)
            card.update(s, now)
            per_col[col].append(card)
        for c in self.columns:
            c.set_cards(per_col[c.key])
        sync_children(self.jlist, journaling)
        self.jscroll.set_visible(bool(journaling))

    def column(self, key: str) -> _Column:
        return next(c for c in self.columns if c.key == key)

    # ── drop ──
    def _enter(self, *_):
        self.dropzone.add_css_class("sps-over")
        return Gdk.DragAction.MOVE

    def _drop(self, _target, value, _x, _y) -> bool:
        self.dropzone.remove_css_class("sps-over")
        return self.drop(str(value) if value is not None else "")

    def drop(self, name: str) -> bool:
        """A card landed on the drop zone. Starting/journaling cards are refused."""
        card = self.cards.get(name)
        if card is None or not card.is_draggable:
            return False
        self._on_drop(name)
        return True
