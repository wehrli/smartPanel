"""GTK tests for the Spend and Journal pages: build, update in place, routing,
states. Skipped without a display; nothing is presented on screen."""
import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gtk, Pango
    _DISPLAY = Gtk.init_check() if hasattr(Gtk, "init_check") else True
    _DISPLAY = _DISPLAY and Gdk.Display.get_default() is not None
except (ImportError, ValueError):  # pragma: no cover
    _DISPLAY = False

pytestmark = pytest.mark.skipif(not _DISPLAY, reason="needs GTK 4 and a display")

from smartpanel.demo import build_state  # noqa: E402
from smartpanel.state import Store  # noqa: E402
from smartpanel.state.model import JournalEntry, Loading  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _adw():
    Adw.init()


class RecordingActions:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append((name, a))
        return rec


class FakeConfig:
    def __init__(self, **values):
        self.values = {"sessions.min_age_s": 120, "sessions.journal": True, **values}

    def get(self, key):
        return self.values.get(key)


def _spend(scenario="busy"):
    from smartpanel.ui.pages import spend
    store = Store(build_state(scenario))
    actions = RecordingActions()
    return spend, store, actions, spend.build(store, actions, FakeConfig())


def _journal(scenario="busy", **kw):
    from smartpanel.ui.pages import journal
    store = Store(build_state(scenario))
    page = journal.JournalPage(store, RecordingActions(), FakeConfig(), **kw)
    return journal, store, page


# ───────────────────────── spend ─────────────────────────

def test_spend_page_shows_header_days_and_today_receipt():
    _mod, store, _a, page = _spend()
    assert page.get_visible_child_name() == "main"
    sp = store.state.spend
    assert page.card_today.value.get_text() == f"${sp.cost_today:,.2f}"
    assert page.card_cache.value.get_text() == f"{sp.cache_hit_pct:.0f}%"
    assert len(page._rows) == 14
    assert page.selected_date == sp.days[0].date
    r = page.receipt.model
    assert r.total == f"{sp.days[0].cost_usd:.2f}"
    assert page.receipt.labels["total"].get_text() == r.total
    assert page.receipt.labels["rest.label"].get_text() in ("left", "over by")
    assert page.receipt.note_visible       # compliments shown -> the estimate footnote


def test_spend_selection_survives_updates_and_zero_day():
    mod, store, _a, page = _spend()
    days = store.state.spend.days
    zero = next(d for d in days if d.cost_usd == 0)
    mod.show_detail(page, zero.date)
    assert page.selected_date == zero.date
    assert page.receipt.model.empty
    assert page.receipt.labels["empty"].get_text() == "NO USAGE THIS DAY"
    assert not page.receipt.note_visible
    row_before = page._rows[0]
    store.update("spend", cost_today=99.0)          # a tick
    assert page.selected_date == zero.date           # selection kept
    assert page._rows[0] is row_before               # rows reused, not rebuilt
    assert page.card_today.value.get_text() == "$99.00"


def test_spend_row_click_changes_receipt():
    _mod, store, _a, page = _spend()
    page.days.select_row(page._rows[1])
    assert page.selected_date == store.state.spend.days[1].date
    assert page.receipt.model.date_line.endswith(str(int(store.state.spend.days[1].date[:4])))


def test_spend_selection_follows_today_across_midnight():
    import dataclasses
    _mod, store, _a, page = _spend()
    old = list(store.state.spend.days)
    new_today = dataclasses.replace(old[0], date="2026-11-18", label="Today", cost_usd=1.0)
    yesterday = dataclasses.replace(old[0], label="Yesterday")
    store.update("spend", days=[new_today, yesterday] + old[1:13], cost_today=1.0)
    assert page.selected_date == "2026-11-18"
    assert page.receipt.model.date_line.startswith("WED 18 NOV 2026")


def test_spend_picked_day_is_kept_until_reopened():
    import dataclasses
    _mod, store, _a, page = _spend()
    old = list(store.state.spend.days)
    page.days.select_row(page._rows[2])
    picked = old[2].date
    store.update("spend", days=[dataclasses.replace(old[0], date="2026-11-18")] + old[:13])
    assert page.selected_date == picked              # the user's pick survives
    page._window_closed = True                       # window hidden, then reopened
    page._on_map()
    assert page.selected_date == "2026-11-18"


def test_spend_route_during_loading_applies_when_loaded():
    mod, store, _a, page = _spend("cold")
    from smartpanel.demo.scenarios import loaded_spend
    loaded = loaded_spend()
    target = loaded.days[3].date
    mod.show_detail(page, target)                    # not listed yet: remembered
    with store.batch():
        store.replace("spend", loaded)
        store.update("app", loading=None)
    assert page.selected_date == target


def test_receipt_updates_in_place_on_a_tick():
    _mod, store, _a, page = _spend()
    rebuilds = page.receipt.rebuilds
    total_label = page.receipt.labels["total"]
    days = list(store.state.spend.days)
    import dataclasses
    days[0] = dataclasses.replace(days[0], cost_usd=days[0].cost_usd + 1.25)
    store.update("spend", days=days, updated_at=(store.state.spend.updated_at or 0) + 60)
    assert page.receipt.rebuilds == rebuilds         # same shape: no rebuild
    assert page.receipt.labels["total"] is total_label
    assert total_label.get_text() == page.receipt.model.total


def test_spend_budget_lines_hidden_without_budget():
    _mod, store, _a, page = _spend()
    store.state.spend.forecast.budget_usd = None
    store.state.settings.values["budget.daily_usd"] = None
    store.touch("spend")
    assert "budget" not in page.receipt.labels


def test_spend_loading_then_loaded():
    _mod, store, _a, page = _spend("cold")
    assert page.get_visible_child_name() == "loading"
    assert page.loading_text.get_text().startswith("0 of 1,284 session files")
    store.update("app", loading=Loading(done=642, total=1284))
    assert page.loading_text.get_text().startswith("642 of 1,284")
    assert abs(page.loading_track.get_fraction() - 0.5) < 1e-6
    from smartpanel.demo.scenarios import loaded_spend
    with store.batch():
        store.replace("spend", loaded_spend())
        store.update("app", loading=None)
    assert page.get_visible_child_name() == "main"


def test_spend_first_run_new_session():
    _mod, _store, actions, page = _spend("first")
    assert page.get_visible_child_name() == "first"
    page.first_run.button.emit("clicked")
    assert ("request_new_session", ()) in actions.calls


# ───────────────────────── journal ─────────────────────────

def test_journal_lists_entries_and_selects_first():
    _mod, store, page = _journal()
    entries = store.state.journal.entries
    assert page.get_visible_child_name() == "main"
    assert len(page._rows) == len(entries)
    assert page.selected == entries[0].id
    assert page.detail.title.get_text() == entries[0].title
    assert page._rows[0].badge.get_text() == "✓ JOURNALED"
    assert page.detail.dd["Duration"].get_text() == "2h30m"
    assert page.detail.open_log.get_child().get_text().startswith("Open JOURNAL/")


def test_journal_open_buttons_wrap_instead_of_cutting_the_file_name():
    # SP-1803: at the default 900 px window the detail card is ~375 px wide;
    # 'Open JOURNAL/2026-11.md' must wrap below 'Open receipt', not be
    # middle-ellipsized to 'Open JOURN…2026-11.md'.
    _mod, _store, page = _journal()
    btns = page.detail.buttons
    label = page.detail.open_log.get_child()
    assert label.get_text() == "Open JOURNAL/2026-11.md"
    assert label.get_ellipsize() != Pango.EllipsizeMode.MIDDLE
    one_line = btns.measure(Gtk.Orientation.VERTICAL, 2000)[1]
    narrow = btns.measure(Gtk.Orientation.VERTICAL, 300)[1]
    assert narrow > one_line                     # wrapped onto a second line
    # each button still gets its whole label on that second line
    log_natural = page.detail.open_log.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
    assert log_natural <= 300


def test_journal_route_selects_entry_and_failed_quote():
    mod, store, page = _journal()
    failed = store.state.journal.entries[1]
    mod.show_detail(page, failed.id)
    assert page.selected == failed.id
    assert page.detail.quote_box.has_css_class("sj-failed")
    assert page._rows[1].badge.get_text() == "RECEIPT ONLY"


def test_journal_pending_route_and_new_entry_keeps_selection():
    mod, store, page = _journal()
    keep = store.state.journal.entries[2].id
    mod.show_detail(page, keep)
    new = JournalEntry(id="2026-11-17-1500-new", path="/nonexistent/new.md", time=1.0,
                       title="New one", project="x")
    store.mutate("journal", lambda j: j.entries.insert(0, new))
    assert page.selected == keep                     # inserting on top keeps selection
    mod.show_detail(page, "later-id")                # not there yet: remembered
    assert page.selected == keep
    late = JournalEntry(id="later-id", path="/nonexistent/late.md", title="Late", project="y")
    store.mutate("journal", lambda j: j.entries.insert(0, late))
    assert page.selected == "later-id"


def test_journal_open_buttons_follow_file_existence(tmp_path):
    receipt = tmp_path / "r.md"
    receipt.write_text("x")
    opened = []
    _mod, store, page = _journal(launcher=opened.append)
    store.mutate("journal", lambda j: setattr(j.entries[0], "path", str(receipt)))
    assert page.detail.open_receipt.get_sensitive()
    assert not page.detail.open_log.get_sensitive()  # the demo's monthly log isn't on disk
    page.detail.open_receipt.emit("clicked")
    assert opened == [str(receipt)]
    receipt.unlink()                                  # vanished since: toast, not launch
    toasts = []
    store.on_event(lambda e: toasts.append(e))
    page.detail.open_receipt.emit("clicked")
    assert opened == [str(receipt)] and toasts and toasts[0].data["error"]
    assert not page.detail.open_receipt.get_sensitive()


def test_journal_log_button_enables_when_log_appears(tmp_path):
    log_file = tmp_path / "2026-11.md"
    _mod, store, page = _journal()
    store.update("journal", monthly_log_path=str(log_file))
    assert not page.detail.open_log.get_sensitive()
    log_file.write_text("x")
    store.touch("journal")                           # any journal change re-stats
    assert page.detail.open_log.get_sensitive()


def test_journal_unreadable_entry():
    _mod, store, page = _journal()
    store.mutate("journal", lambda j: setattr(j.entries[0], "readable", False))
    assert page._rows[0].meta.get_text() == "Couldn’t read this receipt"
    assert not page.detail.dl.get_visible()


def test_journal_empty_state_follows_min_age():
    _mod, store, page = _journal("first")
    assert page.get_visible_child_name() == "empty"
    store.state.settings.values["sessions.min_age_s"] = 300
    store.touch("settings")
    assert "5 minutes" in page.empty._text.get_text()


def test_journal_open_goes_through_actions_open_path(tmp_path):
    receipt = tmp_path / "r.md"
    receipt.write_text("x")
    _mod, store, page = _journal()
    store.mutate("journal", lambda j: setattr(j.entries[0], "path", str(receipt)))
    page.detail.open_receipt.emit("clicked")
    assert ("open_path", (str(receipt),)) in page.actions.calls


def test_spend_receipt_time_comes_from_state_not_the_wall_clock():
    from smartpanel.demo import DEMO_NOW
    from smartpanel.ui.pages import spend
    store = Store(build_state("busy", now=DEMO_NOW))       # the prototype's 14:32
    page = spend.build(store, RecordingActions(), FakeConfig())
    assert page.receipt._model.date_line.endswith("· 14:32")
    later = DEMO_NOW + 3 * 60
    store.update("spend", updated_at=later)
    assert page.receipt._model.date_line.endswith("· 14:35")
