"""SP-1901..1904, SP-0104, SP-0805, SP-1205, SP-1206, SP-1214, SP-2606, SP-2608: bar model."""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from smartpanel.state.model import LEVEL_WARNING, PanelState
from smartpanel.theme.loader import load_theme
from smartpanel.theme.model import Theme
from smartpanel.tray import barspec
from smartpanel.tray.barspec import compose_bar
from tests.fixtures import tray_states as fx


def cfg(strip=True, graphs=True, text=True, ticker=False, **extra):
    d = {"bar.strip": strip, "bar.graphs": graphs, "bar.text": text, "bar.ticker": ticker}
    d.update(extra)
    return d


# ── A1 numbers ──

def test_a1_busy():
    assert compose_bar(fx.busy(), cfg()).label == "12 · 4/1 · 23.84"


def test_a1_no_waiting_drops_slash():
    assert compose_bar(fx.empty(), cfg()).label == "12 · 0 · 23.84"


@pytest.mark.parametrize("make,swap", [(fx.swap_warn, "⚠66"), (fx.swap_crit, "⚠88"),
                                       (fx.noswap, "—")])
def test_a1_swap_segment(make, swap):
    assert compose_bar(make(), cfg()).label.split(" · ")[0] == swap


def test_a1_tmux_missing():
    assert compose_bar(fx.notmux(), cfg()).label == "12 · ! · 23.84"


def test_a1_first_run_spend_dash():
    assert compose_bar(fx.first(), cfg()).label == "12 · 0 · —"


def test_a1_rounds_half_up():
    st = fx.busy()
    st.system.swap.percent = 12.5
    assert compose_bar(st, cfg()).label.startswith("13 ·")


def test_noswap_never_alerts_even_with_stale_level():
    st = fx.noswap()
    st.system.swap.level = LEVEL_WARNING      # a level without a percent is meaningless
    spec = compose_bar(st, cfg(ticker=True))
    assert not spec.ticker_pinned_by_alert
    assert compose_bar(st, cfg()).label.startswith("— ·")


# ── combinations ──

@pytest.mark.parametrize("strip,graphs,text,ticker",
                         list(itertools.product([False, True], repeat=4)))
def test_every_combination(strip, graphs, text, ticker):
    spec = compose_bar(fx.busy(), cfg(strip, graphs, text, ticker))
    if strip or graphs:
        assert spec.icon is not None
        assert spec.icon.graphs == graphs
        assert bool(spec.icon.strip) == strip
    else:
        assert spec.icon is None and spec.icon_name == barspec.ICON_PLAIN
    if ticker:
        assert spec.ticker and "●" in spec.label
    elif text:
        assert spec.label == "12 · 4/1 · 23.84"
    else:
        assert spec.label == ""
    assert spec.accessible_desc.startswith("smartPanel:")
    if spec.label:
        assert len(spec.guide) >= len(spec.label)


def test_nothing_enabled_falls_back_to_plain_icon():
    spec = compose_bar(fx.busy(), cfg(False, False, False, False))
    assert spec.icon is None and spec.label == "" and spec.icon_name == barspec.ICON_PLAIN


def test_defaults_when_config_missing():
    spec = compose_bar(fx.busy(), None)     # strip+graphs+text on, ticker off
    assert spec.icon.strip and spec.icon.graphs and spec.label == "12 · 4/1 · 23.84"


def test_config_object_with_get():
    class C:
        def get(self, k):
            return {"bar.ticker": True}.get(k)
    assert compose_bar(fx.busy(), C()).ticker


# ── strip (A6) ──

def test_strip_kinds_in_display_order():
    spec = compose_bar(fx.transitional(), cfg())
    assert spec.icon.strip == ("waiting", "journaling", "launching", "running")


def test_strip_many_caps_at_six():
    spec = compose_bar(fx.many(), cfg())
    assert len(spec.icon.strip) == 6 and spec.icon.strip_more == 4


@pytest.mark.parametrize("make", [fx.empty, fx.notmux])
def test_strip_hidden_without_sessions_or_tmux(make):
    spec = compose_bar(make(), cfg(strip=True, graphs=False))
    assert spec.icon is None      # nothing graphical left -> plain icon


# ── graphs (A4) ──

def test_graphs_take_last_ten_cpu_samples_and_levels():
    st = fx.swap_crit()
    icon = compose_bar(st, cfg(strip=False)).icon
    assert icon.cpu == tuple(st.system.cpu.history[-10:])
    assert icon.mem_pct == 68.0 and icon.swap_pct == 88.0 and icon.swap_level == 2


def test_graphs_no_swap_empty_meter_ok_level():
    icon = compose_bar(fx.noswap(), cfg(strip=False)).icon
    assert icon.swap_pct is None and icon.swap_level == 0


def test_graphs_without_history_use_current_percent():
    st = fx.busy()
    st.system.cpu.history = []
    assert compose_bar(st, cfg(strip=False)).icon.cpu == (23.0,)


def test_icon_spec_is_hashable_cache_key():
    a = compose_bar(fx.busy(), cfg()).icon
    b = compose_bar(fx.busy(), cfg()).icon
    assert a == b and hash(a) == hash(b)


# ── ticker (A5, SP-1902) ──

def test_ticker_rotation_items_and_dots():
    st = fx.busy()
    expected = ["$23.84 today ●○○○", "4 sessions · 1 waiting ○●○○",
                "Swap 12% ○○●○", "CPU 23% · 61 °C ○○○●"]
    for i, text in enumerate(expected):
        st.app.ticker_index = i
        assert compose_bar(st, cfg(ticker=True)).label == text
    st.app.ticker_index = 5
    assert compose_bar(st, cfg(ticker=True)).label == expected[1]


def test_ticker_pinned_suffix():
    st = fx.busy()
    st.app.ticker_pinned = True
    assert compose_bar(st, cfg(ticker=True)).label == "$23.84 today ●○○○ pinned"


@pytest.mark.parametrize("make,text", [(fx.swap_warn, "⚠ Swap 66% pinned by alert"),
                                       (fx.swap_crit, "⚠ Swap 88% pinned by alert")])
def test_ticker_alert_pins_swap(make, text):
    spec = compose_bar(make(), cfg(ticker=True))
    assert spec.label == text and spec.ticker_pinned_by_alert


def test_ticker_states():
    st = fx.first()
    assert compose_bar(st, cfg(ticker=True)).label.startswith("No spend yet")
    st = fx.notmux()
    st.app.ticker_index = 1
    assert compose_bar(st, cfg(ticker=True)).label.startswith("tmux missing")
    st = fx.noswap()
    st.app.ticker_index = 2
    assert compose_bar(st, cfg(ticker=True)).label.startswith("Swap —")
    st = fx.busy()
    st.system.cpu.temp_c = None
    st.app.ticker_index = 3
    assert compose_bar(st, cfg(ticker=True)).label == "CPU 23% ○○○●"


def test_ticker_one_session_singular():
    st = fx.busy()
    st.sessions.items = st.sessions.items[3:]
    st.app.ticker_index = 1
    assert compose_bar(st, cfg(ticker=True)).label.startswith("1 session ")


# ── loading (SP-1906) ──

def test_loading_label_and_spinner():
    spec = compose_bar(fx.loading(), cfg())
    assert spec.loading and spec.label == "reading 812/1284"
    assert spec.icon is None and spec.icon_name == barspec.ICON_LOADING
    assert len(spec.guide) >= len(spec.label)


# ── guide (SP-1903, SP-0104) ──

def test_text_guide_covers_alert_at_100():
    st = fx.busy()
    guide = compose_bar(st, cfg()).guide
    st.system.swap.percent, st.system.swap.level = 100.0, 2
    st.sessions.items = fx.many().sessions.items        # 10 sessions, 2 waiting
    st.spend.cost_today = 999.99
    label = compose_bar(st, cfg()).label
    assert label == "⚠100 · 10/2 · 999.99"
    assert len(label) <= len(guide)


def test_guide_is_stable_while_values_change():
    a, b = fx.busy(), fx.busy()
    b.system.swap.percent, b.system.swap.level = 99.0, 2
    b.spend.cost_today = 1.5
    b.app.ticker_index = 2
    assert compose_bar(a, cfg()).guide == compose_bar(b, cfg()).guide
    assert compose_bar(a, cfg(ticker=True)).guide == compose_bar(b, cfg(ticker=True)).guide


def test_ticker_guide_covers_every_reading():
    for make in fx.SCENARIOS.values():
        st = make()
        if st.app.loading:
            continue
        guide = compose_bar(st, cfg(ticker=True)).guide
        for i in range(4):
            st.app.ticker_index = i
            st.app.ticker_pinned = True
            assert len(compose_bar(st, cfg(ticker=True)).label) <= len(guide)


# ── every scenario renders ──

@pytest.mark.parametrize("name", list(fx.SCENARIOS))
def test_every_scenario_composes(name):
    spec = compose_bar(fx.SCENARIOS[name](), cfg())
    assert isinstance(spec.label, str) and spec.accessible_desc


def test_fresh_state_renders():
    spec = compose_bar(PanelState(), cfg())
    assert spec.label == "— · 0 · —"
    assert spec.icon is not None and spec.icon.cpu == ()


def test_accessible_desc_mentions_states():
    assert "tmux is not installed" in compose_bar(fx.notmux(), cfg()).accessible_desc
    assert "no swap configured" in compose_bar(fx.noswap(), cfg()).accessible_desc
    assert "(critical)" in compose_bar(fx.swap_crit(), cfg()).accessible_desc
    assert "1 waiting for you" in compose_bar(fx.busy(), cfg()).accessible_desc
    assert "812 of 1284" in compose_bar(fx.loading(), cfg()).accessible_desc


# ── theme template precedence (SP-1904) ──

def test_no_theme_uses_composition():
    assert barspec.render_template(None, fx.busy()) is None


def test_shipped_presets_switch_to_composition():
    presets = Path(barspec.__file__).resolve().parent.parent / "theme" / "presets"
    for name in ("default", "claude-focus"):
        assert barspec.theme_template(load_theme(presets / f"{name}.yaml")) is None


def test_loader_fallback_counts_as_no_template():
    assert barspec.theme_template(Theme()) is None


def test_custom_template_wins():
    theme = Theme(top_bar_format="{SWAP.STATUS} · ${CLAUDE.COST_TODAY:.2f} · {SESSIONS.COUNT}")
    label = barspec.render_template(theme, fx.busy())
    assert label == "🔃12% · $23.84 · 4"
    spec = compose_bar(fx.busy(), cfg(), template_label=label)
    assert spec.label == label and spec.icon is not None and not spec.ticker


def test_custom_template_gets_alert_badge_prefix():
    theme = Theme(top_bar_format="⚡{CPU.PERCENTAGE:.0f}% {MEMORY.PERCENTAGE:.0f}%",
                  author="x")
    theme.top_bar_format_explicit = True       # type: ignore[attr-defined]
    assert barspec.render_template(theme, fx.swap_crit()) == "🔴🔃88% ⚡23% 68%"
    assert barspec.render_template(theme, fx.busy()) == "⚡23% 68%"


def test_legacy_values_cover_preset_keys():
    vals = barspec.legacy_values(fx.busy())
    assert vals["SWAP"]["STATUS"] == "🔃12%"
    assert vals["SESSIONS"]["STATUS"] == "🧩4 ❓1 ▶2"
    assert vals["CLAUDE"]["TOKENS_TODAY_HUMAN"] == "41.2M"
    assert vals["CLAUDE"]["COST_TODAY"] == 23.84
    assert vals["CPU"]["PERCENTAGE"] == 23.0
    assert barspec.legacy_values(fx.noswap())["SWAP"]["STATUS"] == "🔃—"


def test_explicit_flag_false_disables_template():
    theme = Theme(top_bar_format="{CPU.PERCENTAGE}")
    theme.top_bar_format_explicit = False      # type: ignore[attr-defined]
    assert barspec.theme_template(theme) is None


def test_icon_desc_has_no_live_readings():
    a, b = fx.busy(), fx.busy()
    b.system.cpu.percent, b.system.memory.percent, b.spend.cost_today = 91.0, 77.0, 99.5
    assert compose_bar(a, cfg()).icon_desc == compose_bar(b, cfg()).icon_desc
    assert compose_bar(a, cfg()).accessible_desc != compose_bar(b, cfg()).accessible_desc
    assert "swap critical" in compose_bar(fx.swap_crit(), cfg()).icon_desc
    assert "tmux is not installed" in compose_bar(fx.notmux(), cfg()).icon_desc


def test_template_guide_zeroes_digits_and_reserves_badge():
    theme = Theme(top_bar_format="⚡{CPU.PERCENTAGE:.0f}% {MEMORY.PERCENTAGE:.0f}%")
    theme.top_bar_format_explicit = True       # type: ignore[attr-defined]
    label, guide = barspec.render_template_with_guide(theme, fx.busy())
    assert label == "⚡23% 68%" and guide == "🔴🔃000% ⚡00% 00%"
    label2, guide2 = barspec.render_template_with_guide(theme, fx.swap_crit())
    assert label2.startswith("🔴🔃88%") and guide2 == guide and len(guide) >= len(label2)
    spec = compose_bar(fx.busy(), cfg(), template_label=label, template_guide_str=guide)
    assert spec.guide == guide
    # without an explicit guide compose_bar still never echoes the raw value
    assert compose_bar(fx.busy(), cfg(), template_label=label).guide == "⚡00% 00%"


def test_template_that_places_swap_gets_no_badge_reserve():
    theme = Theme(top_bar_format="{SWAP.STATUS} ⚡{CPU.PERCENTAGE:.0f}%")
    theme.top_bar_format_explicit = True       # type: ignore[attr-defined]
    label, guide = barspec.render_template_with_guide(theme, fx.busy())
    assert label == "🔃12% ⚡23%" and guide == "🔃00% ⚡00%"
