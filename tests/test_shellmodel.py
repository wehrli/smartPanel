"""Dashboard shell decisions (SP-2102/2103/2106, SP-0106/0109, SP-1212, SP-2502)."""
import json

import pytest

from smartpanel.state.model import (IDLE, LEVEL_CRITICAL, LEVEL_OK, LEVEL_WARNING,
                                    PHASE_JOURNALING, PHASE_LAUNCHING, RUNNING, UNKNOWN,
                                    WAITING, Loading, PanelState, Session, SwapState)
from smartpanel.ui import shellmodel as sm


# ── routing ──
@pytest.mark.parametrize("target,expected", [
    ("", ("overview", None)),
    (None, ("overview", None)),
    ("system", ("system", None)),
    ("Sessions", ("sessions", None)),
    ("sessions:board", ("sessions", "board")),
    ("journal:2026-11-17-1202-tacomap", ("journal", "2026-11-17-1202-tacomap")),
    ("journal:a:b", ("journal", "a:b")),
    ("journal:", ("journal", None)),
    ("nonsense", ("overview", None)),
    ("nonsense:board", ("overview", None)),
])
def test_parse_page(target, expected):
    assert sm.parse_page(target) == expected


def test_every_page_has_a_title_and_symbolic_icon():
    from smartpanel.state.model import PAGES
    for p in PAGES:
        assert sm.PAGE_TITLES[p]
        assert sm.PAGE_ICONS[p].endswith("-symbolic")


# ── activity words (never colour-only) ──
def test_activity_meta_words_match_the_prototype():
    assert sm.activity_meta(WAITING).label == "Needs you"
    assert sm.activity_meta(RUNNING).label == "Running"
    assert sm.activity_meta(IDLE).label == "Idle"
    assert sm.activity_meta(PHASE_JOURNALING).label == "Journaling"
    assert sm.activity_meta(PHASE_LAUNCHING).label == "Starting"
    assert sm.activity_meta("bogus") == sm.activity_meta(UNKNOWN)
    glyphs = [sm.activity_meta(s).glyph for s in (WAITING, RUNNING, IDLE, PHASE_JOURNALING)]
    assert len(set(glyphs)) == 4  # distinct shapes, not just colours


def test_display_state_prefers_panel_phase():
    s = Session("claude-x", activity=RUNNING)
    assert sm.display_state(s) == RUNNING
    s.phase = PHASE_JOURNALING
    assert sm.display_state(s) == PHASE_JOURNALING


def test_level_chip():
    assert sm.level_chip(LEVEL_OK) == ("Fine", "ok")
    assert sm.level_chip(LEVEL_WARNING) == ("Warning", "warn")
    assert sm.level_chip(LEVEL_CRITICAL) == ("Critical", "crit")
    assert sm.level_chip(None) == ("Not configured", "na")
    assert sm.level_chip(None, na_text="No sensor") == ("No sensor", "na")
    assert sm.level_chip(7)[1] == "crit"


# ── nav badges ──
def _state(waiting=0, running=0, tmux=True, swap=12.0, level=LEVEL_OK):
    s = PanelState()
    s.sessions.items = ([Session(f"claude-w{i}", activity=WAITING) for i in range(waiting)]
                        + [Session(f"claude-r{i}", activity=RUNNING) for i in range(running)])
    s.sessions.tmux_ok = tmux
    s.system.swap = SwapState(percent=swap, used_gb=None if swap is None else swap / 100 * 8,
                              total_gb=8.0, level=level)
    return s


def test_sessions_badge_counts_waiting_in_amber():
    assert sm.nav_badge("sessions", _state()) is None
    assert sm.nav_badge("sessions", _state(running=3)) is None
    b = sm.nav_badge("sessions", _state(waiting=2, running=1))
    assert (b.text, b.css) == ("2", "amber")
    assert "2 sessions need you" == b.a11y
    assert sm.nav_badge("sessions", _state(waiting=1)).a11y == "1 session needs you"


def test_sessions_badge_red_bang_when_tmux_missing():
    b = sm.nav_badge("sessions", _state(waiting=2, tmux=False))
    assert (b.text, b.css) == ("!", "red")


def test_system_badge_by_swap_level():
    assert sm.nav_badge("system", _state(level=LEVEL_OK)) is None
    w = sm.nav_badge("system", _state(swap=66, level=LEVEL_WARNING))
    assert (w.text, w.css) == ("·", "amber")
    c = sm.nav_badge("system", _state(swap=93, level=LEVEL_CRITICAL))
    assert (c.text, c.css) == ("!", "red")
    assert sm.nav_badge("system", _state(swap=None, level=LEVEL_CRITICAL)) is None


def test_other_pages_have_no_badge():
    for page in ("overview", "spend", "journal", "settings"):
        assert sm.nav_badge(page, _state(waiting=3, swap=93, level=LEVEL_CRITICAL)) is None


# ── swap banner (SP-0106) ──
def test_banner_copy_warning():
    s = _state(swap=66, level=LEVEL_WARNING)
    s.system.swap.used_gb = 5.3
    b = sm.swap_banner(s, "overview")
    assert b.text == ("Swap is filling up · 66% used (5.3 of 8 GB). "
                      "Close something heavy before the system stalls.")
    assert b.level == LEVEL_WARNING and b.show_action


def test_banner_copy_critical_and_hidden_action_on_system():
    b = sm.swap_banner(_state(swap=93, level=LEVEL_CRITICAL), "system")
    assert b.headline == "Swap is critically full"
    assert b.text.startswith("Swap is critically full · 93% used (7.4 of 8 GB)")
    assert not b.show_action


def test_banner_hidden_at_ok_and_without_swap():
    assert sm.swap_banner(_state(level=LEVEL_OK), "overview") is None
    assert sm.swap_banner(_state(swap=None, level=LEVEL_WARNING), "overview") is None


def test_banner_without_sizes():
    s = _state(swap=70, level=LEVEL_WARNING)
    s.system.swap.used_gb = None
    assert "(" not in sm.swap_banner(s, "spend").text


# ── footer (SP-2106) ──
def test_footer_updated_ago():
    s = PanelState()
    s.system.updated_at = 100.0
    s.spend.updated_at = 97.0
    assert sm.footer_status(s, now=103.4) == "Updated 3 s ago"
    assert sm.footer_status(s, now=100 + 125) == "Updated 2 min ago"
    assert sm.footer_status(s, now=100 + 7300) == "Updated 2 h ago"


def test_footer_reading_transcripts_wins():
    s = PanelState()
    s.system.updated_at = 100.0
    s.app.loading = Loading(done=120, total=1284)
    assert sm.footer_status(s, now=101) == "Reading transcripts… 120/1,284"
    s.app.loading = Loading()
    assert sm.footer_status(s, now=101) == "Reading transcripts…"


def test_footer_before_any_data():
    assert sm.footer_status(PanelState(), now=5) == "Waiting for data…"


def test_footer_version_line(tmp_path):
    osr = tmp_path / "os-release"
    osr.write_text('NAME="Ubuntu"\nVERSION_ID="26.04"\nPRETTY_NAME="Ubuntu 26.04.1 LTS"\n')
    assert sm.os_label(osr) == "Ubuntu 26.04"
    assert sm.os_label(tmp_path / "missing") == ""
    s = PanelState()
    s.app.version = "0.4.0"
    assert sm.footer_version(s, "Ubuntu 26.04") == "v0.4.0 · Ubuntu 26.04"
    s.app.demo = "busy"
    assert sm.footer_version(s, "") == "v0.4.0 · demo busy"


# ── toasts (SP-2502) ──
def test_toast_model_plain_and_error_timeouts():
    t = sm.toast_model({"text": "Copied"})
    assert (t.text, t.error, t.timeout, t.action_label) == ("Copied", False, 4, None)
    e = sm.toast_model({"text": "Install tmux first", "error": True})
    assert e.error and e.timeout == 6


def test_toast_model_action_routes_to_a_page():
    t = sm.toast_model({"text": "Journaled", "action": {"label": "View", "page": "journal"}})
    assert (t.action_label, t.action_page) == ("View", "journal")


@pytest.mark.parametrize("data", [None, {}, {"text": ""}, {"text": "   "}])
def test_toast_model_nothing_to_say(data):
    assert sm.toast_model(data) is None


def test_toast_model_ignores_malformed_action():
    t = sm.toast_model({"text": "x", "action": "View"})
    assert t.action_label is None


# ── window state ──
def test_window_state_round_trip(tmp_path):
    path = tmp_path / "sub" / "window.json"
    ws = sm.WindowState(width=1000, height=720, maximized=True, page="spend")
    assert sm.save_window_state(path, ws)
    assert sm.load_window_state(path) == ws


def test_window_state_defaults_on_garbage(tmp_path):
    path = tmp_path / "window.json"
    assert sm.load_window_state(path) == sm.WindowState()
    path.write_text("{not json")
    assert sm.load_window_state(path) == sm.WindowState()
    path.write_text(json.dumps({"width": 10, "height": "x", "page": "bogus"}))
    assert sm.load_window_state(path) == sm.WindowState()
    path.write_text(json.dumps([1, 2]))
    assert sm.load_window_state(path) == sm.WindowState()
    assert sm.load_window_state(None) == sm.WindowState()


def test_window_state_save_failure_is_not_fatal(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    assert not sm.save_window_state(blocker / "window.json", sm.WindowState())
    assert not sm.save_window_state(None, sm.WindowState())


def test_window_state_path_honours_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert sm.window_state_path() == tmp_path / "smartpanel" / "window.json"
