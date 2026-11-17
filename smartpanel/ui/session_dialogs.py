"""Session dialogs: journal-confirm, close-all confirm, new-session folder picker.

WAVE-1 WORKING PLACEHOLDER (WP-ui). WP-pages-sessions replaces this file in wave 2;
keep the three entry points and their signatures:

    confirm_close(window, store, actions, config, name)
    confirm_close_all(window, store, actions, config)
    new_session(window, store, actions, config)

`window` is the MainWindow; dialogs go through `window.present_dialog` so the
dialog host can replace / close them. Copy is from the prototype's dialogHTML().
"""
from __future__ import annotations

import logging
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from smartpanel.uiformat import format_age  # noqa: E402

log = logging.getLogger(__name__)


def _find(store, name: str):
    return next((s for s in store.state.sessions.items if s.name == name), None)


def _present(window, dialog):
    if hasattr(window, "present_dialog"):
        return window.present_dialog(dialog)
    dialog.present(window)
    return dialog


def _nobreak(command: str) -> str:
    """Keep a command on one line: no break after '/' or at its space."""
    return command.replace("/", "/\u2060").replace(" ", "\u00a0")


def journal_button_label(seconds_left: int) -> str:
    return f"Journal & close ({seconds_left})" if seconds_left > 0 else "Journal & close"


def confirm_close(window, store, actions, config, name: str):
    """Journal before closing? Auto-confirms after `sessions.confirm_seconds`."""
    session = _find(store, name)
    if session is None:
        store.toast("That session is already gone", error=True)
        return None
    title = session.display_title
    age = format_age(session.created, time.time()) or "a while"
    project = session.project or session.name
    dialog = Adw.AlertDialog(
        heading=f"Journal “{title}” before closing?",
        body=(f"It has been open {age}. smartPanel types {_nobreak('/journal ' + project)} "
              f"into the session, waits for the entry, then closes it."))
    seconds = int(config.get("sessions.confirm_seconds") or 0) if config else 0
    dialog.add_response("without", "Close without")
    dialog.add_response("journal", journal_button_label(seconds))
    dialog.set_response_appearance("journal", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("journal")
    dialog.set_close_response("cancel")

    state = {"left": seconds, "timer": 0, "done": False}

    def finish(response: str):
        if state["done"]:
            return
        state["done"] = True
        if state["timer"]:
            GLib.source_remove(state["timer"])
            state["timer"] = 0
        if response == "journal":
            actions.close_session(name, True)
        elif response == "without":
            actions.close_session(name, False)

    def tick():
        state["left"] -= 1
        if state["left"] <= 0:
            state["timer"] = 0
            finish("journal")
            dialog.force_close()
            return GLib.SOURCE_REMOVE
        dialog.set_response_label("journal", journal_button_label(state["left"]))
        return GLib.SOURCE_CONTINUE

    if seconds > 0:
        state["timer"] = GLib.timeout_add_seconds(1, tick)
    dialog.connect("response", lambda _d, r: finish(r))
    dialog.connect("closed", lambda *_: finish("cancel"))
    return _present(window, dialog)


def confirm_close_all(window, store, actions, config):
    items = store.state.sessions.items
    n = len(items)
    if not n:
        store.toast("No sessions to close", error=True)
        return None
    journal = bool(config.get("sessions.journal")) if config else True
    min_age = int(config.get("sessions.min_age_s") or 0) if config else 0
    now = time.time()
    old = sum(1 for s in items if s.created and now - s.created >= min_age)
    lead = (f"{old} of them get a journal entry first, one at a time, because they share "
            f"one monthly log. " if journal else
            "Journaling is off, so they close straight away. ")
    dialog = Adw.AlertDialog(heading=f"Close all {n} session{'s' if n != 1 else ''}?",
                             body=lead + "Running work is interrupted.")
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("close", "Close all")
    dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda _d, r: r == "close" and actions.close_all())
    return _present(window, dialog)


def new_session(window, store, actions, config):
    """Folder picker over `actions.list_start_folders()`."""
    if not store.state.sessions.tmux_ok:
        store.toast("Install tmux first", error=True)
        return None
    try:
        folders = list(actions.list_start_folders() or [])
    except Exception:  # noqa: BLE001
        log.exception("list_start_folders failed")
        folders = []
    base = config.get("sessions.dir") if config else "~"

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    crumbs = Gtk.Label(label=f"{base} /", xalign=0)
    crumbs.add_css_class("sp-mono")
    crumbs.add_css_class("sp-muted")
    box.append(crumbs)
    flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, max_children_per_line=2,
                       min_children_per_line=2, homogeneous=True, column_spacing=3,
                       row_spacing=3)
    paths: dict[Gtk.FlowBoxChild, str] = {}
    for f in folders:
        row = Gtk.Box(spacing=8)
        row.append(Gtk.Image(icon_name="folder-symbolic"))
        label = Gtk.Label(label=str(f.get("name", "")), xalign=0, ellipsize=3)
        row.append(label)
        if f.get("has_session"):
            dot = Gtk.Label(label="●")
            dot.set_tooltip_text("A session is already running here")
            dot.add_css_class("sp-glyph-running")
            row.append(dot)
        child = Gtk.FlowBoxChild(child=row)
        flow.append(child)
        paths[child] = str(f.get("path", ""))
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                  min_content_height=180, max_content_height=230,
                                  propagate_natural_height=True)
    scroller.set_child(flow)
    if folders:
        box.append(scroller)
    else:
        box.append(Gtk.Label(label=f"No folders found in {base}.", wrap=True))
    hint = Gtk.Label(wrap=True, xalign=0)
    hint.add_css_class("sp-muted")
    box.append(hint)

    dialog = Adw.AlertDialog(heading="New Claude session")
    dialog.set_extra_child(box)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("open", "Open")
    dialog.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_close_response("cancel")
    dialog.set_response_enabled("open", False)

    def selected_path() -> str | None:
        sel = flow.get_selected_children()
        return paths.get(sel[0]) if sel else None

    def on_selected(*_):
        path = selected_path()
        dialog.set_response_enabled("open", bool(path))
        hint.set_text(f"Opens a terminal running Claude inside tmux, in {path}." if path else "")
        if path:
            dialog.set_default_response("open")

    flow.connect("selected-children-changed", on_selected)
    flow.connect("child-activated", lambda *_: (selected_path() and (
        actions.new_session(selected_path()), dialog.force_close())))
    dialog.connect("response",
                   lambda _d, r: r == "open" and selected_path()
                   and actions.new_session(selected_path()))
    return _present(window, dialog)
