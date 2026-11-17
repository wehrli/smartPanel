"""Sessions page (Cockpit, Board, error, empty) and session dialogs, built offscreen.
Skipped without a display. Nothing is presented except dialogs inside an unmapped
window; no real tmux, terminal or controller is involved.
"""
import time

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, GLib, Gtk
    _DISPLAY = Gtk.init_check() if hasattr(Gtk, "init_check") else True
    _DISPLAY = _DISPLAY and Gdk.Display.get_default() is not None
except (ImportError, ValueError):  # pragma: no cover
    _DISPLAY = False

pytestmark = pytest.mark.skipif(not _DISPLAY, reason="needs GTK 4 and a display")

from smartpanel import demo  # noqa: E402
from smartpanel.state import Event, Store  # noqa: E402
from smartpanel.state.model import (IDLE, PHASE_JOURNALING, PHASE_LAUNCHING,  # noqa: E402
                                    PHASE_NONE, RUNNING, UNKNOWN, WAITING, JournalProgress,
                                    Loading, Session)


@pytest.fixture(scope="module", autouse=True)
def _adw():
    Adw.init()


def _drain():
    ctx = GLib.MainContext.default()
    while ctx.pending():
        ctx.iteration(False)


class Rec:
    def __init__(self, folders=()):
        self.calls = []
        self.folders = list(folders)

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append((name, a))
            return None
        return rec

    def list_start_folders(self):
        return self.folders


class Cfg:
    def __init__(self, **values):
        self.values = {"sessions.confirm_seconds": 5, "sessions.journal": True,
                       "sessions.min_age_s": 120, "sessions.dir": "~/dev/omdev",
                       "sessions.terminal": "terminator", **values}

    def get(self, key):
        return self.values.get(key)


def _page(scenario="busy"):
    from smartpanel.ui.pages import sessions
    store = Store(demo.build_state(scenario))
    actions = Rec()
    page = sessions.build(store, actions, Cfg())
    return page, store, actions


def _labels(root):
    out = []
    if isinstance(root, Gtk.Label) and root.get_visible():
        out.append(root.get_text())
    child = root.get_first_child()
    while child is not None:
        if child.get_visible():
            out.extend(_labels(child))
        child = child.get_next_sibling()
    return out


def _buttons(root):
    out = []
    if isinstance(root, Gtk.Button) and root.get_visible():
        out.append(root)
    child = root.get_first_child()
    while child is not None:
        if child.get_visible():
            out.extend(_buttons(child))
        child = child.get_next_sibling()
    return out


def _button(root, label):
    return next(b for b in _buttons(root) if b.get_label() == label)


def _children(box):
    out, c = [], box.get_first_child()
    while c is not None:
        out.append(c)
        c = c.get_next_sibling()
    return out


# ───────────────────────── cockpit ─────────────────────────

def test_cockpit_cards_in_display_order():
    page, store, _ = _page()
    assert page.mode == "cockpit"
    names = [c.name for c in _children(page.cockpit)]
    assert names == [s.name for s in store.state.sessions.items]
    first = page.cards["claude-smartPanel"]
    text = _labels(first)
    assert "Fix swap alert hysteresis" in text and "Needs you" in text
    assert "Do you want to make this edit to alerts.py?" in text
    assert "1. Yes" in text and "● attached" in text and "$2.90" in text
    assert [b.get_label() for b in first.answer_buttons] == ["1 · Yes", "2 · Yes", "3 · No"]
    assert first.answer_buttons[0].has_css_class("suggested-action")
    assert not first.answer_buttons[1].has_css_class("suggested-action")
    assert first.has_css_class("sps-st-waiting")
    assert not first.controls.get_visible()          # no Focus/Title/Close on a waiting card


def test_running_card_shows_doing_and_controls():
    page, _, actions = _page()
    card = page.cards["claude-tacomap"]
    assert card.doing_row.get_visible() and card.doing.get_text() == "Bash · npm test -- filters"
    assert card.controls.get_visible() and not card.answers.get_visible()
    _button(card, "Focus").emit("clicked")
    _button(card, "Close…").emit("clicked")
    assert ("focus_session", ("claude-tacomap",)) in actions.calls
    assert ("request_close", ("claude-tacomap",)) in actions.calls


def test_answer_sends_fingerprint_once():
    page, store, actions = _page()
    card = page.cards["claude-smartPanel"]
    fp = store.state.sessions.items[0].prompt.fingerprint
    card.answer_buttons[1].emit("clicked")
    assert ("answer_prompt", ("claude-smartPanel", 1, fp)) in actions.calls
    assert card.answers_locked                       # no double send into a live pane
    card.answer_buttons[0].emit("clicked")           # insensitive in the UI; still one call
    store.mutate("sessions", lambda s: (setattr(s.items[0], "activity", RUNNING),
                                        setattr(s.items[0], "prompt", None)))
    assert card.state == RUNNING and card.doing_row.get_visible()


def test_unparsed_prompt_offers_only_open_terminal():
    page, store, actions = _page()
    store.mutate("sessions", lambda s: setattr(s.items[0], "prompt", None))
    card = page.cards["claude-smartPanel"]
    assert "This session is waiting for you" in _labels(card)
    assert card.answer_buttons == []
    _button(card, "Open terminal").emit("clicked")
    assert ("focus_session", ("claude-smartPanel",)) in actions.calls


def test_cards_update_in_place_and_reorder():
    page, store, _ = _page()
    card = page.cards["claude-ledgerly"]

    def wake(s):
        q = next(x for x in s.items if x.name == "claude-ledgerly")
        q.activity = RUNNING
        q.doing = "Edit · migrate.go"
        s.items.remove(q)
        s.items.insert(0, q)
    store.mutate("sessions", wake)
    assert page.cards["claude-ledgerly"] is card            # same widget, updated
    assert _children(page.cockpit)[0] is card
    assert card.doing.get_text() == "Edit · migrate.go"
    store.mutate("sessions", lambda s: s.items.pop(0))
    assert "claude-ledgerly" not in page.cards
    assert card.get_parent() is None


def test_journaling_and_starting_cards():
    page, store, actions = _page()

    def go(s):
        s.items[3].phase = PHASE_JOURNALING
        s.items[3].journal = JournalProgress(step=1)
        s.items.append(Session("claude-new", project="new", phase=PHASE_LAUNCHING,
                               activity=UNKNOWN, created=time.time()))
    store.mutate("sessions", go)
    j = page.cards["claude-ledgerly"]
    assert j.steps.get_visible() and not j.controls.get_visible()
    assert [s.state for s in j.steps.steps] == ["done", "now", "todo", "todo", "todo"]
    assert "Journaling" in _labels(j)
    st = page.cards["claude-new"]
    assert st.starting_row.get_visible() and "Opening Terminator…" in _labels(st)
    assert "Starting" in _labels(st) and "starting…" in _labels(st)
    assert not st.controls.get_visible()


def test_retitle_spinner_until_toast():
    page, store, actions = _page()
    card = page.cards["claude-ledgerly"]
    _button(card, "⟳ Title").emit("clicked")
    assert ("retitle_session", ("claude-ledgerly",)) in actions.calls
    assert card.retitling and not card.retitle_btn.get_sensitive()
    card._retitle_started -= 10_000           # pretend the minimum time has passed
    store.toast("Title re-read from tmux")
    assert card.retitling                     # the toast alone doesn't end it
    store.emit(Event("retitled", {"name": "claude-other"}))
    assert card.retitling
    store.emit(Event("retitled", {"name": "claude-ledgerly"}))
    assert not card.retitling and card.retitle_btn.get_sensitive()


def test_retitle_ignores_unrelated_toasts():
    page, store, actions = _page()
    card = page.cards["claude-ledgerly"]
    _button(card, "⟳ Title").emit("clicked")
    card._retitle_started -= 10_000
    store.toast("Sent “1” to Fix swap alert hysteresis")
    store.toast("Copied sudo apt install tmux")
    assert card.retitling
    store.toast(f"Couldn’t re-read the title of {card.title.get_text()}", error=True)
    assert not card.retitling
    card.dispose_timers()


def test_retitle_done_cancels_backstop_timer():
    page, store, actions = _page()
    card = page.cards["claude-ledgerly"]
    _button(card, "⟳ Title").emit("clicked")
    first = card._retitle_timer
    assert first
    card._retitle_started -= 10_000
    card.retitle_done()
    assert card._retitle_timer == 0
    # the first backstop is gone from the main context, not merely forgotten
    assert GLib.MainContext.default().find_source_by_id(first) is None
    card.set_retitling(True)
    second = card._retitle_timer
    card.set_retitling(False)
    assert card._retitle_timer == 0
    assert GLib.MainContext.default().find_source_by_id(second) is None


# ───────────────────────── toolbar / routing / states ─────────────────────────

def test_toolbar_actions_and_view_switch():
    from smartpanel.ui.pages import sessions
    page, store, actions = _page()
    page.close_all_btn.emit("clicked")
    page.new_btn.emit("clicked")
    assert ("request_close_all", ()) in actions.calls
    assert ("request_new_session", ()) in actions.calls
    sessions.show_detail(page, "board")
    assert page.mode == "board" and page.toggle.get_active_name() == "board"
    page.toggle.set_active_name("cockpit")
    assert page.mode == "cockpit"
    sessions.show_detail(page, "nonsense")
    assert page.mode == "cockpit"


def test_close_all_progress_in_toolbar():
    page, store, _ = _page()
    store.update("sessions", close_all=Loading(1, 3))
    assert page.progress.get_visible() and page.progress_label.get_text() == \
        "Closing all · 1 of 3 done"
    assert not page.close_all_btn.get_sensitive()
    store.update("sessions", close_all=None)
    assert not page.progress.get_visible() and page.close_all_btn.get_sensitive()


def test_tmux_missing_error_page():
    page, store, actions = _page("notmux")
    assert page.mode == "error"
    text = _labels(page.error_box)
    assert "tmux isn’t installed" in text and "sudo apt install tmux" in text
    assert not page.new_btn.get_sensitive() and not page.close_all_btn.get_sensitive()
    page.check_again_btn.emit("clicked")
    assert ("recheck_environment", ()) in actions.calls
    store.update("sessions", tmux_ok=True)
    assert page.mode in ("cockpit", "empty")


def test_empty_page():
    page, store, actions = _page("empty")
    assert page.mode == "empty"
    assert not page.close_all_btn.get_sensitive() and page.new_btn.get_sensitive()
    page.empty_new_btn.emit("clicked")
    assert ("request_new_session", ()) in actions.calls
    store.update("sessions", items=[Session("claude-x", project="x", activity=IDLE)])
    assert page.mode == "cockpit"


# ───────────────────────── board ─────────────────────────

def test_board_columns_counts_and_empty():
    page, store, _ = _page()
    b = page.board
    counts = {c.key: c.count.get_text() for c in b.columns}
    assert counts == {WAITING: "1", RUNNING: "2", IDLE: "1"}
    store.mutate("sessions", lambda s: setattr(s.items[0], "activity", IDLE))
    assert b.column(WAITING).empty.get_visible()
    assert "Nothing here" in _labels(b.column(WAITING))
    assert b.column(IDLE).count.get_text() == "2"


def test_board_detail_text():
    page, store, _ = _page()
    card = page.board.cards["claude-smartPanel"]
    assert card.detail.get_text().endswith("waiting")
    assert page.board.cards["claude-ledgerly"].detail.get_text() == "$2.09"


def test_board_drop_requests_close_and_refuses_busy_cards():
    page, store, actions = _page()
    assert page.board.drop("claude-ledgerly")
    assert ("request_close", ("claude-ledgerly",)) in actions.calls

    def jr(s):
        s.items[3].phase = PHASE_JOURNALING
        s.items[3].journal = JournalProgress(step=3)
        s.items.append(Session("claude-new", project="new", phase=PHASE_LAUNCHING,
                               created=time.time()))
    store.mutate("sessions", jr)
    assert not page.board.drop("claude-ledgerly")             # now in the drop zone
    assert not page.board.drop("claude-new")                # starting: not draggable
    assert not page.board.cards["claude-new"].is_draggable
    assert "claude-ledgerly" in page.board.jcards
    assert page.board.jscroll.get_visible()
    assert "Close the tmux session" in _labels(page.board.dropzone)
    assert page.board.column(IDLE).count.get_text() == "1"  # the starting one


def test_board_keyboard_close():
    page, _, actions = _page()
    card = page.board.cards["claude-ledgerly"]
    card._key(None, Gdk.KEY_Delete, 0, 0)
    card._key(None, Gdk.KEY_Return, 0, 0)
    assert ("request_close", ("claude-ledgerly",)) in actions.calls
    assert ("focus_session", ("claude-ledgerly",)) in actions.calls


def test_many_sessions_board_columns_scroll():
    page, store, _ = _page("many")
    assert sum(int(c.count.get_text()) for c in page.board.columns) == 9
    assert all(isinstance(c.scroller, Gtk.ScrolledWindow) for c in page.board.columns)


def test_page_inside_the_window_routes_board(tmp_path):
    from smartpanel.ui.window import MainWindow
    store = Store(demo.build_state("busy"))
    win = MainWindow(None, store, Rec(), Cfg(), state_path=tmp_path / "w.json", os_name="x")
    try:
        page = win.show_page("sessions:board")
        assert page.mode == "board"
    finally:
        win.shutdown()


# ───────────────────────── dialogs ─────────────────────────

@pytest.fixture
def shell(tmp_path):
    from smartpanel.ui.window import MainWindow
    store = Store()
    actions = Rec()
    win = MainWindow(None, store, actions, Cfg(), state_path=tmp_path / "w.json", os_name="x")
    yield win, store, actions
    win.shutdown()


def test_confirm_close_copy(shell):
    from smartpanel.ui import session_dialogs as sd
    win, store, actions = shell
    store.update("sessions", items=[Session("claude-smartPanel", project="smartPanel",
                                            title="Fix swap alert hysteresis",
                                            created=time.time() - 3720)])
    d = sd.confirm_close(win, store, actions, Cfg(), "claude-smartPanel")
    assert d.get_heading() == "Journal “Fix swap alert hysteresis” before closing?"
    body = d.get_body()
    assert body.startswith("It has been open 1h02. smartPanel types <tt>")
    assert "journal smartPanel</tt> into the session, waits for the entry" in body
    assert d.get_response_label("without") == "Close without"
    assert d.get_close_response() == "cancel"
    d.emit("response", "cancel")                 # Esc: nothing happens
    assert not [c for c in actions.calls if c[0] == "close_session"]
    d.force_close()


def test_confirm_close_all_uses_event_data(shell):
    from smartpanel.ui import session_dialogs as sd
    win, store, actions = shell
    store.update("sessions", items=[Session("claude-a", project="a")])
    d = sd.confirm_close_all(win, store, actions, Cfg(), {"count": 4, "journaled": 3})
    assert d.get_heading() == "Close all 4 sessions?"
    assert d.get_body().startswith("3 of them get a journal entry first")
    assert d.get_response_appearance("close") == Adw.ResponseAppearance.DESTRUCTIVE
    d.emit("response", "cancel")
    assert ("close_all", ()) not in actions.calls
    d.force_close()
    d = sd.confirm_close_all(win, store, actions, Cfg(**{"sessions.journal": False}))
    assert d.get_body().startswith("Journaling is off")
    d.force_close()


def _flow(d):
    def find(root):
        if isinstance(root, Gtk.FlowBox):
            return root
        c = root.get_first_child()
        while c is not None:
            f = find(c)
            if f is not None:
                return f
            c = c.get_next_sibling()
        return None
    return find(d.get_extra_child())


def test_new_session_preselects_last_used_and_marks_running(shell, monkeypatch):
    from smartpanel.ui import session_dialogs as sd
    win, store, _ = shell
    monkeypatch.setitem(sd._LAST_USED, "path", "/tmp/b")
    actions = Rec([{"name": "a", "path": "/tmp/a", "has_session": True},
                   {"name": "b", "path": "/tmp/b"}])
    d = sd.new_session(win, store, actions, Cfg())
    flow = _flow(d)
    sel = flow.get_selected_children()
    assert sel and sel[0].get_index() == 1
    assert d.get_response_enabled("open")
    labels = _labels(d.get_extra_child())
    assert "~/dev/omdev /" in labels and "●" in labels
    assert "Opens Terminator running Claude inside tmux, in" in labels
    # the path sits whole on its own unwrapped line, slashes intact
    assert any(t.startswith("/tmp/") and "\u2060" not in t for t in labels)
    d.force_close()


def test_new_session_open_launches_and_progress_follows_state(shell, monkeypatch):
    from smartpanel.ui import session_dialogs as sd
    monkeypatch.setattr(sd, "LAUNCH_MIN_MS", 0)
    win, store, _ = shell
    monkeypatch.setitem(sd._LAST_USED, "path", None)

    class Launching(Rec):
        def new_session(self, path):
            self.calls.append(("new_session", (path,)))
            store.mutate("sessions", lambda s: s.items.append(
                Session("claude-a", project="a", cwd=path, phase=PHASE_LAUNCHING)))
    actions = Launching([{"name": "a", "path": "/tmp/a"}])
    d = sd.new_session(win, store, actions, Cfg())
    _flow(d).select_child(_flow(d).get_child_at_index(0))
    d.emit("response", "open")
    assert ("new_session", ("/tmp/a",)) in actions.calls
    assert sd._LAST_USED["path"] == "/tmp/a"
    progress = win.dialogs.current
    assert progress is not None and progress.get_title() == "Opening Terminator"
    store.mutate("sessions", lambda s: setattr(s.items[0], "phase", PHASE_NONE))
    deadline = time.monotonic() + 2
    while win.dialogs.current is progress and time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.01)
    assert win.dialogs.current is not progress


def test_launch_progress_closes_on_error_toast(shell, monkeypatch):
    from smartpanel.ui import session_dialogs as sd
    monkeypatch.setattr(sd, "LAUNCH_MIN_MS", 0)
    win, store, _ = shell

    class Failing(Rec):
        def new_session(self, path):
            store.toast("Couldn’t open Terminator. Install it, or set SMARTPANEL_TERMINAL.",
                        error=True)
    d = sd._launch(win, store, Failing(), "terminator", "/tmp/a")
    deadline = time.monotonic() + 2
    while win.dialogs.current is d and time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.01)
    assert win.dialogs.current is not d


def test_new_session_without_tmux_toasts(shell):
    from smartpanel.ui import session_dialogs as sd
    win, store, actions = shell
    store.update("sessions", tmux_ok=False)
    seen = []
    store.on_event(lambda e: seen.append(e))
    assert sd.new_session(win, store, actions, Cfg()) is None
    assert seen and seen[0].kind == "toast" and seen[0].data["error"]


def test_demo_controller_flow_drives_the_page(tmp_path):
    """End to end with the simulated controller: an answer turns the card to
    running, a drop on the board asks for the journal confirm."""
    from smartpanel.config import Config
    from smartpanel.demo import DemoController
    from smartpanel.ui.pages import sessions
    store = Store(demo.build_state("busy"))
    cfg = Config(path=tmp_path / "c.yaml")
    ctl = DemoController(store, cfg, autostart=False, live=False,
                         notifier=lambda *_a: None)
    page = sessions.build(store, ctl, cfg)
    events = []
    store.on_event(lambda e: events.append(e))
    page.board.drop("claude-ledgerly")
    assert any(e.kind == "confirm_close" and e.data["name"] == "claude-ledgerly" for e in events)
    card = page.cards["claude-smartPanel"]
    card.answer_buttons[0].emit("clicked")
    assert any(e.kind == "toast" and e.data["text"].startswith("Sent “1”") for e in events)
    _drain()
    ctl.stop()
