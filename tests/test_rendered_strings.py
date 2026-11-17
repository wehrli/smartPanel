"""Rendered strings for every demo scenario, without GTK (SP-0013).

Builds each scenario's `PanelState` (demo.build_state) and asserts on exactly what
the user reads: the top-bar label (A1 text, A5 ticker, loading), its accessible
description, and the visible bar-menu rows. The expected strings are the
prototype's (docs/design/prototype.html: `textHTML`, `tickerItems`, `tickerHTML`,
`barInner`, `menuHTML`, `fmtAge`), with the scenario data of `DEFAULTS()` /
`busySessions()`; deliberate differences are noted inline.

A change here is a change the user sees: update the prototype or the backlog first.
"""
from __future__ import annotations

import pytest

from smartpanel import demo
from smartpanel.theme.model import Theme
from smartpanel.tray import barspec
from smartpanel.tray.barspec import compose_bar
from smartpanel.tray.menu import build_menu, build_rows

NOW = 1_763_389_920.0          # fixed clock: session ages are relative to it

TEXT = {"bar.text": True, "bar.ticker": False}
TICKER = {"bar.text": True, "bar.ticker": True}


# ───────────────────────── harness ─────────────────────────

def state(scenario: str):
    return demo.build_state(scenario, now=NOW)


def bar_label(scenario: str, cfg=TEXT, **app) -> str:
    st = state(scenario)
    for k, v in app.items():
        setattr(st.app, k, v)
    return compose_bar(st, cfg).label


def menu_rows(scenario: str, cfg=None) -> list[str]:
    """Visible, non-separator rows as the user reads them ('label [off]' if disabled)."""
    out = []
    for r in build_rows(state(scenario), cfg, now=NOW):
        if r.separator or not r.visible:
            continue
        label = r.label
        if r.toggle is not None:
            label = ("☑ " if r.toggle else "☐ ") + label
        out.append(label if r.enabled else f"{label} [off]")
    return out


SYSTEM_OK = ["System [off]", "CPU 23%", "Memory 68%", "Swap 12%"]
CLAUDE_BUSY = ["Claude · today [off]", "Spent $23.84", "Burn rate $3.10/h"]
BUSY_SESSIONS = [
    "❓ Fix swap alert hysteresis  1h02",
    "▶ Truck search filters  38m",
    "▶ Stripe webhook retries  12m",
    "· Migrate goose 0023  3h10",
]
# The prototype's palette row reads 'Ctrl K' (its in-page shortcut); the real menu
# shows the configured global shortcut, `palette.shortcut` = <Ctrl><Alt>space.
# Its 'Quit smartPanel' is a disabled no-op only because a web page can't quit.
ACTIONS = ["New session…", "Command palette…   Ctrl+Alt+Space", "Open dashboard",
           "☐ Desktop HUD", "Quit smartPanel"]


# ───────────────────────── A1 text label ─────────────────────────

@pytest.mark.parametrize("scenario, label", [
    ("busy",   "12 · 4/1 · 23.84"),
    ("calm",   "12 · 4 · 8.10"),
    ("alert",  "⚠88 · 4/2 · 41.20"),
    ("empty",  "12 · 0 · 23.84"),
    ("many",   "12 · 9/2 · 23.84"),
    ("cold",   "reading 0/1284"),
    ("first",  "12 · 0 · —"),
    ("notmux", "12 · ! · 23.84"),
    ("noswap", "— · 4/1 · 23.84"),
])
def test_text_label(scenario, label):
    assert bar_label(scenario) == label


def test_loading_label_follows_progress():
    from smartpanel.state.model import Loading
    assert bar_label("cold", loading=Loading(done=812, total=1284)) == "reading 812/1284"


def test_nothing_but_the_icon_when_text_and_ticker_are_off():
    assert bar_label("busy", {"bar.text": False, "bar.ticker": False}) == ""


# ───────────────────────── A5 ticker ─────────────────────────

@pytest.mark.parametrize("index, label", [
    (0, "$23.84 today ●○○○"),
    (1, "4 sessions · 1 waiting ○●○○"),
    (2, "Swap 12% ○○●○"),
    (3, "CPU 23% · 61 °C ○○○●"),
    (4, "$23.84 today ●○○○"),          # wraps around
])
def test_ticker_rotation_busy(index, label):
    assert bar_label("busy", TICKER, ticker_index=index) == label


@pytest.mark.parametrize("scenario, index, label", [
    ("calm",   1, "4 sessions ○●○○"),
    ("alert",  0, "⚠ Swap 88% pinned by alert"),
    ("alert",  3, "⚠ Swap 88% pinned by alert"),
    ("first",  0, "No spend yet ●○○○"),
    ("notmux", 1, "tmux missing ○●○○"),
    ("noswap", 2, "Swap — ○○●○"),
    ("cold",   0, "reading 0/1284"),      # loading wins over the ticker too
])
def test_ticker_other_scenarios(scenario, index, label):
    assert bar_label(scenario, TICKER, ticker_index=index) == label


def test_ticker_pinned_suffix():
    assert bar_label("busy", TICKER, ticker_index=2, ticker_pinned=True) == "Swap 12% ○○●○ pinned"


# ───────────────────────── A6 strip / A4 graphs (what the icon draws) ─────────────────────────

@pytest.mark.parametrize("scenario, strip, more", [
    ("busy",   ("waiting", "running", "running", "idle"), 0),
    ("alert",  ("waiting", "waiting", "running", "running"), 0),
    ("many",   ("waiting", "waiting", "running", "running", "running", "idle"), 3),
    ("empty",  (), 0),
    ("notmux", (), 0),
])
def test_session_strip(scenario, strip, more):
    icon = compose_bar(state(scenario), TEXT).icon
    assert icon is not None and icon.graphs
    assert (icon.strip, icon.strip_more) == (strip, more)


# ───────────────────────── accessible description ─────────────────────────

@pytest.mark.parametrize("scenario, desc", [
    ("busy", "smartPanel: swap 12%, 4 sessions, 1 waiting for you, 2 running, "
             "$23.84 spent today, CPU 23%, memory 68%"),
    ("alert", "smartPanel: swap 88% (critical), 4 sessions, 2 waiting for you, 2 running, "
              "$41.20 spent today, CPU 92%, memory 97%"),
    ("first", "smartPanel: swap 12%, 0 sessions, no Claude spend yet, CPU 23%, memory 68%"),
    ("notmux", "smartPanel: swap 12%, tmux is not installed, $23.84 spent today, "
               "CPU 23%, memory 68%"),
    ("noswap", "smartPanel: no swap configured, 4 sessions, 1 waiting for you, 2 running, "
               "$23.84 spent today, CPU 23%, memory 68%"),
    ("cold", "smartPanel: swap 12%, 4 sessions, 1 waiting for you, 2 running, "
             "reading transcripts 0 of 1284, CPU 23%, memory 68%"),
])
def test_accessible_description(scenario, desc):
    assert compose_bar(state(scenario), TEXT).accessible_desc == desc


# ───────────────────────── theme template (SP-1904) ─────────────────────────

def test_custom_theme_template_replaces_the_text():
    theme = Theme(top_bar_format="{SWAP.STATUS} · {SESSIONS.STATUS} · ${CLAUDE.COST_TODAY:,.2f}",
                  top_bar_format_explicit=True)
    assert barspec.render_template(theme, state("busy")) == "🔃12% · 🧩4 ❓1 ▶2 · $23.84"
    assert barspec.render_template(theme, state("alert")) == "🔴🔃88% · 🧩4 ❓2 ▶2 · $41.20"


# ───────────────────────── bar menu ─────────────────────────

MENUS = {
    "busy": [*SYSTEM_OK, *CLAUDE_BUSY,
             "Sessions · 4 · 1 waiting [off]", *BUSY_SESSIONS, *ACTIONS],
    "calm": ["System [off]", "CPU 6%", "Memory 41%", "Swap 12%",
             "Claude · today [off]", "Spent $8.10", "Burn rate $0.40/h",
             "Sessions · 4 [off]",
             "· Fix swap alert hysteresis  1h02", "· Truck search filters  38m",
             "· Stripe webhook retries  12m", "· Migrate goose 0023  3h10",
             *ACTIONS],
    "alert": ["System [off]", "CPU 92%", "Memory 97%", "Swap 88% critical",
              "Claude · today [off]", "Spent $41.20", "Burn rate $7.80/h",
              "Sessions · 4 · 2 waiting [off]",
              "❓ Fix swap alert hysteresis  1h02", "❓ Migrate goose 0023  3h10",
              "▶ Truck search filters  38m", "▶ Stripe webhook retries  12m",
              *ACTIONS],
    "empty": [*SYSTEM_OK, *CLAUDE_BUSY, "Sessions · 0 [off]", "No sessions [off]", *ACTIONS],
    "many": [*SYSTEM_OK, *CLAUDE_BUSY, "Sessions · 9 · 2 waiting [off]",
             "❓ Fix swap alert hysteresis  1h02", "❓ Gift list sharing links  8m",
             "▶ Truck search filters  38m", "▶ Stripe webhook retries  12m",
             "+5 more…", *ACTIONS],
    "cold": [*SYSTEM_OK, "Claude · today [off]", "Reading transcripts… 0/1284 [off]",
             "Sessions · 4 · 1 waiting [off]", *BUSY_SESSIONS, *ACTIONS],
    "first": [*SYSTEM_OK, "Claude · today [off]", "No transcripts yet [off]",
              "Sessions · 0 [off]", "No sessions [off]", *ACTIONS],
    # The prototype just drops the session rows; the real menu says why and how to fix.
    "notmux": [*SYSTEM_OK, *CLAUDE_BUSY, "Sessions · tmux missing [off]",
               "tmux isn't installed — how to fix…", "New session… [off]", *ACTIONS[1:]],
    "noswap": ["System [off]", "CPU 23%", "Memory 68%", "Swap none", *CLAUDE_BUSY,
               "Sessions · 4 · 1 waiting [off]", *BUSY_SESSIONS, *ACTIONS],
}


def test_every_scenario_has_an_expected_menu():
    assert set(MENUS) == set(demo.SCENARIOS)


@pytest.mark.parametrize("scenario", sorted(MENUS))
def test_menu_rows(scenario):
    assert menu_rows(scenario) == MENUS[scenario]


def test_menu_ticker_rows():
    rows = menu_rows("busy", {"bar.ticker": True})
    assert rows[-3:] == ["Pin current reading", "☐ Desktop HUD", "Quit smartPanel"]


def test_menu_hud_checkmark_follows_the_setting():
    assert menu_rows("busy", {"hud.enabled": True})[-2] == "☑ Desktop HUD"


def test_dbusmenu_tree_carries_the_same_labels():
    """What goes over the wire (build_menu) is what build_rows says, row for row."""
    st = state("many")
    rows = {r.id: r for r in build_rows(st, None, now=NOW)}
    tree = build_menu(st, None, now=NOW)
    seen = 0
    for node in tree.walk():
        if node.id == 0 or node.props.get("type") == "separator":
            continue
        row = rows[node.id]
        assert node.props["label"] == row.label      # no underscores in demo titles
        assert node.props["visible"] == row.visible
        assert node.props["enabled"] == row.enabled
        seen += 1
    assert seen == sum(1 for r in rows.values() if not r.separator)
