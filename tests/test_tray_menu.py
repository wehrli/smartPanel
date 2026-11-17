"""SP-2001/2003, SP-0105, SP-0206, SP-0305, SP-0603, SP-0808, SP-1207, SP-1214, SP-2606."""
from __future__ import annotations

import pytest

from smartpanel.state.model import PanelState
from smartpanel.tray import menu
from smartpanel.tray.menu import ROW_KEYS, build_rows, fmt_age, fmt_shortcut, invoke
from tests.fixtures import tray_states as fx

NOW = fx.NOW


def rows(state, config=None):
    return {r.key: r for r in build_rows(state, config, NOW)}


def visible(state, config=None):
    return [r for r in build_rows(state, config, NOW) if r.visible and not r.separator]


def labels(state, config=None):
    return [r.label for r in visible(state, config)]


class RecordingActions:
    """Implements the Actions verbs the menu uses and records the calls."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def rec(*args):
            self.calls.append((name, *args))
        return rec


# ── structure ──

def test_busy_menu_matches_prototype():
    assert labels(fx.busy()) == [
        "System", "CPU 23%", "Memory 68%", "Swap 12%",
        "Claude · today", "Spent $23.84", "Burn rate $3.10/h",
        "Sessions · 4 · 1 waiting",
        "❓ Fix swap alert hysteresis  1h02",
        "▶ Truck search filters  38m",
        "▶ Stripe webhook retries  12m",
        "· Migrate goose 0023  3h10",
        "New session…", "Command palette…   Ctrl+Alt+Space", "Open dashboard",
        "Desktop HUD", "Quit smartPanel",
    ]


@pytest.mark.parametrize("name", list(fx.SCENARIOS))
def test_row_set_is_fixed(name):
    """SP-2002: the same keys, ids and order in every state."""
    keys = [r.key for r in build_rows(fx.SCENARIOS[name](), None, NOW)]
    assert keys == list(ROW_KEYS)
    ids = [r.id for r in build_rows(fx.SCENARIOS[name](), None, NOW)]
    assert ids == list(range(1, len(ROW_KEYS) + 1))


def test_headers_disabled():
    r = rows(fx.busy())
    assert not r["sys.header"].enabled and not r["claude.header"].enabled
    assert not r["sessions.header"].enabled


def test_fresh_state_renders():
    ls = labels(PanelState())
    assert "CPU —" in ls and "Swap none" in ls and "Spent —" in ls and "No sessions" in ls


# ── system ──

@pytest.mark.parametrize("make,text", [(fx.busy, "Swap 12%"), (fx.swap_warn, "Swap 66% high"),
                                       (fx.swap_crit, "Swap 88% critical"),
                                       (fx.noswap, "Swap none")])
def test_swap_row(make, text):
    assert rows(make())["sys.swap"].label == text


def test_gpu_rows_hidden_without_gpu():
    r = rows(fx.busy())
    assert not r["sys.gpu"].visible and not r["sys.vram"].visible


def test_gpu_rows_shown_with_gpu():
    r = rows(fx.gpu())
    assert r["sys.gpu"].visible and r["sys.gpu"].label == "GPU 41% · 58 °C"
    assert r["sys.vram"].visible and r["sys.vram"].label == "VRAM 3.1 of 8 GB"


def test_system_rows_open_system_page():
    a = RecordingActions()
    invoke(a, rows(fx.busy())["sys.cpu"].action)
    assert a.calls == [("open_dashboard", "system")]


# ── Claude · today ──

def test_loading_rows():
    r = rows(fx.loading())
    assert r["claude.loading"].visible and r["claude.loading"].label == "Reading transcripts… 812/1284"
    assert not r["claude.spent"].visible and not r["claude.burn"].visible
    assert not r["claude.first_run"].visible


def test_first_run_rows():
    r = rows(fx.first())
    assert r["claude.first_run"].visible and r["claude.first_run"].label == "No transcripts yet"
    assert not r["claude.spent"].visible and not r["claude.loading"].visible


def test_unknown_burn_is_dash():
    st = fx.busy()
    st.spend.burn_per_hour = None
    assert rows(st)["claude.burn"].label == "Burn rate —"


# ── sessions ──

def test_empty_sessions():
    r = rows(fx.empty())
    assert r["sessions.header"].label == "Sessions · 0"
    assert r["sessions.none"].visible and not r["sessions.none"].enabled
    assert not any(r[f"sessions.{i}"].visible for i in range(4))
    assert not r["sessions.more"].visible


def test_notmux():
    r = rows(fx.notmux())
    assert r["sessions.header"].label == "Sessions · tmux missing"
    assert r["sessions.tmux"].visible and r["sessions.tmux"].action == ("page", "sessions")
    assert not r["sessions.none"].visible
    assert not r["act.new"].enabled            # session actions disabled


def test_many_sessions_more_row():
    r = rows(fx.many())
    assert r["sessions.header"].label == "Sessions · 10 · 2 waiting"
    assert all(r[f"sessions.{i}"].visible for i in range(4))
    assert r["sessions.more"].visible and r["sessions.more"].label == "+6 more…"
    a = RecordingActions()
    invoke(a, r["sessions.more"].action)
    assert a.calls == [("open_dashboard", "sessions")]


def test_transitional_rows_disabled():
    r = rows(fx.transitional())
    assert r["sessions.0"].enabled
    assert r["sessions.1"].label.startswith("⏳ wishlist") and not r["sessions.1"].enabled
    assert r["sessions.2"].label == "◌ netConfigs  starting…" and not r["sessions.2"].enabled


def test_session_click_focuses():
    a = RecordingActions()
    invoke(a, rows(fx.busy())["sessions.1"].action)
    assert a.calls == [("focus_session", "claude-tacomap")]


def test_long_title_truncated():
    st = fx.busy()
    st.sessions.items[0].title = "x" * 80
    label = rows(st)["sessions.0"].label
    assert "…" in label and len(label) < 60


# ── actions ──

def test_pin_row_only_with_ticker():
    assert not rows(fx.busy())["act.pin"].visible
    r = rows(fx.busy(), {"bar.ticker": True})
    assert r["act.pin"].visible and r["act.pin"].label == "Pin current reading"
    assert r["act.pin"].action == ("pin", True)
    st = fx.busy()
    st.app.ticker_pinned = True
    r = rows(st, {"bar.ticker": True})
    assert r["act.pin"].label == "Resume rotation" and r["act.pin"].action == ("pin", False)


def test_ticker_setting_from_state_settings():
    st = fx.busy()
    st.settings.values = {"bar.ticker": True}
    assert rows(st)["act.pin"].visible


def test_hud_checkmark():
    r = rows(fx.busy())
    assert r["act.hud"].toggle is False and r["act.hud"].action == ("hud", True)
    r = rows(fx.busy(), {"hud.enabled": True})
    assert r["act.hud"].toggle is True and r["act.hud"].action == ("hud", False)


def test_palette_shortcut_from_config():
    r = rows(fx.busy(), {"palette.shortcut": "<Primary>k"})
    assert r["act.palette"].label == "Command palette…   Ctrl+K"


@pytest.mark.parametrize("key,call", [
    ("act.new", ("request_new_session",)), ("act.palette", ("open_palette",)),
    ("act.dashboard", ("open_dashboard", "")), ("act.hud", ("set_hud", True)),
    ("act.quit", ("quit",)),
])
def test_action_rows_invoke(key, call):
    a = RecordingActions()
    assert invoke(a, rows(fx.busy())[key].action)
    assert a.calls == [call]


def test_invoke_none():
    assert not invoke(RecordingActions(), None)


# ── formatting ──

@pytest.mark.parametrize("mins,text", [(0.2, "now"), (38, "38m"), (62, "1h02"), (190, "3h10")])
def test_fmt_age(mins, text):
    assert fmt_age(NOW - mins * 60, NOW) == text


def test_fmt_age_unknown():
    assert fmt_age(0.0, NOW) == ""


@pytest.mark.parametrize("accel,text", [("<Ctrl><Alt>space", "Ctrl+Alt+Space"),
                                        ("<Super>p", "Super+P"), ("", ""), (None, "")])
def test_fmt_shortcut(accel, text):
    assert fmt_shortcut(accel) == text


# ── tree ──

def test_to_node_tree():
    node = menu.build_menu(fx.busy(), {"hud.enabled": True}, NOW)
    assert node.id == 0 and node.props["children-display"] == "submenu"
    by_id = {c.id: c for c in node.children}
    sep = by_id[menu.ROW_IDS["sep.system"]]
    assert sep.props["type"] == "separator"
    hud = by_id[menu.ROW_IDS["act.hud"]]
    assert hud.props["toggle-type"] == "checkmark" and hud.props["toggle-state"] == 1
    assert by_id[menu.ROW_IDS["sys.header"]].props["enabled"] is False


def test_underscores_escaped_in_labels():
    st = fx.busy()
    st.sessions.items[0].title = "fix my_var"
    node = menu.build_menu(st, None, NOW)
    lbl = next(c for c in node.children if c.id == menu.ROW_IDS["sessions.0"]).props["label"]
    assert "my__var" in lbl
