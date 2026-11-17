"""GTK smoke tests: every widget builds and updates in place; the shell routes,
badges, banners and toasts from store changes. Skipped without a display.

Nothing is shown on screen: widgets are built but no window is presented.
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

from smartpanel.state import Event, Store  # noqa: E402
from smartpanel.state.model import (LEVEL_CRITICAL, LEVEL_OK, LEVEL_WARNING,  # noqa: E402
                                    PHASE_JOURNALING, RUNNING, WAITING, Loading, Session,
                                    SwapState)


@pytest.fixture(scope="module", autouse=True)
def _adw():
    Adw.init()


def _drain():
    ctx = GLib.MainContext.default()
    while ctx.pending():
        ctx.iteration(False)


class RecordingActions:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append((name, a))
            return [] if name == "list_start_folders" else None
        return rec


class FakeConfig:
    def __init__(self, **values):
        self.values = {"sessions.confirm_seconds": 5, "sessions.journal": True,
                       "sessions.min_age_s": 120, "sessions.dir": "~/dev/omdev", **values}

    def get(self, key):
        return self.values.get(key)


# ───────────────────────── widget kit ─────────────────────────

def test_gallery_builds_every_widget():
    from smartpanel.ui.gallery import build_gallery
    g = build_gallery()
    kinds = {type(w).__name__ for w in g.widgets.values()}
    assert {"Banner", "MetricCard", "Sparkline", "HourBars", "Pill", "ActivityGlyph",
            "LevelChip", "NavBadge", "EmptyState", "Track"} <= kinds


def test_metric_card_updates_in_place():
    from smartpanel.ui.widgets import MetricCard
    card = MetricCard("Swap", vmax=100, chip=True)
    card.set_value("66%")
    card.set_sub("5.3 of 8 GB")
    card.set_level(LEVEL_WARNING)
    card.set_history([1, 2, 3], thresholds=[(60, "warn")])
    assert card.value_text == "66%" and card.sub_text == "5.3 of 8 GB"
    assert card.has_css_class("sp-level-warn")
    card.set_level(LEVEL_CRITICAL)
    assert card.has_css_class("sp-level-crit") and not card.has_css_class("sp-level-warn")
    card.set_level(LEVEL_OK)
    assert not card.has_css_class("sp-level-crit")
    assert card.sparkline.values == [1, 2, 3]
    card.set_chip(None, "Not configured")
    assert card.chip.get_text() == "Not configured"


def test_activity_glyph_and_pill_carry_words():
    from smartpanel.ui.widgets import ActivityGlyph, Pill
    g = ActivityGlyph(WAITING)
    assert g.get_text() == "❓" and g.get_tooltip_text() == "Needs you"
    assert g.has_css_class("sp-glyph-waiting")
    g.set_state(PHASE_JOURNALING)
    assert g.has_css_class("sp-glyph-journaling") and not g.has_css_class("sp-glyph-waiting")
    p = Pill()
    p.set_activity(RUNNING)
    assert p.get_text() == "Running" and p.has_css_class("sp-pill-running")


def test_level_chip_and_badge():
    from smartpanel.ui.shellmodel import Badge
    from smartpanel.ui.widgets import LevelChip, NavBadge
    c = LevelChip(LEVEL_WARNING)
    assert c.get_text() == "Warning" and c.has_css_class("sp-chip-warn")
    c.set_level(None)
    assert c.get_text() == "Not configured" and c.has_css_class("sp-chip-na")
    b = NavBadge()
    assert not b.get_visible()
    assert b.set_badge(Badge("2", "amber", "2 sessions need you"))
    assert not b.set_badge(Badge("2", "amber", "2 sessions need you"))  # dedupe
    assert b.get_visible() and b.has_css_class("amber")
    b.set_badge(Badge("!", "red", "tmux is not installed"))
    assert b.has_css_class("red") and not b.has_css_class("amber")
    b.set_badge(None)
    assert not b.get_visible()


def test_banner_dedupes_and_hides():
    from smartpanel.ui.widgets import Banner
    clicked = []
    b = Banner(on_action=lambda: clicked.append(1))
    assert b.show("Swap is filling up", LEVEL_WARNING, "Open System")
    assert not b.show("Swap is filling up", LEVEL_WARNING, "Open System")
    assert b.revealed and b.banner.has_css_class("sp-banner-warn")
    b.show("Swap is critically full", LEVEL_CRITICAL, None)
    assert b.banner.has_css_class("sp-banner-crit") and not b.banner.has_css_class("sp-banner-warn")
    b.banner.emit("button-clicked")
    assert clicked == [1]
    assert b.hide() and not b.hide()
    assert b.level == LEVEL_OK


def test_empty_state_action():
    from smartpanel.ui.widgets import EmptyState
    hits = []
    e = EmptyState("No sessions", "Start one", glyph="⌁", action_label="New",
                   on_action=lambda: hits.append(1))
    e.button.emit("clicked")
    assert hits == [1]
    e.set_action(None)
    assert not e.button.get_visible()


def test_track_over_budget_turns_red():
    from smartpanel.ui.widgets import Track
    t = Track()
    t.set_percent(50)
    assert t.get_fraction() == pytest.approx(0.5) and not t.has_css_class("sp-over")
    t.set_percent(130)
    assert t.get_fraction() == pytest.approx(1.0) and t.has_css_class("sp-over")
    t.set_percent(None)
    assert t.get_fraction() == 0


# ───────────────────────── shell ─────────────────────────

@pytest.fixture
def shell(tmp_path):
    from smartpanel.ui.window import MainWindow
    store = Store()
    store.state.app.version = "0.4.0"
    actions = RecordingActions()
    win = MainWindow(None, store, actions, FakeConfig(), state_path=tmp_path / "window.json",
                     os_name="Ubuntu 26.04")
    yield win, store, actions, tmp_path
    win.shutdown()


def test_window_routes_and_falls_back(shell):
    win, *_ = shell
    assert win.page == "overview"
    win.show_page("system")
    assert win.page == "system" and win.window_title.get_title() == "System"
    win.show_page("bogus")
    assert win.page == "overview"
    win.show_page("")                       # empty keeps the current page
    assert win.page == "overview"
    win.show_page("spend")
    assert win.nav.get_selected_row().page_name == "spend"
    # every page builds (wave-1 placeholders) and is cached
    for p in ("overview", "sessions", "spend", "system", "journal", "settings"):
        assert win.show_page(p) is win.show_page(p)


def test_window_calls_show_detail(shell, monkeypatch):
    win, *_ = shell
    from smartpanel.ui.pages import sessions as page
    seen = []
    monkeypatch.setattr(page, "show_detail", lambda w, d: seen.append(d), raising=False)
    win.show_page("sessions:board")
    assert seen == ["board"] and win.page == "sessions"


def test_nav_row_selection_routes(shell):
    win, *_ = shell
    win.nav.select_row(win._rows["journal"])
    assert win.page == "journal"


def test_badges_follow_the_store(shell):
    win, store, *_ = shell
    assert not win.badges["sessions"].get_visible()
    store.update("sessions", items=[Session("claude-a", activity=WAITING),
                                    Session("claude-b", activity=WAITING),
                                    Session("claude-c", activity=RUNNING)])
    assert win.badges["sessions"].get_text() == "2"
    store.update("sessions", tmux_ok=False)
    assert win.badges["sessions"].get_text() == "!"
    store.update("system", swap=SwapState(percent=66, used_gb=5.3, total_gb=8, level=LEVEL_WARNING))
    assert win.badges["system"].get_text() == "·"
    store.update("system", swap=SwapState(percent=93, used_gb=7.4, total_gb=8, level=LEVEL_CRITICAL))
    assert win.badges["system"].get_text() == "!" and win.badges["system"].has_css_class("red")


def test_banner_follows_swap_and_page(shell):
    win, store, *_ = shell
    assert not win.banner.revealed
    store.update("system", swap=SwapState(percent=66, used_gb=5.3, total_gb=8, level=LEVEL_WARNING))
    assert win.banner.revealed and win.banner.action_label == "Open System"
    assert win.banner.text.startswith("Swap is filling up · 66% used (5.3 of 8 GB)")
    win.show_page("system")
    assert win.banner.action_label is None          # hidden on System
    win.show_page("overview")
    win.banner.banner.emit("button-clicked")
    assert win.page == "system"
    store.update("system", swap=SwapState(percent=12, used_gb=1, total_gb=8, level=LEVEL_OK))
    assert not win.banner.revealed


def test_footer(shell):
    win, store, *_ = shell
    assert win.footer_version.get_text() == "v0.4.0 · Ubuntu 26.04"
    store.update("system", updated_at=time.time() - 3)
    assert win.footer_status.get_text() in ("Updated 3 s ago", "Updated 4 s ago")
    store.update("app", loading=Loading(1, 10))
    assert win.footer_status.get_text() == "Reading transcripts… 1/10"


def test_toasts_replace_and_style_errors(shell):
    win, *_ = shell
    t1 = win.toasts.show({"text": "Copied"})
    assert t1.get_timeout() == 4
    assert not win.toast_overlay.has_css_class("sp-toast-error")
    t2 = win.toasts.show({"text": "Install tmux first", "error": True,
                          "action": {"label": "Settings", "page": "settings"}})
    assert t2.get_timeout() == 6 and t2.get_button_label() == "Settings"
    assert win.toast_overlay.has_css_class("sp-toast-error")
    t2.emit("button-clicked")
    assert win.page == "settings"
    t2.dismiss()
    _drain()
    assert not win.toast_overlay.has_css_class("sp-toast-error")
    assert win.toasts.show({"text": ""}) is None


def test_keyboard_actions(shell):
    win, _store, actions, _ = shell
    win.activate_action("win.page", GLib.Variant("s", "journal"))
    assert win.page == "journal"
    win.activate_action("win.palette", None)
    assert ("open_palette", ()) in actions.calls
    from smartpanel.ui.window import ACCELS
    assert ACCELS["win.palette"] == ["<Control>k"]
    assert ACCELS["win.page::settings"] == ["<Control>6"]
    assert ACCELS["win.close"] == ["<Control>w"]


def test_window_state_saved_on_close(shell):
    import json
    win, _s, _a, tmp = shell
    win.show_page("spend")
    win.emit("close-request")
    data = json.loads((tmp / "window.json").read_text())
    assert data["page"] == "spend"


def test_window_reopens_on_last_page(tmp_path):
    from smartpanel.ui import shellmodel
    from smartpanel.ui.window import MainWindow
    shellmodel.save_window_state(tmp_path / "w.json",
                                 shellmodel.WindowState(1000, 700, False, "journal"))
    win = MainWindow(None, Store(), RecordingActions(), FakeConfig(),
                     state_path=tmp_path / "w.json", os_name="")
    try:
        assert win.page == "journal"
        assert win.get_default_size() == (1000, 700)
    finally:
        win.shutdown()


def test_broken_page_shows_an_error_state(shell, monkeypatch):
    win, *_ = shell
    from smartpanel.ui.pages import journal

    def boom(*_a):
        raise RuntimeError("kaput")
    monkeypatch.setattr(journal, "build", boom)
    widget = win.show_page("journal")
    assert type(widget).__name__ == "EmptyState"


# ───────────────────────── dialogs ─────────────────────────

def test_session_dialogs_build(shell):
    from smartpanel.ui import session_dialogs as sd
    win, store, actions, _ = shell
    store.update("sessions", items=[Session("claude-a", project="a", title="Fix it",
                                            created=time.time() - 3600)])
    d = sd.confirm_close(win, store, actions, FakeConfig(), "claude-a")
    assert d.get_heading() == "Journal “Fix it” before closing?"
    assert d.get_response_label("journal") == "Journal & close (5)"
    d.emit("response", "without")
    assert ("close_session", ("claude-a", False)) in actions.calls
    d.force_close()
    assert sd.confirm_close(win, store, actions, FakeConfig(), "claude-gone") is None

    d = sd.confirm_close_all(win, store, actions, FakeConfig())
    assert d.get_heading() == "Close all 1 session?"
    d.emit("response", "close")
    assert ("close_all", ()) in actions.calls
    d.force_close()

    d = sd.new_session(win, store, actions, FakeConfig())
    assert d.get_heading() == "New Claude session"
    assert not d.get_response_enabled("open")
    d.force_close()


def test_journal_confirm_autoconfirms(shell):
    from smartpanel.ui import session_dialogs as sd
    win, store, actions, _ = shell
    store.update("sessions", items=[Session("claude-a", project="a", created=time.time() - 999)])
    sd.confirm_close(win, store, actions, FakeConfig(**{"sessions.confirm_seconds": 1}), "claude-a")
    deadline = time.monotonic() + 3
    while ("close_session", ("claude-a", True)) not in actions.calls and time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.01)
    assert ("close_session", ("claude-a", True)) in actions.calls


def test_palette_placeholder_builds():
    from smartpanel.ui.palette import PaletteWindow
    actions = RecordingActions()
    p = PaletteWindow(None, Store(), actions, FakeConfig())
    p.entry.set_text("sys")
    p._on_search()
    p._run_selected()
    assert ("open_dashboard", ("system",)) in actions.calls
    p.destroy()


# ───────────────────────── app helpers ─────────────────────────

def test_actions_proxy_forwards_once_wired():
    from smartpanel.app import ActionsProxy
    proxy = ActionsProxy()
    assert proxy.open_palette() is None           # dropped while starting
    assert proxy.list_start_folders() == []
    target = RecordingActions()
    proxy.target = target
    proxy.focus_session("claude-x")
    assert target.calls == [("focus_session", ("claude-x",))]


def test_app_routes_events_without_a_window(monkeypatch):
    from smartpanel import notify
    from smartpanel.app import SmartPanelApp
    sent = []
    monkeypatch.setattr(notify, "send", lambda *a, **k: sent.append((a, k)) or True)
    app = SmartPanelApp(application_id="dev.omdev.SmartPanel.Test")
    app._on_event(Event("toast", {"text": "Journal written", "error": False}))
    assert sent and sent[0][0][1] == "Journal written"
    assert sent[0][1]["urgency"] == "normal"
