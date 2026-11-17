"""Receipt: one day's spend printed as a till receipt (SP-1004, SP-0904, SP-1005,
SP-1102, SP-1103).

Paper look in mono type on off-white (in both modes), with dashed rules and a
scalloped bottom edge drawn with cairo. `set_model(ReceiptModel)` is a no-op
when the model is unchanged, so the page can call it on every store tick; when
it does change only the receipt's own labels are rebuilt.
"""
from __future__ import annotations

import math

from smartpanel.ui.widgets import spend_journal_css as sjcss
from smartpanel.ui.widgets._gi import Adw, Gtk
from gi.repository import Pango  # noqa: E402  (after _gi pinned the versions)
from smartpanel.ui.widgets.spend_journal_model import ESTIMATE_NOTE, ReceiptModel

PAPER_WIDTH = 290
SHADOW = 8               # room around the paper for its drop shadow
SCALLOP_R = 5.0          # notch radius
SCALLOP_STEP = 12.0      # notch spacing (the prototype's 12px mask tile)
PAD_X, PAD_TOP, PAD_BOTTOM = 18, 16, 24


def _rgb(hex_color: str) -> tuple[float, float, float]:
    c = hex_color.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def paper_path(cr, x: float, y: float, w: float, h: float,
               r: float = SCALLOP_R, step: float = SCALLOP_STEP) -> None:
    """A rectangle whose bottom edge has a semicircular notch every `step` px."""
    bottom = y + h
    cr.new_path()
    cr.move_to(x, y)
    cr.line_to(x + w, y)
    cr.line_to(x + w, bottom)
    cx = x + step / 2 + (w % step) / 2
    centres = []
    while cx - r < x + w:
        centres.append(cx)
        cx += step
    for c in reversed(centres):
        right, left = min(c + r, x + w), max(c - r, x)
        cr.line_to(right, bottom)
        if right - left >= 2 * r - 0.01:
            cr.arc_negative(c, bottom, r, 0, math.pi)
        else:
            cr.line_to(left, bottom)
    cr.line_to(x, bottom)
    cr.close_path()


class Receipt(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sjcss.install()
        # The clamp gives the paper its 290 px when there's room and lets it
        # shrink (lines ellipsize, centred lines wrap) in a narrow window.
        clamp = Adw.Clamp(maximum_size=PAPER_WIDTH + 2 * SHADOW,
                          tightening_threshold=PAPER_WIDTH + 2 * SHADOW)
        overlay = Gtk.Overlay()
        overlay.add_css_class("sj-paper")
        self._paper = Gtk.DrawingArea()
        self._paper.set_draw_func(self._draw)
        overlay.set_child(self._paper)
        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._content.set_margin_start(SHADOW + PAD_X)
        self._content.set_margin_end(SHADOW + PAD_X)
        self._content.set_margin_top(SHADOW // 2 + PAD_TOP)
        self._content.set_margin_bottom(SHADOW + PAD_BOTTOM)
        overlay.add_overlay(self._content)
        overlay.set_measure_overlay(self._content, True)
        clamp.set_child(overlay)
        self.append(clamp)

        self._note = Gtk.Label(label=f"Compliments: {ESTIMATE_NOTE}", wrap=True,
                               max_width_chars=40, justify=Gtk.Justification.CENTER)
        self._note.add_css_class("sj-note")
        self._note.set_visible(False)
        self.append(self._note)
        self._model: ReceiptModel | None = None
        self.labels: dict[str, Gtk.Label] = {}   # role -> label, for tests / a11y

    # ── drawing ──
    def _draw(self, _area, cr, width: int, height: int) -> None:
        x, y = SHADOW, SHADOW // 2
        w, h = width - 2 * SHADOW, height - SHADOW // 2 - SHADOW
        if w <= 0 or h <= 0:
            return
        for i, alpha in enumerate((0.05, 0.06, 0.08)):   # cheap soft shadow
            spread = 3 - i
            cr.save()
            cr.translate(0, 3)
            paper_path(cr, x - spread, y - spread + 1, w + 2 * spread, h + 2 * spread)
            cr.set_source_rgba(0, 0, 0, alpha)
            cr.fill()
            cr.restore()
        paper_path(cr, x, y, w, h)
        cr.set_source_rgb(*_rgb(sjcss.PAPER))
        cr.fill()

    # ── content ──
    def _label(self, text: str, *classes: str, xalign: float = 0.0,
               role: str | None = None, hexpand: bool = False) -> Gtk.Label:
        lab = Gtk.Label(label=text, xalign=xalign, hexpand=hexpand)
        for c in classes:
            if c:
                lab.add_css_class(c)
        if role:
            self.labels[role] = lab
        return lab

    def _center(self, text: str, *classes: str, role: str | None = None) -> Gtk.Label:
        lab = self._label(text, *classes, xalign=0.5, role=role)
        lab.set_wrap(True)
        self._content.append(lab)
        return lab

    def _line(self, left: str, right: str, *classes: str, role: str | None = None,
              tooltip: str | None = None) -> Gtk.Box:
        row = Gtk.Box(spacing=8)
        lab_l = self._label(left, *classes, hexpand=True)
        lab_l.set_ellipsize(Pango.EllipsizeMode.END)
        lab_r = self._label(right, *classes, xalign=1.0)
        row.append(lab_l)
        row.append(lab_r)
        if role:
            self.labels[role] = lab_r
            self.labels[role + ".label"] = lab_l
        if tooltip:
            row.set_tooltip_text(tooltip)
        row.update_property([Gtk.AccessibleProperty.LABEL], [f"{left.strip()} {right}"])
        self._content.append(row)
        return row

    def _rule(self) -> None:
        sep = Gtk.Box()
        sep.add_css_class("sj-dash")
        self._content.append(sep)

    def set_model(self, model: ReceiptModel) -> None:
        if model == self._model:
            return
        self._model = model
        self.labels = {}
        child = self._content.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._content.remove(child)
            child = nxt

        self._center("CLAUDE CODE", "sj-brand", role="brand")
        self._center(model.date_line, "sj-m", role="date")
        self._rule()
        if model.empty:
            empty = self._center("NO USAGE THIS DAY", "sj-m", role="empty")
            empty.set_margin_top(18)
            empty.set_margin_bottom(18)
            self._note.set_visible(False)
            self._sync_a11y()
            return
        for i, (name, amt) in enumerate(model.projects):
            self._line(name, amt, role=f"project.{i}")
        if model.models:
            self._rule()
            for i, (name, amt) in enumerate(model.models):
                self._line(f" {name}", amt, "sj-m", role=f"model.{i}")
        self._rule()
        self._line("TOTAL USD", model.total, "sj-tot", role="total")
        if model.praise is not None:
            self._line("incl. compliments", model.praise, "sj-tip", role="praise",
                       tooltip=ESTIMATE_NOTE)
        if model.echo is not None:
            self._line("  echo of them later", model.echo, "sj-m", role="echo",
                       tooltip=ESTIMATE_NOTE)
        if model.budget is not None:
            self._rule()
            self._line("daily budget", model.budget, "sj-m", role="budget")
            if model.budget_rest is not None:
                label, amt = model.budget_rest
                self._line(label, amt, "sj-tip" if model.over else "sj-m", role="rest")
        foot = self._center(model.footer, "sj-m", role="footer")
        foot.set_margin_top(8)
        self._note.set_visible(model.has_praise)
        self._sync_a11y()

    def _sync_a11y(self) -> None:
        m = self._model
        if m is None:
            return
        text = (f"Receipt for {m.date_line}: no usage" if m.empty
                else f"Receipt for {m.date_line}: total {m.total} US dollars")
        self.update_property([Gtk.AccessibleProperty.LABEL], [text])

    @property
    def model(self) -> ReceiptModel | None:
        return self._model

    @property
    def note_visible(self) -> bool:
        return self._note.get_visible()
