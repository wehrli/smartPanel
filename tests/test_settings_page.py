"""Settings page (SP-2401, SP-2404, SP-1908, SP-0905, SP-2204, SP-2303, SP-2304).

Skipped without a display. Nothing is presented; the shortcut writer only ever
sees the in-memory backend, and the config lives in tmp_path.
"""
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

from smartpanel.config import SPECS, Config  # noqa: E402
from smartpanel.demo import scenarios as sc  # noqa: E402
from smartpanel.state import Store  # noqa: E402

NOW = 1_763_380_000.0


@pytest.fixture(scope="module", autouse=True)
def _adw():
    Adw.init()


class CfgActions:
    """Actions whose set_setting goes to a real Config (like both controllers)."""

    def __init__(self, store, config):
        self.store, self.config, self.calls = store, config, []

    def set_setting(self, key, value):
        self.calls.append((key, value))
        try:
            self.config.set(key, value)
        except ValueError as e:
            self.store.toast(str(e), error=True)

    def __getattr__(self, name):
        return lambda *a, **k: None


@pytest.fixture
def make(tmp_path):
    from smartpanel.ui.pages import settings
    from smartpanel.ui.widgets import settings_palette_shortcut as ks
    made = []

    def _make(env=None, scenario="busy", backend=None):
        store = Store(sc.build_state(scenario, NOW))
        config = Config(path=tmp_path / "config.yaml", env=env or {})
        config.bind_store(store)
        actions = CfgActions(store, config)
        backend = backend or ks.MemoryBackend()
        page = settings.build(store, actions, config, shortcut_backend=backend)
        made.append(page)
        return page, store, actions, config, backend
    yield _make
    for p in made:
        p.flush()


def test_every_key_has_a_row_in_its_group(make):
    from smartpanel.ui.pages import settings
    page, *_ = make()
    assert set(page.controls) == set(SPECS)
    assert list(page.groups) == list(settings.GROUP_ORDER)
    assert isinstance(page, Adw.PreferencesPage)


def test_switch_goes_through_set_setting_and_updates_preview(make):
    page, store, actions, config, _ = make()
    assert page.preview.label.get_text() != ""
    page.controls["bar.text"].row.set_active(False)
    assert actions.calls == [("bar.text", False)]
    assert config.get("bar.text") is False
    assert store.state.settings.values["bar.text"] is False
    assert page.preview.label.get_visible() is False
    page.controls["bar.strip"].row.set_active(False)
    page.controls["bar.graphs"].row.set_active(False)
    assert page.preview.showing_icon_only


def test_store_change_syncs_controls_in_place(make):
    page, store, actions, config, _ = make()
    row = page.controls["hud.enabled"].row
    config.set("hud.enabled", True)               # e.g. toggled from the bar menu
    assert page.controls["hud.enabled"].row is row
    assert row.get_active() is True
    assert actions.calls == []                    # syncing never echoes a set
    config.set("hud.corner", "bottom-left")
    assert page.controls["hud.corner"].row.get_selected() == 3
    config.set("appearance.style", "dark")
    assert page.controls["appearance.style"].buttons["dark"].get_active()


def test_choice_and_style_rows_commit(make):
    page, _store, actions, *_ = make()
    page.controls["hud.corner"].row.set_selected(1)
    page.controls["appearance.style"].buttons["light"].set_active(True)
    assert ("hud.corner", "top-left") in actions.calls
    assert ("appearance.style", "light") in actions.calls


def test_env_override_greys_out_with_reason(make):
    page, *_ = make(env={"SMARTPANEL_SWAP_WARN": "70"})
    c = page.controls["swap.warn"]
    assert c.row.get_sensitive() is False
    assert c.row.get_subtitle() == "Set by SMARTPANEL_SWAP_WARN"
    assert c.value() == 70
    other = page.controls["swap.crit"]
    assert other.row.get_sensitive() is True
    assert other.row.get_subtitle() == "Repeats every 10 minutes while it lasts"


def test_warning_past_critical_pushes_it_up(make):
    page, _store, actions, config, _ = make()
    warn, crit = page.controls["swap.warn"], page.controls["swap.crit"]
    warn.adj.set_value(90)
    assert crit.value() == 90 and crit.readout.get_text() == "90%"
    assert actions.calls == []                    # debounced
    page.flush()
    assert actions.calls == [("swap.warn", 90)]
    assert config.get("swap.crit") == 90


def test_critical_cannot_go_below_warning(make):
    page, _store, actions, config, _ = make()
    page.controls["swap.crit"].adj.set_value(40)  # warning is 60
    assert page.controls["swap.crit"].value() == 60
    page.flush()
    assert config.get("swap.crit") == 60


def test_min_age_shown_in_minutes_stored_in_seconds(make):
    page, _store, actions, config, _ = make()
    c = page.controls["sessions.min_age_s"]
    assert c.adj.get_value() == 2
    c.adj.set_value(5)
    page.flush()
    assert actions.calls == [("sessions.min_age_s", 300)]
    assert config.get("sessions.min_age_s") == 300


def test_numbers_cannot_go_negative(make):
    page, *_ = make()
    for key in ("sessions.min_age_s", "swap.hysteresis", "hud.margin", "swap.repeat_s"):
        adj = page.controls[key].adj
        adj.set_value(-5)
        assert adj.get_value() >= 0
    assert page.controls["sessions.confirm_seconds"].adj.get_lower() == 1


def test_budget_empty_means_off_and_bad_input_is_inline(make):
    page, _store, actions, config, _ = make()
    c = page.controls["budget.daily_usd"]
    assert c.entry.get_text() == ""
    assert c.submit("25") and config.get("budget.daily_usd") == 25.0
    assert c.entry.get_text() == "25"
    c.entry.set_text("abc")
    assert not c.submit()
    assert c.error and "budget off" in c.row.get_subtitle()
    assert config.get("budget.daily_usd") == 25.0
    # an unrelated settings change keeps the rejected text and its error
    config.set("hud.margin", 40)
    assert c.error and c.entry.get_text() == "abc"
    assert not c.submit("-3")
    assert c.submit("")
    assert config.get("budget.daily_usd") is None and c.error is None
    assert c.row.get_subtitle() == "Shown on Overview and on every receipt"


def test_folder_must_exist(make, tmp_path):
    page, _store, actions, config, _ = make()
    c = page.controls["sessions.dir"]
    assert not c.submit(str(tmp_path / "nope"))
    assert c.error == "That folder doesn’t exist"
    assert "entry" not in [k for k, _v in actions.calls]
    assert not [k for k, _v in actions.calls if k == "sessions.dir"]
    good = tmp_path / "proj"
    good.mkdir()
    assert c.submit(str(good))
    assert config.get("sessions.dir") == str(good)
    assert c.error is None


def test_disk_mount_needs_absolute_path(make):
    page, _store, actions, *_ = make()
    c = page.controls["disk.mount"]
    assert not c.submit("relative")
    assert c.error
    assert not actions.calls
    assert c.submit("/")                          # fixing it clears the error
    assert c.error is None
    c.entry.set_text("/")
    assert c.submit() is False                    # unchanged value: nothing to save


def test_add_shortcut_writes_only_to_the_injected_backend(make):
    from smartpanel.ui.widgets import settings_palette_shortcut as ks
    page, store, *_rest = make()
    backend = _rest[-1]
    events = []
    store.on_event(events.append)
    c = page.controls["palette.shortcut"]
    assert c.code.get_text() == "Ctrl+Alt+Space"
    assert c.button.get_sensitive() and c.button.get_label() == "Add shortcut"
    assert page.add_shortcut(c)
    entry = ks.find_existing(backend)
    assert entry["binding"] == "<Ctrl><Alt>space"
    assert entry["command"] == "smartpanel --palette"
    assert c.button.get_sensitive() is False and c.button.get_label() == "Shortcut added"
    assert events and events[-1].kind == "toast" and not events[-1].data["error"]


def test_add_shortcut_unavailable(make):
    from smartpanel.ui.widgets import settings_palette_shortcut as ks
    page, store, *_ = make(backend=ks.MemoryBackend(available=False))
    c = page.controls["palette.shortcut"]
    assert c.button.get_sensitive() is False
    events = []
    store.on_event(events.append)
    assert not page.add_shortcut(c)
    assert events[-1].data["error"]


def test_demo_uses_memory_backend():
    from smartpanel.ui.pages import settings
    from smartpanel.ui.widgets import settings_palette_shortcut as ks
    state = sc.build_state("busy", NOW)
    state.app.demo = "busy"
    page = settings.build(Store(state), CfgActions(None, None), Config())
    assert isinstance(page.shortcut_backend, ks.MemoryBackend)


def test_loading_preview_shows_spinner_label(make):
    page, *_ = make(scenario="cold")
    assert page.preview.spec.loading
    assert page.preview.label.get_text().startswith("reading")


def test_money_helpers():
    from smartpanel.ui.pages.settings import format_money, parse_money
    assert parse_money("") == (True, None)
    assert parse_money(" $12,5 ") == (True, 12.5)
    assert parse_money("0") == (True, None)
    assert parse_money("-1") == (False, None)
    assert parse_money("nan") == (False, None)
    assert format_money(None) == "" and format_money(25.0) == "25"
    assert format_money(12.5) == "12.50"
