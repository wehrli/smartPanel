"""Session dialogs (SP-1503, SP-1504, SP-1506, SP-1604, SP-1606, SP-1609).

Entry points (called by app.py when the controller emits the matching Event):

    confirm_close(window, store, actions, config, name)
    confirm_close_all(window, store, actions, config, data=None)
    new_session(window, store, actions, config)

`window` is the MainWindow; dialogs go through `window.present_dialog` so the
dialog host replaces / closes them. Copy is from the prototype's `dialogHTML()`.

- Journal confirm: "Journal “<title>” before closing?", **Close without** /
  **Journal & close (5)** counting down from `sessions.confirm_seconds` and
  confirming on its own; Esc cancels (nothing happens).
- Close all: "Close all 4 sessions?", how many get a journal, destructive
  **Close all**.
- New session: breadcrumb of `sessions.dir`, a two-column folder grid from
  `actions.list_start_folders()` (a single click only *selects*; Open, a
  double-click or Enter launches), the last-used folder preselected, folders with
  a running session marked, **Browse…** for anything else. After Open, a short
  "Opening Terminator" dialog follows the launch in the state: it closes once the
  new session shows up (or leaves Starting), or as soon as an error toast says why
  it didn't.
"""
from __future__ import annotations

import logging
import time
from html import escape

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from smartpanel.state.model import PHASE_LAUNCHING  # noqa: E402
from smartpanel.ui.widgets import sessions_css  # noqa: E402
from smartpanel.uiformat import home_path  # noqa: E402
from smartpanel.ui.widgets.sessions_model import (close_all_body,  # noqa: E402
                                                  close_all_counts, close_all_heading,
                                                  confirm_age, confirm_heading, crumbs,
                                                  folder_hint, journal_button_label,
                                                  journal_command, launch_detail,
                                                  launch_title, preselect)

log = logging.getLogger(__name__)

# The folder last opened from the picker (this process). Preselected next time.
_LAST_USED: dict[str, str | None] = {"path": None}

LAUNCH_MIN_MS = 700        # the progress dialog never just flashes
LAUNCH_SHOWN_MS = 2500     # once the Starting card is up, the card carries on
LAUNCH_MAX_MS = 10000      # matches the controller's launch timeout

__all__ = ["confirm_close", "confirm_close_all", "new_session", "journal_button_label"]


def _find(store, name: str):
    return next((s for s in store.state.sessions.items if s.name == name), None)


def _present(window, dialog):
    sessions_css.install()
    if hasattr(window, "present_dialog"):
        return window.present_dialog(dialog)
    dialog.present(window)
    return dialog


def _cfg(config, key: str, default=None):
    try:
        v = config.get(key) if config is not None else None
    except Exception:  # noqa: BLE001
        v = None
    return default if v is None else v


def _nobreak(command: str) -> str:
    """Keep a command on one line: no break after '/' or at its space."""
    return command.replace("/", "/⁠").replace(" ", " ")


# ───────────────────────── journal confirm (SP-1604) ─────────────────────────

def confirm_body_markup(session, now: float | None = None) -> str:
    cmd = escape(_nobreak(journal_command(session)))
    return (f"It has been open {escape(confirm_age(session, now))}. smartPanel types "
            f"<tt>{cmd}</tt> into the session, waits for the entry, then closes it.")


def confirm_close(window, store, actions, config, name: str):
    """Journal before closing? Auto-confirms after `sessions.confirm_seconds`."""
    session = _find(store, name)
    if session is None:
        store.toast("That session is already gone", error=True)
        return None
    dialog = Adw.AlertDialog(heading=confirm_heading(session), body_use_markup=True,
                             body=confirm_body_markup(session))
    seconds = int(_cfg(config, "sessions.confirm_seconds", 0) or 0)
    dialog.add_response("without", "Close without")
    dialog.add_response("journal", journal_button_label(seconds))
    dialog.set_response_appearance("journal", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("journal")
    dialog.set_close_response("cancel")

    state = {"left": seconds, "timer": 0, "done": False}

    def stop_timer():
        if state["timer"]:
            GLib.source_remove(state["timer"])
            state["timer"] = 0

    def finish(response: str):
        if state["done"]:
            return
        state["done"] = True
        stop_timer()
        try:
            if response == "journal":
                actions.close_session(name, True)
            elif response == "without":
                actions.close_session(name, False)
        except Exception:  # noqa: BLE001 — never strand the dialog open
            log.exception("close_session(%s) failed", name)

    def tick():
        state["left"] -= 1
        if state["left"] <= 0:
            state["timer"] = 0
            try:
                finish("journal")
            finally:
                dialog.force_close()
            return GLib.SOURCE_REMOVE
        dialog.set_response_label("journal", journal_button_label(state["left"]))
        return GLib.SOURCE_CONTINUE

    if seconds > 0:
        # Millisecond timer, not timeout_add_seconds: second-granularity sources
        # are coalesced and the first tick could fire early, auto-confirming
        # before sessions.confirm_seconds have really passed (SP-1604).
        state["timer"] = GLib.timeout_add(1000, tick)
    dialog.connect("response", lambda _d, r: finish(r))
    dialog.connect("closed", lambda *_: finish("cancel"))
    return _present(window, dialog)


# ───────────────────────── close all (SP-1606) ─────────────────────────

def confirm_close_all(window, store, actions, config, data: dict | None = None):
    """'Close all 4 sessions?' using the Event's {count, journaled} when given,
    else counted from the state the way the controller counts."""
    journal_on = bool(_cfg(config, "sessions.journal", True))
    min_age = float(_cfg(config, "sessions.min_age_s", 0) or 0)
    count, journaled = close_all_counts(store.state.sessions.items, journal_on=journal_on,
                                        min_age_s=min_age)
    if data and isinstance(data.get("count"), int):
        count = data["count"]
        journaled = int(data.get("journaled") or 0)
    if not count:
        store.toast("No sessions to close", error=True)
        return None
    dialog = Adw.AlertDialog(heading=close_all_heading(count),
                             body=close_all_body(count, journaled, journal_on))
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("close", "Close all")
    dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def on_response(_d, r):
        if r == "close":
            try:
                actions.close_all()
            except Exception:  # noqa: BLE001
                log.exception("close_all failed")
    dialog.connect("response", on_response)
    return _present(window, dialog)


# ───────────────────────── new session (SP-1503, SP-1504) ─────────────────────────

def _folder_child(f: dict) -> Gtk.FlowBoxChild:
    row = Gtk.Box(spacing=8)
    row.append(Gtk.Image(icon_name="folder-symbolic",
                         accessible_role=Gtk.AccessibleRole.PRESENTATION))
    name = str(f.get("name", ""))
    label = Gtk.Label(label=name, xalign=0, ellipsize=3, hexpand=True)
    row.append(label)
    a11y = name
    if f.get("has_session"):
        dot = Gtk.Label(label="●")
        dot.set_tooltip_text("A session is already running here")
        dot.add_css_class("sps-has-session")
        row.append(dot)
        a11y += ", a session is already running here"
    child = Gtk.FlowBoxChild(child=row)
    child.set_tooltip_text(str(f.get("path", "")) or None)
    child.update_property([Gtk.AccessibleProperty.LABEL], [a11y])
    return child


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
    base = str(_cfg(config, "sessions.dir", "~"))
    terminal = _cfg(config, "sessions.terminal", "terminator")

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    crumb = Gtk.Label(label=crumbs(base), xalign=0, ellipsize=1)  # START
    crumb.add_css_class("sps-crumbs")
    crumb.set_tooltip_text(base)
    box.append(crumb)

    flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, max_children_per_line=2,
                       min_children_per_line=2, homogeneous=True, column_spacing=3,
                       row_spacing=3, activate_on_single_click=False)
    flow.add_css_class("sps-dirs")
    flow.update_property([Gtk.AccessibleProperty.LABEL], ["Folders"])
    # A click only selects; launching takes Open, a double-click or Enter. A
    # mis-click must never spawn a terminal running Claude.
    paths: dict[Gtk.FlowBoxChild, str] = {}
    children: list[Gtk.FlowBoxChild] = []
    for f in folders:
        child = _folder_child(f)
        flow.append(child)
        paths[child] = str(f.get("path", ""))
        children.append(child)
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                  min_content_height=120, max_content_height=230,
                                  propagate_natural_height=True)
    scroller.set_child(flow)
    if folders:
        box.append(scroller)
    else:
        empty = Gtk.Label(label=f"No folders found in {crumbs(base)[:-2]}. Use Browse… to "
                                "pick one.", wrap=True, xalign=0)
        empty.add_css_class("sps-hint")
        box.append(empty)

    hint = Gtk.Label(wrap=True, xalign=0)
    hint.add_css_class("sps-hint")
    box.append(hint)
    # The path sits on its own unwrapped line, in monospace like the prototype's
    # <code>, shortened from the left when long — never broken mid-path.
    hint_path = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.START, visible=False,
                          selectable=False)
    hint_path.add_css_class("sps-hint")
    hint_path.add_css_class("monospace")
    box.append(hint_path)
    browse = Gtk.Button(label="Browse…", halign=Gtk.Align.START)
    browse.add_css_class("flat")
    browse.set_tooltip_text("Choose a folder outside this list")
    box.append(browse)

    dialog = Adw.AlertDialog(heading="New Claude session", prefer_wide_layout=True)
    box.set_size_request(400, -1)
    dialog.set_extra_child(box)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("open", "Open")
    dialog.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_close_response("cancel")
    dialog.set_response_enabled("open", False)

    picked = {"custom": None, "launched": False}

    def selected_path() -> str | None:
        sel = flow.get_selected_children()
        if sel:
            return paths.get(sel[0])
        return picked["custom"]

    def sync():
        path = selected_path()
        dialog.set_response_enabled("open", bool(path))
        text = folder_hint(path, terminal)
        short = home_path(path) if path else ""
        cut = text.rfind(short) if short else -1
        if cut > 0:
            hint.set_text(text[:cut].rstrip())
            hint_path.set_text(text[cut:])
            hint_path.set_tooltip_text(path)
            hint_path.set_visible(True)
        else:
            hint.set_text(text)
            hint_path.set_visible(False)
        if path:
            dialog.set_default_response("open")

    def on_selected(*_):
        if flow.get_selected_children():
            picked["custom"] = None
        sync()

    def launch(path: str | None) -> None:
        if not path or picked["launched"]:
            return
        picked["launched"] = True
        _LAST_USED["path"] = path
        _launch(window, store, actions, terminal, path)

    def on_activated(*_):
        path = selected_path()
        launch(path)
        dialog.force_close()

    def on_browse(*_):
        fd = Gtk.FileDialog(title="Choose a folder for the new session", modal=True)
        try:
            fd.set_initial_folder(Gio.File.new_for_path(_expand(base)))
        except Exception:  # noqa: BLE001
            pass

        def done(src, res):
            try:
                gfile = src.select_folder_finish(res)
            except GLib.Error:
                return  # cancelled
            path = gfile.get_path() if gfile else None
            if path:
                flow.unselect_all()
                picked["custom"] = path
                sync()
        fd.select_folder(window if isinstance(window, Gtk.Window) else None, None, done)

    flow.connect("selected-children-changed", on_selected)
    flow.connect("child-activated", on_activated)
    browse.connect("clicked", on_browse)
    dialog.connect("response", lambda _d, r: r == "open" and launch(selected_path()))

    idx = preselect(folders, _LAST_USED["path"], store.state.sessions.items)
    if idx is not None:
        flow.select_child(children[idx])
    sync()
    result = _present(window, dialog)

    def focus_selection():
        sel = flow.get_selected_children()
        (sel[0] if sel else (children[0] if children else browse)).grab_focus()
        return GLib.SOURCE_REMOVE
    GLib.idle_add(focus_selection)
    return result


def _expand(path: str) -> str:
    import os
    return os.path.expanduser(path)


def launch_dialog(terminal, path: str) -> Adw.Dialog:
    """'Opening Terminator' + spinner + 'claude-tmux in ~/dev/omdev/ledgerly'."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=28,
                  margin_bottom=28, margin_start=28, margin_end=28,
                  halign=Gtk.Align.CENTER)
    spinner = Adw.Spinner(halign=Gtk.Align.CENTER)
    spinner.set_size_request(28, 28)
    box.append(spinner)
    title = Gtk.Label(label=launch_title(terminal))
    title.add_css_class("title-3")
    box.append(title)
    sub = Gtk.Label(label=launch_detail(path), wrap=True, justify=Gtk.Justification.CENTER)
    sub.add_css_class("sps-mono")
    sub.add_css_class("dim-label")
    box.append(sub)
    dialog = Adw.Dialog(title=launch_title(terminal), content_width=340, child=box)
    dialog.update_property([Gtk.AccessibleProperty.LABEL], [launch_title(terminal)])
    return dialog


def _launch(window, store, actions, terminal, path: str) -> Adw.Dialog | None:
    """Present the progress dialog, call `actions.new_session`, and close the
    dialog from what the state and toasts say (SP-1504, SP-1505)."""
    before = {s.name for s in store.state.sessions.items}
    dialog = launch_dialog(terminal, path)
    started = time.monotonic()
    st = {"timer": 0, "unsubs": [], "closed": False, "seen": False}

    def close_now(*_):
        if st["closed"]:
            return GLib.SOURCE_REMOVE
        st["closed"] = True
        if st["timer"]:
            GLib.source_remove(st["timer"])
            st["timer"] = 0
        for u in st["unsubs"]:
            u()
        st["unsubs"] = []
        dialog.force_close()
        return GLib.SOURCE_REMOVE

    def close_soon(after_ms: int):
        if st["closed"]:
            return
        if st["timer"]:
            GLib.source_remove(st["timer"])
        elapsed = int((time.monotonic() - started) * 1000)
        delay = max(after_ms, LAUNCH_MIN_MS - elapsed, 0)

        def fire():
            st["timer"] = 0
            return close_now()
        st["timer"] = GLib.timeout_add(delay, fire)

    def check(_sections=None):
        new = [s for s in store.state.sessions.items if s.name not in before]
        if not new:
            return
        if any(s.phase != PHASE_LAUNCHING for s in new):
            close_soon(0)            # tmux lists it: done
        elif not st["seen"]:
            st["seen"] = True        # the Starting card is up and carries on
            close_soon(LAUNCH_SHOWN_MS)

    def on_event(ev):
        if ev.kind == "toast" and (ev.data or {}).get("error"):
            close_soon(0)            # the toast says why; don't hide it behind us
        elif ev.kind == "open_dashboard":
            close_soon(0)

    dialog.connect("closed", lambda *_: close_now())
    _present(window, dialog)
    st["unsubs"] = [store.subscribe(check), store.on_event(on_event)]
    st["timer"] = GLib.timeout_add(LAUNCH_MAX_MS, lambda: (st.update(timer=0), close_now())[1])
    try:
        actions.new_session(path)
    except Exception:  # noqa: BLE001
        log.exception("new_session(%s) failed", path)
        store.toast("Couldn’t start the session. See the log for details.", error=True)
        close_now()
        return None
    check()
    return dialog
