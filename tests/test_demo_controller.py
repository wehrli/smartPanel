"""DemoController: the prototype's flows in virtual time (SP-0012, SP-1610)."""
from __future__ import annotations

import pytest

from smartpanel.config import Config
from smartpanel.demo.controller import (APPLYING, JOURNAL_BUSY_WAIT_MS, DemoController,
                                        LAUNCH_MS)
from smartpanel.demo.scenarios import PROJECTS
from smartpanel.state.actions import Actions
from smartpanel.state.model import (IDLE, JOURNAL_STEPS, LEVEL_CRITICAL, LEVEL_OK,
                                    LEVEL_WARNING, OUTCOME_JOURNALED, OUTCOME_SKILL_FAILED,
                                    PHASE_JOURNALING, PHASE_LAUNCHING, PHASE_NONE, RUNNING,
                                    UNKNOWN, WAITING)
from tests.helpers_state import FakeScheduler, Recorder, demo_controller, store_for

LEDGERLY = "claude-ledgerly"           # idle, 190 min old
SMART = "claude-smartPanel"        # waiting, 62 min old
STRIPE = "claude-JobScout"       # running, 12 min old


def find(store, name):
    return next((s for s in store.state.sessions.items if s.name == name), None)


def test_implements_every_action():
    store, ctl, *_ = demo_controller()
    for name, member in vars(Actions).items():
        if callable(member) and not name.startswith("_"):
            assert callable(getattr(type(ctl), name, None)), name


def test_env_switches():
    _, ctl, *_ = demo_controller(env={"SMARTPANEL_DEMO_JOURNAL_FAILS": "1",
                                      "SMARTPANEL_DEMO_FOCUS_ROUTE": "raise",
                                      "SMARTPANEL_DEMO_TERMINAL_MISSING": "yes"})
    assert ctl.journal_fails and ctl.focus_route == "raise" and ctl.terminal_missing
    _, ctl, *_ = demo_controller(env={"SMARTPANEL_DEMO_FOCUS_ROUTE": "warp"})
    assert ctl.focus_route == "reattach" and not ctl.journal_fails


# ── navigation ──

def test_navigation_events():
    store, ctl, sched, rec = demo_controller()
    ctl.open_dashboard("journal:abc")
    ctl.open_palette()
    ctl.request_new_session()
    ctl.request_close_all()
    assert [e.kind for e in rec.events] == ["open_dashboard", "open_palette", "new_session",
                                            "confirm_close_all"]
    assert rec.events[0].data == {"page": "journal:abc"}


def test_quit_emits_and_stops_timers():
    store, ctl, sched, rec = demo_controller(live=True)
    assert sched.pending
    ctl.quit()
    assert rec.of("quit") == [{}]
    assert sched.pending == 0


def test_pin_ticker_and_hud():
    cfg = Config()
    store, ctl, sched, rec = demo_controller(config=cfg)
    ctl.pin_ticker(True)
    assert store.state.app.ticker_pinned
    ctl.set_hud(True)
    assert cfg.get("hud.enabled") is True
    assert store.state.settings.values["hud.enabled"] is True


# ── close with journal (SP-1610) ──

def test_request_close_old_session_asks_to_confirm():
    store, ctl, sched, rec = demo_controller()
    ctl.request_close(LEDGERLY)
    assert rec.of("confirm_close") == [{"name": LEDGERLY}]
    assert find(store, LEDGERLY) is not None


def test_request_close_young_session_closes_at_once():
    store, ctl, sched, rec = demo_controller()
    ctl.new_session("~/dev/omdev/wishlist")
    sched.advance(LAUNCH_MS)
    rec.clear()
    ctl.request_close("claude-wishlist")
    assert find(store, "claude-wishlist") is None
    assert rec.last_toast()["text"] == "Closed “wishlist”. Open under 2 min, so no journal."
    assert rec.of("confirm_close") == []


def test_request_close_with_journaling_off():
    cfg = Config({"sessions.journal": False})
    store, ctl, sched, rec = demo_controller(config=cfg)
    ctl.request_close(LEDGERLY)
    assert find(store, LEDGERLY) is None
    assert rec.last_toast()["text"] == "Closed “Migrate goose 0023”."


def test_min_age_comes_from_config():
    cfg = Config({"sessions.min_age_s": 4 * 3600})
    store, ctl, sched, rec = demo_controller(config=cfg)
    ctl.request_close(LEDGERLY)           # 190 min < 4 h
    assert find(store, LEDGERLY) is None
    assert "Open under 4 h," in rec.last_toast()["text"]


def test_close_without_journal():
    store, ctl, sched, rec = demo_controller()
    ctl.close_session(LEDGERLY, journal=False)
    assert find(store, LEDGERLY) is None
    assert rec.last_toast()["text"] == "Closed without a journal entry"


def test_journal_runs_five_steps_then_closes():
    store, ctl, sched, rec = demo_controller()
    ctl.close_session(LEDGERLY, journal=True)
    s = find(store, LEDGERLY)
    assert s.phase == PHASE_JOURNALING and s.journal.step == 0
    # journaling sorts before idle (prototype ORDER)
    assert [x.name for x in store.state.sessions.items].index(LEDGERLY) == 3
    steps = [s.journal.step]
    for ms in (800, 1200, 2000, 700):       # idle session: step 0 takes 800 ms
        sched.advance(ms)
        steps.append(find(store, LEDGERLY).journal.step)
    assert steps == [0, 1, 2, 3, 4]
    assert rec.of("toast") == []
    sched.advance(600)
    assert find(store, LEDGERLY) is None
    entry = store.state.journal.entries[0]
    assert entry.outcome == OUTCOME_JOURNALED and entry.title == "Migrate goose 0023"
    assert entry.project == "ledgerly" and entry.duration_s == 190 * 60 + 5
    assert entry.id.endswith("-ledgerly") and entry.path.endswith(entry.id + ".md")
    assert entry.confirmation.startswith("Journaled ledgerly to JOURNAL/2026-11.md.")
    assert len(store.state.journal.entries) == 4
    t = rec.last_toast()
    assert t == {"text": "Journaled & closed “Migrate goose 0023”", "error": False,
                 "action": {"label": "View", "page": f"journal:{entry.id}"}}
    assert ctl.notifications == [("Journal written · Migrate goose 0023",
                                  "Appended to JOURNAL/2026-11.md. Session closed.", "ok")]
    assert len(JOURNAL_STEPS) == 5


def test_busy_session_waits_longer_to_go_idle():
    store, ctl, sched, rec = demo_controller()
    ctl.close_session(STRIPE, journal=True)
    sched.advance(JOURNAL_BUSY_WAIT_MS - 1)
    assert find(store, STRIPE).journal.step == 0
    sched.advance(1)
    assert find(store, STRIPE).journal.step == 1


def test_journal_failure_switch_still_closes():
    store, ctl, sched, rec = demo_controller()
    ctl.journal_fails = True
    ctl.close_session(LEDGERLY, journal=True)
    sched.advance(800 + 1200)
    s = find(store, LEDGERLY)
    assert s.journal.step == 2 and not s.journal.failed
    sched.advance(2000)
    assert s.journal.failed and s.journal.step == 2      # "failed, closing anyway"
    assert find(store, LEDGERLY) is not None
    sched.advance(1500)
    assert find(store, LEDGERLY) is None
    entry = store.state.journal.entries[0]
    assert entry.outcome == OUTCOME_SKILL_FAILED
    assert "no confirmation line" in entry.confirmation
    t = rec.last_toast()
    assert t["text"] == "Closed. The journal failed, receipt saved." and t["error"]
    assert t["action"] == {"label": "View", "page": f"journal:{entry.id}"}
    assert ctl.notifications[0][0] == "Journal failed · Migrate goose 0023"
    assert ctl.notifications[0][2] == "crit"


def test_journaling_session_ignores_other_actions():
    store, ctl, sched, rec = demo_controller()
    ctl.close_session(LEDGERLY, journal=True)
    rec.clear()
    ctl.request_close(LEDGERLY)
    ctl.close_session(LEDGERLY, journal=True)
    ctl.focus_session(LEDGERLY)
    assert rec.events == []
    sched.run_all()
    assert len(store.state.journal.entries) == 4      # journaled exactly once


def test_receipt_ids_are_unique_within_a_minute():
    store, ctl, sched, rec = demo_controller("many")
    ctl.close_session("claude-ledgerly", journal=True)
    ctl.close_session("claude-ledgerly-2", journal=True)
    sched.run_all()
    ids = [e.id for e in store.state.journal.entries]
    assert len(ids) == len(set(ids))


def test_unknown_session_toasts():
    store, ctl, sched, rec = demo_controller()
    for call in (lambda: ctl.request_close("nope"), lambda: ctl.close_session("nope", True),
                 lambda: ctl.focus_session("nope"), lambda: ctl.retitle_session("nope"),
                 lambda: ctl.answer_prompt("nope", 0, "")):
        rec.clear()
        call()
        assert rec.last_toast()["error"] and "nope" in rec.last_toast()["text"]


# ── close all ──

def test_close_all_is_sequential():
    store, ctl, sched, rec = demo_controller()
    ctl.close_all()
    # first in display order is the waiting (busy) smartPanel session: journaling
    journaling = [s.name for s in store.state.sessions.items if s.phase == PHASE_JOURNALING]
    assert journaling == [SMART]
    sched.advance(JOURNAL_BUSY_WAIT_MS + 1200 + 2000 + 700 + 600)
    assert find(store, SMART) is None
    journaling = [s.name for s in store.state.sessions.items if s.phase == PHASE_JOURNALING]
    assert journaling == ["claude-tacomap"]
    sched.run_all()
    assert store.state.sessions.items == []
    assert rec.last_toast()["text"] == "All sessions closed"
    # JobScout is 12 min old: journaled too (min age 2 min); all four have receipts
    assert len(store.state.journal.entries) == 3 + 4


def test_close_all_closes_young_sessions_without_journal():
    cfg = Config({"sessions.min_age_s": 3600})
    store, ctl, sched, rec = demo_controller(config=cfg)
    ctl.close_all()
    sched.run_all()
    assert store.state.sessions.items == []
    # only smartPanel (62 min) and ledgerly (190 min) are old enough
    assert len(store.state.journal.entries) == 3 + 2
    texts = [t["text"] for t in rec.toasts()]
    assert "Closed “Truck search filters”" in texts
    assert texts[-1] == "All sessions closed"


def test_close_all_without_sessions():
    store, ctl, sched, rec = demo_controller("empty")
    ctl.request_close_all()
    assert rec.of("confirm_close_all") == [] and rec.last_toast()["error"]


# ── new session ──

def test_new_session_launches_then_goes_idle():
    store, ctl, sched, rec = demo_controller()
    ctl.new_session("ledgerly")
    s = find(store, "claude-ledgerly-2")
    assert s.phase == PHASE_LAUNCHING and s.activity == UNKNOWN
    assert s.cwd == "~/dev/omdev/ledgerly" and s.project == "ledgerly" and s.title == ""
    assert rec.toasts() == []
    sched.advance(LAUNCH_MS - 1)
    assert find(store, "claude-ledgerly-2").phase == PHASE_LAUNCHING
    sched.advance(1)
    s = find(store, "claude-ledgerly-2")
    assert s.phase == PHASE_NONE and s.activity == IDLE and s.attached
    assert rec.last_toast() == {"text": "Session started in ~/dev/omdev/ledgerly",
                                "error": False, "action": None}


def test_new_session_terminal_missing():
    store, ctl, sched, rec = demo_controller()
    ctl.terminal_missing = True
    ctl.new_session("/tmp/x")
    assert len(store.state.sessions.items) == 4
    assert store.state.sessions.terminal_ok is False
    t = rec.last_toast()
    assert t["error"] and t["text"] == ("Couldn’t open Terminator. Install it, or set "
                                        "SMARTPANEL_TERMINAL.")


def test_new_session_without_tmux():
    store, ctl, sched, rec = demo_controller("notmux")
    ctl.request_new_session()
    assert rec.of("new_session") == []
    assert rec.of("open_dashboard") == [{"page": "sessions"}]
    assert rec.last_toast()["text"] == "Install tmux first"


def test_list_start_folders():
    cfg = Config({"sessions.dir": "/srv/code"})
    store, ctl, sched, rec = demo_controller(config=cfg)
    folders = ctl.list_start_folders()
    assert [f["name"] for f in folders] == list(PROJECTS)
    by = {f["name"]: f for f in folders}
    assert by["ledgerly"] == {"name": "ledgerly", "path": "/srv/code/ledgerly", "has_session": True}
    assert by["wishlist"]["has_session"] is False


# ── answer a prompt ──

def test_answer_prompt_runs_then_settles():
    store, ctl, sched, rec = demo_controller()
    fp = find(store, SMART).prompt.fingerprint
    ctl.answer_prompt(SMART, 0, fp)
    s = find(store, SMART)
    assert s.activity == RUNNING and s.doing == APPLYING and s.prompt is None
    assert s.waiting_since is None
    assert rec.last_toast()["text"] == "Sent “1” to Fix swap alert hysteresis"
    assert store.state.sessions.count(WAITING) == 0
    assert [x.activity for x in store.state.sessions.items] == [RUNNING] * 3 + [IDLE]
    sched.advance(5000)
    assert find(store, SMART).activity == IDLE and find(store, SMART).doing is None


def test_answer_no_goes_straight_to_idle():
    store, ctl, sched, rec = demo_controller()
    ctl.answer_prompt(SMART, 2, "")
    assert find(store, SMART).activity == IDLE
    assert rec.last_toast()["text"] == "Sent “3” to Fix swap alert hysteresis"


def test_answer_refuses_stale_fingerprint_and_bad_option():
    store, ctl, sched, rec = demo_controller()
    ctl.answer_prompt(SMART, 0, "deadbeef")
    assert find(store, SMART).activity == WAITING and rec.last_toast()["error"]
    ctl.answer_prompt(SMART, 7, "")
    assert find(store, SMART).activity == WAITING and rec.last_toast()["error"]
    ctl.answer_prompt(LEDGERLY, 0, "")
    assert "isn’t waiting" in rec.last_toast()["text"]


# ── focus / retitle ──

def test_focus_reattach_route():
    store, ctl, sched, rec = demo_controller()
    ctl.focus_session(LEDGERLY)
    assert find(store, LEDGERLY).attached
    assert rec.last_toast()["text"] == ("Re-attached “Migrate goose 0023” in a new terminal. "
                                        "The old window closed.")


def test_focus_raise_route():
    store, ctl, sched, rec = demo_controller(env={"SMARTPANEL_DEMO_FOCUS_ROUTE": "raise"})
    ctl.focus_session(LEDGERLY)
    assert rec.last_toast()["text"] == "Raised the terminal for “Migrate goose 0023”"


def test_focus_without_reattach():
    store, ctl, sched, rec = demo_controller(config=Config({"sessions.reattach": False}))
    ctl.focus_session(LEDGERLY)
    t = rec.last_toast()
    assert t["error"] and t["action"] == {"label": "Settings", "page": "settings"}


def test_retitle_toasts_after_delay():
    store, ctl, sched, rec = demo_controller()
    ctl.retitle_session(LEDGERLY)
    assert rec.toasts() == []
    sched.advance(700)
    assert rec.last_toast()["text"] == "Title re-read from tmux"


# ── settings ──

def test_set_setting_valid_and_invalid():
    cfg = Config()
    store, ctl, sched, rec = demo_controller(config=cfg)
    ctl.set_setting("swap.warn", 70)
    assert cfg.get("swap.warn") == 70 and store.state.system.swap.warn_at == 70
    ctl.set_setting("swap.warn", "lots")
    assert rec.last_toast()["error"] and cfg.get("swap.warn") == 70
    ctl.set_setting("nope", 1)
    assert "unknown setting" in rec.last_toast()["text"]


def test_set_setting_refuses_env_override():
    cfg = Config(env={"SMARTPANEL_SWAP_WARN": "50"})
    store, ctl, sched, rec = demo_controller(config=cfg)
    assert store.state.settings.overridden_by == {"swap.warn": "SMARTPANEL_SWAP_WARN"}
    ctl.set_setting("swap.warn", 70)
    assert "SMARTPANEL_SWAP_WARN" in rec.last_toast()["text"]


def test_thresholds_apply_without_restart():
    cfg = Config()
    store, ctl, sched, rec = demo_controller("alert", config=cfg)
    assert store.state.system.swap.level == LEVEL_CRITICAL
    ctl.set_setting("swap.crit", 95)
    assert store.state.system.swap.level == LEVEL_WARNING
    assert store.state.system.swap.crit_at == 95
    cfg2 = Config({"swap.warn": 90, "swap.crit": 95})
    store, ctl, sched, rec = demo_controller("alert", config=cfg2)
    assert store.state.system.swap.level == LEVEL_OK


def test_budget_follows_config():
    cfg = Config()
    store, ctl, sched, rec = demo_controller(config=cfg)
    assert store.state.spend.forecast.budget_usd == 40     # the prototype's default
    ctl.set_setting("budget.daily_usd", 20)
    fc = store.state.spend.forecast
    assert fc.budget_usd == 20 and fc.eta_reason == "over" and fc.over_by_usd == 3.84
    ctl.set_setting("budget.daily_usd", None)
    assert store.state.spend.forecast.budget_usd is None
    store, ctl, *_ = demo_controller(config=Config({"budget.daily_usd": 100}))
    assert store.state.spend.forecast.budget_usd == 100


def test_recheck_environment():
    store, ctl, sched, rec = demo_controller("notmux")
    ctl.recheck_environment()
    assert rec.last_toast()["text"] == "tmux still not found on PATH"
    store, ctl, sched, rec = demo_controller()
    ctl.recheck_environment()
    assert not rec.last_toast()["error"] and store.state.sessions.terminal_ok


# ── simulated clocks ──

def test_cold_start_animates_loading_then_fills_spend():
    store, ctl, sched, rec = demo_controller("cold")
    assert store.state.app.loading.done == 0
    sched.advance(350)
    assert store.state.app.loading.done == 97
    sched.advance(350 * 12)
    assert store.state.app.loading is not None
    sched.advance(350)
    assert store.state.app.loading is None
    assert sched.now_ms == 350 * 14            # ~5 s, as in the prototype
    assert store.state.spend.cost_today == 23.84
    assert store.state.spend.forecast.budget_usd == 40
    assert rec.last_toast()["text"] == "Read 1,284 transcripts"
    assert sched.pending == 0


def test_live_jitter_moves_histories_within_bounds():
    store, ctl, sched, rec = demo_controller(live=True)
    n = len(store.state.system.cpu.history)
    sched.advance(3000 * 5)
    cpu = store.state.system.cpu
    assert len(cpu.history) == n + 5
    assert all(1 <= v <= 100 for v in cpu.history[-5:])
    assert {"system"} in rec.changes
    ctl.stop()
    assert sched.pending == 0


def test_default_scheduler_is_glib():
    """Constructs with the real GLib scheduler; nothing runs without a main loop."""
    store = store_for("busy")
    ctl = DemoController(store, Config(), env={}, notifier=lambda *a: None, live=True)
    try:
        assert ctl._timers
    finally:
        ctl.stop()
    assert not ctl._timers


def test_works_without_config():
    store = store_for("busy")
    sched = FakeScheduler()
    rec = Recorder(store)
    ctl = DemoController(store, None, scheduler=sched, clock=sched.time, env={},
                         notifier=lambda *a: None, live=False)
    ctl.request_close(LEDGERLY)
    assert rec.of("confirm_close")
    ctl.set_setting("swap.warn", 1)
    assert rec.last_toast()["error"]


# ── review fixes ──

@pytest.mark.parametrize("min_age, words", [(120, "2 min"), (30, "30 s"), (90, "90 s"),
                                            (4 * 3600, "4 h")])
def test_short_session_toast_words_the_threshold(min_age, words):
    cfg = Config({"sessions.min_age_s": min_age})
    store, ctl, sched, rec = demo_controller(config=cfg)
    ctl.new_session("~/dev/omdev/wishlist")
    sched.advance(LAUNCH_MS)
    ctl.request_close("claude-wishlist")
    assert rec.last_toast()["text"] == f"Closed “wishlist”. Open under {words}, so no journal."


@pytest.mark.parametrize("option", ["x", None, "1.5"])
def test_answer_prompt_with_garbage_option_toasts(option):
    store, ctl, sched, rec = demo_controller()
    ctl.answer_prompt(SMART, option, "")
    assert rec.last_toast()["error"]
    assert find(store, SMART).activity == WAITING


def test_demo_never_writes_the_config_file(tmp_path):
    path = tmp_path / "smartpanel" / "config.yaml"
    cfg = Config.load(path, env={})
    store, ctl, sched, rec = demo_controller(config=cfg)
    ctl.set_hud(True)
    ctl.set_setting("swap.warn", 50)
    assert cfg.get("swap.warn") == 50
    assert not path.exists() and not any(tmp_path.rglob("*"))
    cfg2 = Config.load(path, env={})
    store, ctl, *_ = demo_controller(config=cfg2, persist=True)
    ctl.set_setting("swap.warn", 55)
    assert path.exists()


def test_each_demo_notification_has_its_own_key(monkeypatch):
    from smartpanel import notify
    keys = []
    monkeypatch.setattr(notify, "send", lambda *a, **kw: keys.append(kw.get("key")))
    store, ctl, *_ = demo_controller()
    ctl._notifier = None
    ctl._notify("a", "b", "info")
    ctl._notify("c", "d", "info")
    assert len(set(keys)) == 2
