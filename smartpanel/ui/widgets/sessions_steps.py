"""The five-step journal checklist (SP-1605) and a box-children sync helper.

`JournalSteps.set_steps(steps)` updates the five rows in place (icon, spinner,
text, class), so a step change never rebuilds the list. Each row is spoken as
"<text>, done / in progress / to do / failed": state is never only colour or a
glyph.
"""
from __future__ import annotations

from typing import Callable, Iterable

from smartpanel.state.model import JOURNAL_STEPS
from smartpanel.ui.widgets._gi import Adw, Gtk
from smartpanel.ui.widgets.sessions_model import STEP_ICON, STEP_WORD, Step


def set_class(widget: Gtk.Widget, prefix: str, new: str | None, attr: str = "_sps_css") -> None:
    """Swap one `<prefix><state>` css class; no-op when unchanged."""
    cur = getattr(widget, attr, None)
    if cur == new:
        return
    if cur:
        widget.remove_css_class(f"{prefix}{cur}")
    if new:
        widget.add_css_class(f"{prefix}{new}")
    setattr(widget, attr, new)


def set_text(label: Gtk.Label, text: str) -> None:
    if label.get_text() != text:
        label.set_text(text)


def sync_children(box: Gtk.Box, widgets: Iterable[Gtk.Widget]) -> None:
    """Make `box`'s children exactly `widgets`, in order, moving rather than
    re-creating them (keeps focus and avoids flicker)."""
    wanted = list(widgets)
    keep = set(map(id, wanted))
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        if id(child) not in keep:
            box.remove(child)
        child = nxt
    prev = None
    for w in wanted:
        parent = w.get_parent()
        if parent is not box:
            if parent is not None:
                parent.remove(w)
            box.insert_child_after(w, prev)
        elif (w.get_prev_sibling() is not prev):
            box.reorder_child_after(w, prev)
        prev = w


class _StepRow(Gtk.Box):
    def __init__(self):
        super().__init__(spacing=8)
        self.add_css_class("sps-step")
        self.icon = Gtk.Label(xalign=0.5, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.icon.add_css_class("sps-step-icon")
        self.spinner = Adw.Spinner(visible=False, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.spinner.set_size_request(16, 16)
        self.text = Gtk.Label(xalign=0, wrap=True, hexpand=True)
        self.append(self.icon)
        self.append(self.spinner)
        self.append(self.text)
        self.state: str | None = None

    def set_step(self, step: Step) -> None:
        set_text(self.text, step.text)
        if step.state != self.state:
            self.state = step.state
            set_class(self, "sps-", step.state)
            self.icon.set_text(STEP_ICON.get(step.state, ""))
            self.icon.set_visible(step.state != "now")
            self.spinner.set_visible(step.state == "now")
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"{step.text}, {STEP_WORD.get(step.state, '')}"])


class JournalSteps(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("sps-steps")
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Journal progress"])
        self.rows = [_StepRow() for _ in JOURNAL_STEPS]
        for r in self.rows:
            self.append(r)
        self._steps: list[Step] = []

    def set_steps(self, steps: list[Step]) -> None:
        if steps == self._steps:
            return
        self._steps = list(steps)
        for row, step in zip(self.rows, steps):
            row.set_step(step)

    @property
    def steps(self) -> list[Step]:
        return list(self._steps)


def icon_button(label: str, tooltip: str, on_click: Callable[[], None], *,
                css: tuple[str, ...] = ()) -> Gtk.Button:
    """A text button with a tooltip and an accessible name."""
    b = Gtk.Button(label=label)
    b.set_tooltip_text(tooltip)
    b.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    for c in css:
        b.add_css_class(c)
    b.connect("clicked", lambda *_: on_click())
    return b
