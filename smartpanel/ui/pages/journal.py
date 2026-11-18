"""Dashboard page 'journal' (SP-1803, SP-1804, SP-1805, SP-2604).

    ┌ entries ──────────────────────┐  ┌ detail ─────────────────────────────┐
    │ Map clustering   ✓ JOURNALED  │  │ TACOMAP · TODAY 12:02             │
    │ 12:02 · tacomap · 2h30m     │  │ Map clustering                      │
    │ Résumé parser   RECEIPT ONLY  │  │ ▌ Journaled tacomap to …          │
    └───────────────────────────────┘  │ ACTIVITY  Duration / Files / …      │
                                       │ ~/.claude/smartpanel/journals/….md  │
                                       │ [Open receipt] [Open JOURNAL/….md]  │
                                       └─────────────────────────────────────┘

`show_detail(widget, '<entry id>')` (route `journal:<id>`) selects that entry;
an id that hasn't arrived yet is remembered and selected when it does. The
page subscribes to the store once; the selection is kept by id across updates.

API (fixed for every page): build(store, actions, config) -> Gtk.Widget
"""
from __future__ import annotations

import logging
import os
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from smartpanel.ui.widgets import EmptyState  # noqa: E402
from smartpanel.ui.widgets import spend_journal_css as sjcss  # noqa: E402
from smartpanel.ui.widgets import spend_journal_model as m  # noqa: E402

log = logging.getLogger(__name__)

SCROLLS = True          # see _scroller: the breakpoint bin sits outside the scroller
LIST_WIDTH = 250
STACK_BELOW = 600       # px: below this the list sits above the detail
NARROW_MIN = 280
_WATCH = {"journal", "settings"}


def _scroller(child: Gtk.Widget) -> Gtk.ScrolledWindow:
    sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                            hexpand=True)
    sw.set_child(child)
    return sw


def _set_text(label: Gtk.Label, text: str) -> None:
    if label.get_text() != text:
        label.set_text(text)


def _a11y(widget: Gtk.Widget, text: str) -> None:
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])


def _swap(widget: Gtk.Widget, old: str | None, new: str | None) -> str | None:
    if old != new:
        if old:
            widget.remove_css_class(old)
        if new:
            widget.add_css_class(new)
    return new


def _button_label(text: str) -> Gtk.Label:
    """A button label shown whole: the button row wraps instead (SP-1803). It
    only ellipsizes, at the end, when a single button is wider than the card,
    so the detail never forces the window wider."""
    return Gtk.Label(label=text, ellipsize=Pango.EllipsizeMode.END, width_chars=8)


class EntryRow(Gtk.ListBoxRow):
    def __init__(self):
        super().__init__()
        grid = Gtk.Grid(column_spacing=8, row_spacing=2)
        self.title = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.title.add_css_class("sj-jtitle")
        self.badge = Gtk.Label(xalign=1)
        self.badge.add_css_class("sj-badge")
        self.meta = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.meta.add_css_class("sp-muted")
        self.meta.add_css_class("caption")
        self.meta.add_css_class("sj-tnum")
        grid.attach(self.title, 0, 0, 1, 1)
        grid.attach(self.badge, 1, 0, 1, 1)
        grid.attach(self.meta, 0, 1, 2, 1)
        self.set_child(grid)
        self.row: m.JournalRow | None = None
        self._badge_css: str | None = None

    def set_row(self, row: m.JournalRow) -> None:
        if row == self.row:
            return
        self.row = row
        _set_text(self.title, row.title)
        self.title.set_tooltip_text(row.title)
        _set_text(self.badge, row.badge)
        self._badge_css = _swap(self.badge, self._badge_css, f"sj-badge-{row.badge_css}")
        _set_text(self.meta, row.meta)
        _a11y(self, row.a11y)


class Detail(Gtk.Box):
    """The right-hand card. `set_detail` re-sets only what changed."""

    def __init__(self, on_open):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, hexpand=True,
                         valign=Gtk.Align.START)
        self.add_css_class("sp-card")
        self.add_css_class("sj-jdet")
        self._on_open = on_open

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.cap = Gtk.Label(xalign=0, wrap=True)
        self.cap.add_css_class("sp-cap")
        self.title = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.title.add_css_class("sj-jdet-title")
        head.append(self.cap)
        head.append(self.title)
        self.append(head)

        self.quote_box = Gtk.Box()
        self.quote_box.add_css_class("sj-quote")
        self.quote = Gtk.Label(xalign=0, wrap=True, selectable=True, hexpand=True,
                               wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.quote_box.append(self.quote)
        self.append(self.quote_box)

        self.activity_cap = Gtk.Label(label="ACTIVITY", xalign=0)
        self.activity_cap.add_css_class("sp-cap")
        self.append(self.activity_cap)
        self.dl = Gtk.Grid(column_spacing=10, row_spacing=5)
        self.dd: dict[str, Gtk.Label] = {}
        for i, term in enumerate(("Duration", "Files", "Commands", "Tools")):
            dt = Gtk.Label(label=term, xalign=0, yalign=0)
            dt.add_css_class("sj-dt")
            dt.set_size_request(110, -1)
            dd = Gtk.Label(xalign=0, yalign=0, wrap=True, selectable=True, hexpand=True,
                           wrap_mode=Pango.WrapMode.WORD_CHAR)
            dd.add_css_class("sj-dd")
            self.dl.attach(dt, 0, i, 1, 1)
            self.dl.attach(dd, 1, i, 1, 1)
            self.dd[term] = dd
        self.append(self.dl)

        # WORD_CHAR: Pango breaks after '/' and '-' first, and only splits a
        # segment mid-word when it alone is wider than the line.
        self.path = Gtk.Label(xalign=0, wrap=True, selectable=True,
                              wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.path.add_css_class("sj-path")
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_insert_hyphens_new(False))   # a path, not a word
        self.path.set_attributes(attrs)
        self.append(self.path)

        # Side by side when both fit, otherwise the second wraps below; the
        # file name on 'Open JOURNAL/2026-11.md' is never cut to make room.
        buttons = Adw.WrapBox(child_spacing=8, line_spacing=8, halign=Gtk.Align.START)
        buttons.set_margin_top(2)
        self.buttons = buttons
        buttons.add_css_class("sj-btns")
        self.open_receipt = Gtk.Button(child=_button_label("Open receipt"))
        self.open_receipt.connect("clicked", lambda *_: self._open("receipt"))
        self.open_log = Gtk.Button(child=_button_label(""))
        self.open_log.connect("clicked", lambda *_: self._open("log"))
        for b in (self.open_receipt, self.open_log):
            buttons.append(b)
            b.set_halign(Gtk.Align.START)
        self.append(buttons)

        self.detail: m.JournalDetail | None = None
        self._quote_css: str | None = None

    def _open(self, which: str) -> None:
        d = self.detail
        if d is None:
            return
        path = d.receipt_file if which == "receipt" else d.log_file
        if path:
            self._on_open(path)

    def set_detail(self, d: m.JournalDetail, *, receipt_exists: bool, log_exists: bool) -> None:
        self.detail = d
        _set_text(self.cap, d.cap.upper())
        self.cap.set_visible(bool(d.cap))
        _set_text(self.title, d.title)
        _set_text(self.quote, d.quote)
        self._quote_css = _swap(self.quote_box, self._quote_css,
                                "sj-failed" if d.failed else None)
        _a11y(self.quote_box, ("Failed: " if d.failed else "Confirmation: ") + d.quote)
        has_activity = bool(d.activity)
        self.activity_cap.set_visible(has_activity)
        self.dl.set_visible(has_activity)
        for term, value in d.activity:
            _set_text(self.dd[term], value)
        _set_text(self.path, d.path)
        self.path.set_visible(bool(d.path))

        self.open_receipt.set_sensitive(receipt_exists)
        self.open_receipt.set_tooltip_text(
            None if receipt_exists else f"Receipt not found: {d.path or 'no path recorded'}")
        self.open_log.set_visible(bool(d.log_name))
        if d.log_name:
            _set_text(self.open_log.get_child(), f"Open {d.log_name}")
            self.open_log.set_sensitive(log_exists)
            self.open_log.set_tooltip_text(
                None if log_exists else f"Not written yet: {m.home_short(d.log_file or '')}")


class JournalPage(Gtk.Stack):
    def __init__(self, store, actions, config, *, exists=os.path.exists,
                 launcher=None, clock=time.time):
        super().__init__(transition_type=Gtk.StackTransitionType.CROSSFADE,
                         hhomogeneous=False, vhomogeneous=False)
        sjcss.install()
        self.store, self.actions, self.config = store, actions, config
        self._exists = exists
        self._launcher = launcher or self._open_with_actions
        self._clock = clock
        self._selected: str | None = None
        self._pending: str | None = None
        self._syncing = False
        self._rows: list[EntryRow] = []
        self.narrow = False
        # ((receipt path, log path), receipt exists, log exists): stat once per change
        self._exist_cache: tuple = (None, False, False)

        self.empty = EmptyState(m.JOURNAL_EMPTY_TITLE, "", glyph="📓")
        self.add_named(_scroller(self.empty), "empty")

        # A Grid, not a Box: a horizontal GtkBox reports its minimum height at
        # its children's minimum widths, which for the wrapping detail labels is
        # far above the natural height (GTK warns on every layout).
        main = Gtk.Grid(column_spacing=12, row_spacing=12)
        for side in ("top", "bottom", "start", "end"):
            getattr(main, f"set_margin_{side}")(18)
        list_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START)
        list_card.add_css_class("sp-card")
        list_card.set_overflow(Gtk.Overflow.HIDDEN)
        list_card.set_size_request(LIST_WIDTH, -1)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
        self.list.add_css_class("sj-jlist")
        _a11y(self.list, "Journal entries")
        self.list.connect("row-selected", self._on_row_selected)
        list_card.append(self.list)
        main.attach(list_card, 0, 0, 1, 1)
        self.detail = Detail(self._open_path)
        main.attach(self.detail, 1, 0, 1, 1)

        bin_ = Adw.BreakpointBin()
        bin_.set_size_request(NARROW_MIN, 200)
        bin_.set_child(_scroller(main))
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f"max-width: {STACK_BELOW}px"))
        bp.connect("apply", lambda *_: self._relayout(main, list_card, narrow=True))
        bp.connect("unapply", lambda *_: self._relayout(main, list_card, narrow=False))
        bp.add_setter(list_card, "valign", Gtk.Align.FILL)
        bin_.add_breakpoint(bp)
        self.add_named(bin_, "main")

        self._unsubscribe = store.subscribe(self._on_change)
        self.connect("destroy", lambda *_: self._unsubscribe())
        # Reopened: 'today'/'yesterday' and the files' existence may be stale.
        self.connect("map", lambda *_: self.refresh())
        self.refresh()

    def _relayout(self, grid: Gtk.Grid, list_card: Gtk.Widget, *, narrow: bool) -> None:
        """Narrow: the list above the detail; wide: side by side."""
        grid.remove(self.detail)
        grid.attach(self.detail, 0 if narrow else 1, 1 if narrow else 0, 1, 1)
        list_card.set_size_request(-1 if narrow else LIST_WIDTH, -1)
        self.narrow = narrow

    # ── store ──
    def _on_change(self, sections: set[str]) -> None:
        if sections & _WATCH:
            self.refresh()

    def refresh(self) -> None:
        st = self.store.state
        entries = st.journal.entries
        if not entries:
            min_age = m.setting(st, self.config, "sessions.min_age_s", 120)
            journal_on = bool(m.setting(st, self.config, "sessions.journal", True))
            self.empty.set_text(m.journal_empty_text(min_age, journal_on))
            self._show("empty")
            return
        # Re-stat on every change: the monthly log or the receipt may have
        # appeared since the entry was first shown (two stats, cheap).
        self._exist_cache = (None, False, False)
        self._sync_rows(entries)
        self._show("main")

    def _show(self, name: str) -> None:
        if self.get_visible_child_name() != name:
            self.set_visible_child_name(name)

    def _sync_rows(self, entries) -> None:
        rows = [m.journal_row(e) for e in entries]
        ids = [r.id for r in rows]
        if self._pending in ids:
            self._selected, self._pending = self._pending, None
        if self._selected not in ids:
            self._selected = ids[0]
        self._syncing = True
        try:
            while len(self._rows) < len(rows):
                w = EntryRow()
                self._rows.append(w)
                self.list.append(w)
            while len(self._rows) > len(rows):
                self.list.remove(self._rows.pop())
            for w, r in zip(self._rows, rows):
                w.set_row(r)
            target = self._rows[ids.index(self._selected)]
            if self.list.get_selected_row() is not target:
                self.list.select_row(target)
        finally:
            self._syncing = False
        self._update_detail()

    def _on_row_selected(self, _box, row) -> None:
        if row is None or self._syncing or not isinstance(row, EntryRow) or row.row is None:
            return
        self._selected = row.row.id
        self._pending = None
        self._update_detail()

    def _entry(self, entry_id: str | None):
        for e in self.store.state.journal.entries:
            if e.id == entry_id:
                return e
        return None

    def _update_detail(self) -> None:
        e = self._entry(self._selected)
        if e is None:
            return
        st = self.store.state
        d = m.journal_detail(e, self._clock(), st.journal.monthly_log_path)
        # Two stats, only when the selection or its entry changes.
        if d != self.detail.detail or self._exist_cache[0] != (d.receipt_file, d.log_file):
            receipt_ok = bool(d.receipt_file) and self._exists(d.receipt_file)
            log_ok = bool(d.log_file) and self._exists(d.log_file)
            self._exist_cache = ((d.receipt_file, d.log_file), receipt_ok, log_ok)
        _, receipt_ok, log_ok = self._exist_cache
        self.detail.set_detail(d, receipt_exists=receipt_ok, log_exists=log_ok)

    # ── routing ──
    def select(self, entry_id: str) -> bool:
        """Select `entry_id`; if it isn't listed yet, select it when it arrives."""
        if self._entry(entry_id) is None:
            self._pending = entry_id
            return False
        self._selected, self._pending = entry_id, None
        self.refresh()
        return True

    @property
    def selected(self) -> str | None:
        return self._selected

    # ── opening files ──
    def _open_path(self, path: str) -> None:
        if not self._exists(path):
            self.store.toast(f"{m.home_short(path)} no longer exists", error=True)
            self._exist_cache = (None, False, False)
            self._update_detail()
            return
        self._launcher(path)

    def _open_with_actions(self, path: str) -> None:
        """`actions.open_path` (async, toasts its own failure); a local
        Gtk.FileLauncher only for an Actions without it."""
        opener = getattr(self.actions, "open_path", None)
        if callable(opener):
            opener(path)
        else:
            self._launch(path)

    def _launch(self, path: str) -> None:
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(path))

        def done(src, res):
            try:
                src.launch_finish(res)
            except GLib.Error as e:
                if not e.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    log.warning("could not open %s: %s", path, e.message)
                    self.store.toast(f"Couldn’t open {m.home_short(path)}", error=True)

        root = self.get_root()
        launcher.launch(root if isinstance(root, Gtk.Window) else None, None, done)


def build(store, actions, config):
    return JournalPage(store, actions, config)


def show_detail(widget, detail: str) -> None:
    """Route 'journal:<entry id>' → select that entry."""
    if isinstance(widget, JournalPage) and detail:
        widget.select(detail)
