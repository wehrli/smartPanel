"""Design tokens (SP-0008): AA contrast, palette parity, CSS references."""
import re

import pytest

from smartpanel.ui import tokens

TEXT_TOKENS = ("fg", "muted", "wait", "run", "idle", "jour", "crit", "ok", "accent_text")


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("name", TEXT_TOKENS)
@pytest.mark.parametrize("surface", ["bg", "card", "side"])
def test_text_tokens_meet_wcag_aa(mode, name, surface):
    p = tokens.PALETTES[mode]
    assert tokens.contrast(p[name], p[surface]) >= 4.5, (mode, name, surface)


@pytest.mark.parametrize("name", sorted(tokens.FILLS))
def test_fill_pairs_meet_wcag_aa(name):
    bg, fg = tokens.FILLS[name]
    assert tokens.contrast(bg, fg) >= 4.5, name


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_banner_and_toast_text_meet_aa(mode):
    p = tokens.PALETTES[mode]
    assert tokens.contrast(p["banner_warn_bg"], p["banner_warn_fg"]) >= 4.5
    assert tokens.contrast(p["banner_crit_bg"], p["banner_crit_fg"]) >= 4.5
    assert tokens.contrast(p["toast_err_bg"], p["toast_fg"]) >= 4.5


def test_modes_define_the_same_tokens():
    assert set(tokens.PALETTES["light"]) == set(tokens.PALETTES["dark"])


def test_accent_is_ubuntu_orange_in_both_modes():
    assert tokens.ACCENT.lower() == "#e95420"
    assert all(p["accent"] == tokens.ACCENT for p in tokens.PALETTES.values())


def test_contrast_maths():
    assert tokens.contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert tokens.contrast("#777777", "#777777") == pytest.approx(1.0)


def test_every_css_variable_used_is_defined():
    css = tokens.STYLE_CSS.read_text()
    used = set(re.findall(r"var\(--sp-([a-z0-9-]+)\)", css))
    for dark in (False, True):
        defined = set(re.findall(r"--sp-([a-z0-9-]+):", tokens.palette_css(dark)))
        assert used <= defined, used - defined


def test_palette_css_differs_by_mode_and_is_a_root_block():
    light, dark = tokens.palette_css(False), tokens.palette_css(True)
    assert light.startswith(":root {") and dark.startswith(":root {")
    assert light != dark
    assert "--sp-wait: #f6b73c;" in dark


def test_chart_colors_have_what_the_painters_need():
    for dark in (False, True):
        assert {"line", "dot", "warn", "crit", "bar", "now"} <= set(tokens.chart_colors(dark))
