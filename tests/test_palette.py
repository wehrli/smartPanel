"""Command palette window (SP-2203, SP-2205, SP-0111). Skipped without a display."""
import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gtk
    _DISPLAY = Gtk.init_check() if hasattr(Gtk, "init_check") else True
    _DISPLAY = _DISPLAY and Gdk.Display.get_default() is not None
except (ImportError, ValueError):  # pragma: no cover
    _DISPLAY = False

pytestmark = pytest.mark.skipif(not _DISPLAY, reason="needs GTK 4 and a display")

from smartpanel.demo import scenarios as sc  # noqa: E402
from smartpanel.state import Store  # noqa: E402
from smartpanel.state.model import WAITING, Session  # noqa: E402

NOW = 1_763_380_000.0


@pytest.fixture(scope="module", autouse=True)
def _adw():
    Adw.init()


class Rec:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a: self.calls.append((name, a))


def _palette(state=None):
    from smartpanel.ui.palette import PaletteWindow
    store = Store(state or sc.build_state("busy", NOW))
    actions = Rec()
    return PaletteWindow(None, store, actions, {}), store, actions


def _search(p, text):
    p.entry.set_text(text)
    p._on_search()


def test_lists_commands_and_status_footer():
    p, store, _ = _palette()
    assert p.entry.get_placeholder_text() == "Type a session or an action…"
    assert p.results[0].title.startswith("Answer · ")
    assert len(p.results) == 9
    assert p.status.get_text().startswith("Swap ")
    assert "1 waiting" in p.status.get_text()
    assert p.none.get_visible() is False
    p.destroy()


def test_query_filters_and_enter_runs_selected():
    p, _store, actions = _palette()
    _search(p, "jour")
    assert p.selected_command.title.startswith("Journal & close · ")
    p._run_selected()
    assert actions.calls[0][0] == "request_close"
    p.destroy()


def test_arrows_move_and_clamp():
    p, _store, actions = _palette()
    _search(p, "go to")
    assert [c.title for c in p.results][:2] == ["Go to Overview", "Go to Sessions"]
    p.move(-1)
    assert p.selected_command.title == "Go to Overview"
    p.move(1)
    assert p.selected_command.title == "Go to Sessions"
    p.move(50)
    assert p.selected_command.title == "Go to Settings"
    p._on_key(None, Gdk.KEY_Up, 0, 0)
    assert p.selected_command.title == "Go to Journal"
    p._on_key(None, Gdk.KEY_Return, 0, 0)
    assert actions.calls == [("open_dashboard", ("journal",))]
    p.destroy()


def test_nothing_matches():
    p, *_ = _palette()
    _search(p, "xyzzy")
    assert p.results == []
    assert p.none.get_visible()
    assert p.none.get_text() == "Nothing matches “xyzzy”"
    p._run_selected()                      # nothing to run, no crash
    p.destroy()


def test_store_change_refreshes_and_keeps_selection():
    p, store, _ = _palette()
    p.set_visible(True)
    _search(p, "go to")
    p.move(2)
    assert p.selected_command.id == "page:spend"
    store.mutate("sessions", lambda s: s.items.insert(
        0, Session("claude-new", project="np", title="New thing", activity=WAITING)))
    assert p.selected_command.id == "page:spend"
    assert "2 waiting" in p.status.get_text()
    p.set_visible(False)
    p.destroy()


def test_highlight_markup_escapes_and_marks():
    from smartpanel.ui.palette import highlight_markup
    m = highlight_markup("Journal & close", [(0, 4)], "#ff0000")
    assert m == ('<span foreground="#ff0000" weight="bold">Jour</span>nal &amp; close')
    assert highlight_markup("a<b", [], "#000") == "a&lt;b"


def test_present_resets_query_and_accepts_token(monkeypatch):
    shown, ids = [], []
    monkeypatch.setattr(Adw.Window, "present", lambda self: shown.append(self))
    p, *_ = _palette()
    monkeypatch.setattr(p, "set_startup_id", ids.append)
    _search(p, "xyz")
    p.present("fake-token")
    assert shown == [p] and ids == ["fake-token"]
    assert p.entry.get_text() == ""
    assert p.results
    p.present()
    assert ids == ["fake-token"]
    p.destroy()
