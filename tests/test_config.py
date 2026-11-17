"""Settings file: load, validate, env precedence, atomic save, observers (SP-0007/2402/2403)."""
from __future__ import annotations

import logging
import os

import pytest
import yaml

from smartpanel.config import (DEFAULTS, ENV_VARS, SPECS, Config, SettingOverridden,
                               validate)
from smartpanel.state import Store


@pytest.fixture
def path(tmp_path):
    return tmp_path / "smartpanel" / "config.yaml"


def load(path, env=None):
    return Config.load(path, env=env or {})


# ── loading ──

def test_missing_file_gives_defaults(path):
    cfg = load(path)
    assert cfg.all() == DEFAULTS
    assert not path.exists()  # loading never writes


def test_bare_config_ignores_process_env(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))   # never read the host's file
    monkeypatch.setenv("SMARTPANEL_SWAP_WARN", "70")
    assert Config().get("swap.warn") == 60
    assert Config.load(path=None, env=None).overridden_by("swap.warn") == "SMARTPANEL_SWAP_WARN"


@pytest.mark.parametrize("text", [
    "swap: [unclosed", "just a string", "- a\n- list\n", "\x00\x01 garbage: : :",
])
def test_garbled_file_falls_back_to_defaults(path, text, caplog):
    path.parent.mkdir(parents=True)
    path.write_text(text)
    with caplog.at_level(logging.WARNING):
        cfg = load(path)
    assert cfg.all() == DEFAULTS
    assert caplog.records


def test_unreadable_file_is_a_warning(tmp_path, caplog):
    d = tmp_path / "dir-not-file"
    d.mkdir()
    with caplog.at_level(logging.WARNING):
        cfg = load(d)
    assert cfg.all() == DEFAULTS


def test_empty_file_is_defaults(path):
    path.parent.mkdir(parents=True)
    path.write_text("")
    assert load(path).all() == DEFAULTS


def test_nested_and_flat_keys_load(path):
    path.parent.mkdir(parents=True)
    path.write_text("swap:\n  warn: 70\n  crit: 90\nbudget.daily_usd: 40\n"
                    "sessions:\n  dir: ~/work\n  journal: 'off'\n")
    cfg = load(path)
    assert cfg.get("swap.warn") == 70
    assert cfg.get("swap.crit") == 90
    assert cfg.get("budget.daily_usd") == 40.0
    assert cfg.get("sessions.dir") == "~/work"
    assert cfg.get("sessions.journal") is False


def test_bad_value_in_good_file_keeps_that_default_only(path, caplog):
    path.parent.mkdir(parents=True)
    path.write_text("swap:\n  warn: lots\n  crit: 90\nhud:\n  corner: middle\n")
    with caplog.at_level(logging.WARNING):
        cfg = load(path)
    assert cfg.get("swap.warn") == 60
    assert cfg.get("swap.crit") == 90
    assert cfg.get("hud.corner") == "top-right"
    assert any("swap.warn" in r.getMessage() for r in caplog.records)


def test_unknown_file_keys_are_kept_on_save(path):
    path.parent.mkdir(parents=True)
    path.write_text("future:\n  thing: 3\nswap:\n  warn: 70\n")
    cfg = load(path)
    cfg.set("swap.crit", 95)
    data = yaml.safe_load(path.read_text())
    assert data["future"] == {"thing": 3}
    assert data["swap"] == {"warn": 70, "crit": 95}


def test_constructor_values_are_validated(caplog):
    with caplog.at_level(logging.WARNING):
        cfg = Config({"swap.warn": "70", "bar.strip": "nope", "bogus": 1})
    assert cfg.get("swap.warn") == 70
    assert cfg.get("bar.strip") is True


# ── validation ──

@pytest.mark.parametrize("key,value,expected", [
    ("bar.strip", "yes", True), ("bar.strip", 0, False), ("bar.ticker", "On", True),
    ("bar.ticker_seconds", "7", 7), ("swap.warn", 70.0, 70), ("swap.warn", "62.5", 62.5),
    ("budget.daily_usd", None, None), ("budget.daily_usd", "", None),
    ("budget.daily_usd", 0, None), ("budget.daily_usd", "40", 40.0),
    ("budget.daily_usd", 12.345, 12.35),
    ("sessions.min_age_s", 0, 0), ("sessions.dir", "  ~/x ", "~/x"),
    ("hud.corner", "Bottom-Left", "bottom-left"), ("appearance.style", "dark", "dark"),
    ("disk.mount", "/home", "/home"),
])
def test_validate_coerces(key, value, expected):
    assert validate(key, value) == expected


@pytest.mark.parametrize("key,value", [
    ("bar.strip", "maybe"), ("bar.strip", 2), ("swap.warn", -1), ("swap.warn", 101),
    ("swap.warn", True), ("swap.warn", "nan"), ("swap.warn", float("inf")),
    ("bar.ticker_seconds", 0), ("bar.ticker_seconds", 2.5), ("budget.daily_usd", -3),
    ("sessions.min_age_s", -60), ("sessions.confirm_seconds", 0), ("sessions.dir", ""),
    ("sessions.dir", 3), ("hud.corner", "middle"), ("appearance.style", None),
    ("disk.mount", "relative"), ("no.such.key", 1),
])
def test_validate_rejects(key, value):
    with pytest.raises(ValueError):
        validate(key, value)


def test_every_key_has_spec_and_defaults_validate():
    assert set(SPECS) == set(DEFAULTS)
    for key, value in DEFAULTS.items():
        assert validate(key, value) == value
    assert set(ENV_VARS) <= set(DEFAULTS)


# ── set + atomic save ──

def test_set_persists_atomically_and_round_trips(path):
    cfg = load(path)
    cfg.set("swap.warn", 70)
    cfg.set("budget.daily_usd", "40")
    cfg.set("sessions.journal", False)
    assert sorted(os.listdir(path.parent)) == ["config.yaml"]   # no temp files left
    again = load(path)
    assert again.get("swap.warn") == 70
    assert again.get("budget.daily_usd") == 40.0
    assert again.get("sessions.journal") is False
    assert again.all() == cfg.all()


def test_set_rejects_unknown_and_bad_values(path):
    cfg = load(path)
    with pytest.raises(ValueError):
        cfg.set("nope", 1)
    with pytest.raises(ValueError):
        cfg.set("swap.warn", "high")
    assert not path.exists()
    assert cfg.get("swap.warn") == 60


def test_save_failure_keeps_value_in_memory(tmp_path, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    cfg = Config.load(blocker / "sub" / "config.yaml", env={})
    with caplog.at_level(logging.WARNING):
        cfg.set("swap.warn", 70)
    assert cfg.get("swap.warn") == 70
    assert any("can't save" in r.getMessage() for r in caplog.records)


def test_config_without_path_does_not_write(tmp_path):
    cfg = Config()
    cfg.set("swap.warn", 70)
    assert cfg.get("swap.warn") == 70
    assert cfg.save() is False


# ── swap crit >= warn (SP-2404) ──

def test_raising_warn_pushes_crit_up(path):
    cfg = load(path)
    seen = []
    cfg.subscribe(lambda k, v: seen.append((k, v)))
    cfg.set("swap.warn", 90)
    assert cfg.get("swap.crit") == 90
    assert ("swap.warn", 90) in seen and ("swap.crit", 90) in seen
    assert load(path).get("swap.crit") == 90


def test_crit_below_warn_is_clamped(path):
    cfg = load(path)
    cfg.set("swap.crit", 40)
    assert cfg.get("swap.crit") == 60


def test_env_inversion_is_clamped(path):
    cfg = load(path, env={"SMARTPANEL_SWAP_WARN": "80", "SMARTPANEL_SWAP_CRIT": "70"})
    assert cfg.get("swap.warn") == 80
    assert cfg.get("swap.crit") == 80
    # file crit below env warn: the effective value is clamped too
    cfg2 = load(path, env={"SMARTPANEL_SWAP_WARN": "80"})
    cfg2.set("swap.crit", 75)
    assert cfg2.get("swap.crit") == 80


# ── env precedence (SP-2402) ──

@pytest.mark.parametrize("key,env,raw,expected", [
    ("swap.warn", "SMARTPANEL_SWAP_WARN", "55", 55),
    ("swap.crit", "SMARTPANEL_SWAP_CRIT", "95", 95),
    ("swap.hysteresis", "SMARTPANEL_SWAP_HYSTERESIS", "2.5", 2.5),
    ("swap.repeat_s", "SMARTPANEL_SWAP_REPEAT", "0", 0),
    ("sessions.min_age_s", "SMARTPANEL_JOURNAL_MIN_AGE", "300", 300),
    ("sessions.reattach", "SMARTPANEL_FOCUS_REATTACH", "0", False),
    ("sessions.dir", "SMARTPANEL_SESSIONS_DIR", "/srv/code", "/srv/code"),
    ("sessions.terminal", "SMARTPANEL_TERMINAL", "kitty", "kitty"),
])
def test_env_beats_file_beats_default(path, key, env, raw, expected):
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({key: DEFAULTS[key]}))
    cfg = load(path, env={env: raw})
    assert cfg.get(key) == expected
    assert cfg.overridden_by(key) == env
    assert cfg.overrides() == {key: env}
    with pytest.raises(SettingOverridden, match=env):
        cfg.set(key, DEFAULTS[key])
    assert cfg.get(key) == expected


def test_garbled_env_is_ignored_and_not_an_override(path, caplog):
    with caplog.at_level(logging.WARNING):
        cfg = load(path, env={"SMARTPANEL_SWAP_WARN": "lots", "SMARTPANEL_TERMINAL": ""})
    assert cfg.get("swap.warn") == 60
    assert cfg.overridden_by("swap.warn") is None
    assert cfg.overridden_by("sessions.terminal") is None
    cfg.set("swap.warn", 65)
    assert cfg.get("swap.warn") == 65


def test_unknown_key_reads_as_none():
    assert Config().get("no.such.key") is None


def test_overridden_by_none_for_keys_without_env(path):
    cfg = load(path, env={"SMARTPANEL_SWAP_WARN": "70"})
    assert cfg.overridden_by("bar.strip") is None


# ── observers (SP-2403) ──

def test_observers_fire_only_on_real_change(path):
    cfg = load(path)
    seen = []
    unsub = cfg.subscribe(lambda k, v: seen.append((k, v)))
    cfg.set("hud.enabled", True)
    cfg.set("hud.enabled", True)
    cfg.set("hud.enabled", "yes")
    assert seen == [("hud.enabled", True)]
    unsub()
    cfg.set("hud.enabled", False)
    assert seen == [("hud.enabled", True)]


def test_broken_observer_does_not_stop_others(path, caplog):
    cfg = load(path)
    seen = []

    def boom(k, v):
        raise RuntimeError("boom")

    cfg.subscribe(boom)
    cfg.subscribe(lambda k, v: seen.append(k))
    with caplog.at_level(logging.ERROR):
        cfg.set("bar.ticker", True)
    assert seen == ["bar.ticker"]


def test_reload_notifies_hand_edits(path):
    cfg = load(path)
    cfg.set("swap.warn", 70)
    seen = []
    cfg.subscribe(lambda k, v: seen.append((k, v)))
    path.write_text("swap:\n  warn: 75\n")
    cfg.reload()
    assert seen == [("swap.warn", 75)]


# ── store mirror ──

def test_bind_store_mirrors_values_and_overrides(path):
    store = Store()
    cfg = load(path, env={"SMARTPANEL_TERMINAL": "kitty"})
    sections = []
    store.subscribe(sections.append)
    cfg.bind_store(store)
    cfg.bind_store(store)   # idempotent
    s = store.state.settings
    assert s.values == cfg.all()
    assert s.values["sessions.terminal"] == "kitty"
    assert s.overridden_by == {"sessions.terminal": "SMARTPANEL_TERMINAL"}
    sections.clear()
    cfg.set("budget.daily_usd", 40)
    assert store.state.settings.values["budget.daily_usd"] == 40.0
    assert sections == [{"settings"}]
    store.to_json()   # stays serialisable


def test_last_dir_is_an_optional_hidden_string():
    assert DEFAULTS["sessions.last_dir"] is None
    assert SPECS["sessions.last_dir"].hidden
    assert not any(s.hidden for k, s in SPECS.items() if k != "sessions.last_dir")
    assert validate("sessions.last_dir", " /home/u/dev/x ") == "/home/u/dev/x"
    assert validate("sessions.last_dir", "") is None
    assert validate("sessions.last_dir", None) is None
    with pytest.raises(ValueError):
        validate("sessions.last_dir", 3)


def test_last_dir_round_trips_through_the_file(tmp_path):
    path = tmp_path / "config.yaml"
    cfg = Config.load(path, env={})
    cfg.set("sessions.last_dir", "/home/u/dev/ledgerly")
    assert Config.load(path, env={}).get("sessions.last_dir") == "/home/u/dev/ledgerly"
