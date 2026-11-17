"""Widgets for the Overview and System pages (W2-overview-system).

Page-specific CSS lives here (class prefix `os-`) and is installed once per
display by `install_css()`; colours only ever come from the `--sp-*` tokens, so
light and dark both follow `ui/tokens.py`.

Every widget updates in place through `set_*`/`update` methods that are no-ops
when nothing changed, so pages can call them on every store notification.
"""
from __future__ import annotations

from typing import Callable

from smartpanel.ui.widgets import overview_system_model as m
from gi.repository import Pango

from smartpanel.ui.widgets._gi import Adw, GLib, Gtk
from smartpanel.ui.widgets.labels import ActivityGlyph

CSS = """
.os-page { padding: 16px; }
.sp-empty.os-compact { padding: 18px 16px; }
.os-scard { padding: 12px 13px; }
.os-scard.sp-na { opacity: 0.75; }   /* SP-0602: no sensor / no swap, dimmed */
.os-cap-wait { color: var(--sp-wait); }

/* metric cards in a FlowBox: the child box must not draw a focus ring */
flowbox.os-grid > flowboxchild { padding: 0; }

/* skeleton (SP-2602) */
.os-skel {
  background-color: var(--sp-card2);
  border-radius: 6px;
  animation: os-shimmer 1.4s ease-in-out infinite;
}
@keyframes os-shimmer { 0% { opacity: 1; } 50% { opacity: 0.5; } 100% { opacity: 1; } }

/* "Needs you" card (SP-1209) */
.os-needs {
  background-color: var(--sp-waitbg);
  border: 1px solid var(--sp-level-warn);
  border-radius: 12px;
  padding: 11px 13px;
}
.os-needs .os-q { color: var(--sp-muted); }
.os-title { font-weight: 600; }
button.os-stitle label { font-weight: normal; }
button.os-stitle label.os-title { font-weight: 600; }

/* session rows (SP-1208) */
list.os-sessions > row { padding: 0; }
.os-srow { padding: 6px 8px 6px 12px; min-height: 34px; }
button.os-stitle { padding: 2px 4px; border-radius: 6px; }
button.os-stitle:disabled, button.os-stitle:disabled label { filter: none; opacity: 1; }
button.os-stitle:disabled .os-title { color: var(--sp-fg); }
.os-sub { font-size: 0.88em; color: var(--sp-muted); font-weight: normal; }
.os-age { font-size: 0.9em; color: var(--sp-muted); font-feature-settings: "tnum"; }
.os-jt { font-size: 0.88em; color: var(--sp-jour); }
button.os-ib { min-width: 24px; min-height: 24px; padding: 0; color: var(--sp-muted); }

/* budget + projects */
.os-box { padding: 12px; }
.os-num { font-feature-settings: "tnum"; }
.os-over { color: var(--sp-crit); font-weight: 600; }
button.os-link { padding: 2px 0; color: var(--sp-accent-text); font-weight: 600; }
progressbar.os-pbar > trough { min-height: 7px; min-width: 30px; background: none; border-radius: 4px; }
progressbar.os-pbar > trough > progress { min-width: 0; }
progressbar.os-pbar > trough > progress { min-height: 7px; border-radius: 4px; }
progressbar.os-track-warn > trough > progress { background-color: var(--sp-level-warn); }
progressbar.os-track-crit > trough > progress { background-color: var(--sp-level-crit); }
"""


def _palette_css() -> str:
    rules = [f"progressbar.os-pc-{i} > trough > progress {{ background-color: {c}; }}"
             for i, c in enumerate(m.PROJECT_PALETTE)]
    rules.append(f"progressbar.os-pc-x > trough > progress {{ background-color: {m.OTHERS_COLOR}; }}")
    return "\n".join(rules)


_css_displays: set[int] = set()


def install_css() -> None:
    """Add the page CSS once per display (idempotent)."""
    from gi.repository import Gdk
    display = Gdk.Display.get_default()
    if display is None or id(display) in _css_displays:
        return
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS + _palette_css())
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
    _css_displays.add(id(display))


def a11y_label(widget: Gtk.Widget, text: str) -> None:
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])


def set_text(label: Gtk.Label, text: str) -> None:
    if label.get_text() != text:
        label.set_text(text)


def card_box(*classes: str, spacing: int = 6) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    box.add_css_class("sp-card")
    for c in classes:
        box.add_css_class(c)
    return box


def cap(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text.upper(), xalign=0)
    label.add_css_class("sp-cap")
    return label


def toggle_class(widget: Gtk.Widget, css: str, on: bool) -> None:
    if widget.has_css_class(css) != on:
        (widget.add_css_class if on else widget.remove_css_class)(css)


# ───────────────────────── skeleton ─────────────────────────

class SkeletonCard(Gtk.Box):
    """The prototype's loading card: caption, value and chart placeholders."""

    def __init__(self, label: str = "Loading"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("sp-card")
        self.add_css_class("sp-metric-card")
        for frac, h in ((0.5, 10), (0.7, 22), (1.0, 26)):
            bar = Gtk.Box(halign=Gtk.Align.FILL if frac == 1.0 else Gtk.Align.START,
                          accessible_role=Gtk.AccessibleRole.PRESENTATION)
            bar.add_css_class("os-skel")
            bar.set_size_request(int(120 * frac), h)
            self.append(bar)
        self.set_accessible_role(Gtk.AccessibleRole.GROUP)
        a11y_label(self, f"{label}, loading")


# ───────────────────────── session row ─────────────────────────

def _spinner() -> Gtk.Widget:
    sp = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)
    sp.set_size_request(12, 12)
    sp.set_valign(Gtk.Align.CENTER)
    return sp


class SessionRow(Gtk.ListBoxRow):
    """glyph · title button (title, sub-line) · age | journal step · ⟳ · ✕"""

    RETITLE_MIN_MS = 500
    JOURNAL_FLOOR_CHARS = 24
    RETITLE_MAX_MS = 3000

    def __init__(self, name: str, *, on_focus: Callable[[str], None],
                 on_retitle: Callable[[str], None], on_close: Callable[[str], None]):
        super().__init__(activatable=False, selectable=False)
        self.name = name
        self._model: m.SessionRow | None = None
        self._retitle_started = 0
        self._retitle_timer = 0

        box = Gtk.Box(spacing=8)
        box.add_css_class("os-srow")
        self.glyph = ActivityGlyph()
        self.glyph.set_valign(Gtk.Align.CENTER)
        box.append(self.glyph)

        self.title_button = Gtk.Button(hexpand=True)
        self.title_button.add_css_class("flat")
        self.title_button.add_css_class("os-stitle")
        self.title_button.set_tooltip_text("Bring its terminal forward")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        head = Gtk.Box(spacing=6)
        self.title = Gtk.Label(xalign=0, ellipsize=3, hexpand=False, width_chars=6)
        self.title.add_css_class("os-title")
        head.append(self.title)
        self.spinner = _spinner()
        self.spinner.set_visible(False)
        head.append(self.spinner)
        inner.append(head)
        self.sub = Gtk.Label(xalign=0, ellipsize=3)
        self.sub.add_css_class("os-sub")
        inner.append(self.sub)
        self.title_button.set_child(inner)
        self.title_button.connect("clicked", lambda *_: on_focus(self.name))
        box.append(self.title_button)

        self.right = Gtk.Label(xalign=1, ellipsize=3, max_width_chars=48)
        self.right.add_css_class("os-age")
        box.append(self.right)

        self.retitle_button = Gtk.Button(label="⟳", valign=Gtk.Align.CENTER)
        self.retitle_button.set_tooltip_text("Re-read title from tmux")
        self.close_button = Gtk.Button(label="✕", valign=Gtk.Align.CENTER)
        self.close_button.set_tooltip_text("Close session")
        for b in (self.retitle_button, self.close_button):
            b.add_css_class("flat")
            b.add_css_class("circular")
            b.add_css_class("os-ib")
            box.append(b)
        self.retitle_button.connect("clicked", lambda *_: self._retitle(on_retitle))
        self.close_button.connect("clicked", lambda *_: on_close(self.name))
        self.set_child(box)

    # ── updates ──
    def update(self, row: m.SessionRow) -> None:
        if row == self._model:
            return
        self._model = row
        self.glyph.set_state(row.state)
        set_text(self.title, row.title)
        set_text(self.sub, row.sub)
        set_text(self.right, row.right)
        toggle_class(self.right, "os-jt", row.journaling)
        toggle_class(self.right, "os-age", not row.journaling)
        self.right.set_tooltip_text(row.right if row.journaling else None)
        # an ellipsizing label's minimum is its width_chars and its natural
        # width the full text: the title (ellipsize END) gives way first, down
        # to its own floor; below JOURNAL_FLOOR_CHARS the step ellipsizes in
        # the MIDDLE so the "…/2026-11.md…" tail stays readable
        self.right.set_width_chars(min(len(row.right), self.JOURNAL_FLOOR_CHARS)
                                   if row.journaling else -1)
        self.right.set_ellipsize(Pango.EllipsizeMode.MIDDLE if row.journaling
                                 else Pango.EllipsizeMode.END)
        for b in (self.title_button, self.retitle_button, self.close_button):
            if b.get_sensitive() == row.busy:
                b.set_sensitive(not row.busy)
        a11y_label(self.title_button, f"{row.title}, {row.sub}. Bring its terminal forward")
        a11y_label(self.retitle_button, f"Re-read the title of {row.title}")
        a11y_label(self.close_button, f"Close {row.title}")

    # ── retitle feedback (SP-1404) ──
    @property
    def retitling(self) -> bool:
        return self.spinner.get_visible()

    def _retitle(self, on_retitle: Callable[[str], None]) -> None:
        self.spinner.set_visible(True)
        self._retitle_started = GLib.get_monotonic_time()
        self._arm(self.RETITLE_MAX_MS)       # backstop if no toast ever comes
        on_retitle(self.name)

    def retitle_done(self) -> None:
        """The title was re-read: drop the spinner after a short minimum."""
        if not self.retitling:
            return
        elapsed = (GLib.get_monotonic_time() - self._retitle_started) // 1000
        self._arm(max(0, self.RETITLE_MIN_MS - int(elapsed)))

    def _arm(self, ms: int) -> None:
        if self._retitle_timer:
            GLib.source_remove(self._retitle_timer)

        def hide():
            self._retitle_timer = 0
            self.spinner.set_visible(False)
            return GLib.SOURCE_REMOVE
        self._retitle_timer = GLib.timeout_add(ms, hide)

    def dispose_timers(self) -> None:
        if self._retitle_timer:
            GLib.source_remove(self._retitle_timer)
            self._retitle_timer = 0


# ───────────────────────── needs-you row ─────────────────────────

class NeedsRow(Gtk.Box):
    def __init__(self, name: str, on_answer: Callable[[str], None]):
        super().__init__(spacing=10)
        self.name = name
        self._model: m.NeedsRow | None = None
        glyph = ActivityGlyph("waiting")
        glyph.set_valign(Gtk.Align.CENTER)
        self.append(glyph)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.title = Gtk.Label(xalign=0, ellipsize=3)
        self.title.add_css_class("os-title")
        self.question = Gtk.Label(xalign=0, ellipsize=3)
        self.question.add_css_class("os-q")
        self.question.add_css_class("os-sub")
        text.append(self.title)
        text.append(self.question)
        self.append(text)
        self.button = Gtk.Button(label="Answer", valign=Gtk.Align.CENTER)
        self.button.add_css_class("sp-accent")
        self.button.connect("clicked", lambda *_: on_answer(self.name))
        self.append(self.button)

    def update(self, row: m.NeedsRow) -> None:
        if row == self._model:
            return
        self._model = row
        set_text(self.title, row.title)
        set_text(self.question, row.question)
        self.question.set_tooltip_text(row.question)
        a11y_label(self.button, f"Answer {row.title}")


# ───────────────────────── project bars ─────────────────────────

def _color_class(color: str) -> str:
    try:
        return f"os-pc-{m.PROJECT_PALETTE.index(color)}"
    except ValueError:
        return "os-pc-x"


class ProjectBars(Gtk.Grid):
    """name · coloured bar · amount, one line per project (SP-0807)."""

    def __init__(self):
        super().__init__(column_spacing=8, row_spacing=8)
        self._rows: list[tuple[Gtk.Label, Gtk.ProgressBar, Gtk.Label, list]] = []
        self._model: list[m.ProjectBar] | None = None

    def update(self, bars: list[m.ProjectBar]) -> None:
        if bars == self._model:
            return
        self._model = list(bars)
        while len(self._rows) < len(bars):
            i = len(self._rows)
            name = Gtk.Label(xalign=0, ellipsize=3, width_chars=11, max_width_chars=11)
            bar = Gtk.ProgressBar(hexpand=True, valign=Gtk.Align.CENTER)
            bar.add_css_class("os-pbar")
            bar.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)
            amount = Gtk.Label(xalign=1, width_chars=8)
            amount.add_css_class("os-num")
            self.attach(name, 0, i, 1, 1)
            self.attach(bar, 1, i, 1, 1)
            self.attach(amount, 2, i, 1, 1)
            self._rows.append((name, bar, amount, [None]))
        while len(self._rows) > len(bars):
            name, bar, amount, _ = self._rows.pop()
            for w in (name, bar, amount):
                self.remove(w)
        for (name, bar, amount, css), b in zip(self._rows, bars):
            set_text(name, b.name)
            name.set_tooltip_text(b.name)
            set_text(amount, m.money(b.cost))
            bar.set_fraction(max(0.0, min(b.fraction, 1.0)))
            cls = _color_class(b.color)
            if css[0] != cls:
                if css[0]:
                    bar.remove_css_class(css[0])
                bar.add_css_class(cls)
                css[0] = cls
            a11y_label(name, f"{b.name}: {m.money(b.cost)}")

    @property
    def names(self) -> list[str]:
        return [r[0].get_text() for r in self._rows]
