"""SpendService: worker hand-off, loading/first-run, session cost, budget alerts
(SP-0801, SP-2601, SP-2603, SP-1401, SP-0902). Scheduling is injected, so the
thread hand-off is driven step by step without a display or a main loop."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from smartpanel.claude_stats.aggregator import Aggregator
from smartpanel.config import Config
from smartpanel.services import spend as spend_mod
from smartpanel.services.spend import SpendService, default_notifier
from smartpanel.state.model import Loading
from smartpanel.state.store import Store
from tests.fixtures import ORIGINAL, OTHER, PARIS, RESUMED, SPEND_NOW, copy_spend_tree


class FakeScheduler:
    def __init__(self):
        self.timers: dict[int, tuple[int, object]] = {}
        self.threads: list = []
        self.idle: list = []
        self._next = 1

    def timeout_add_seconds(self, seconds, fn):
        tid = self._next
        self._next += 1
        self.timers[tid] = (seconds, fn)
        return tid

    def source_remove(self, tid):
        self.timers.pop(tid, None)

    def idle_add(self, fn):
        self.idle.append(fn)

    def run_in_thread(self, fn, name=""):
        self.threads.append(fn)

    # drive
    def run_threads(self):
        while self.threads:
            self.threads.pop(0)()

    def pump(self):
        while self.idle:
            self.idle.pop(0)()

    def settle(self):
        while self.threads or self.idle:
            self.run_threads()
            self.pump()


def make(tmp_path, root=None, budget=None, notifications=True, **kw):
    root = root if root is not None else copy_spend_tree(tmp_path)
    store = Store()
    cfg = Config({"budget.daily_usd": budget, "notifications.budget": notifications})
    sched = FakeScheduler()
    sent = []
    svc = SpendService(
        store, cfg, root, scheduler=sched,
        aggregator=Aggregator(claude_root=root, now_fn=lambda: SPEND_NOW, tz=PARIS),
        notifier=lambda kind, title, body, action: sent.append((kind, title, body, action)),
        clock=lambda: 1000.0, **kw)
    return svc, store, cfg, sched, sent, root


def test_start_schedules_a_15s_tick_and_scans_on_a_worker(tmp_path):
    svc, store, _, sched, _, _ = make(tmp_path)
    svc.start()
    assert [s for s, _ in sched.timers.values()] == [15]
    assert len(sched.threads) == 1                     # nothing ran on the "main loop"
    assert store.state.spend.cost_today is None
    sched.settle()
    sp = store.state.spend
    assert sp.cost_today > 0 and sp.updated_at == 1000.0
    assert len(sp.days) == 14 and sp.days[0].label == "Today"
    assert store.state.app.loading is None
    assert store.state.app.first_run is False


def test_cold_scan_reports_progress_then_clears_it(tmp_path, monkeypatch):
    monkeypatch.setattr(spend_mod, "PROGRESS_MIN_INTERVAL_S", 0.0)
    svc, store, _, sched, _, _ = make(tmp_path)
    seen = []
    store.subscribe(lambda secs: "app" in secs and seen.append(store.state.app.loading))
    svc.start()
    sched.run_threads()
    # Progress and the result are queued in order; progress lands first.
    sched.pump()
    assert seen[:3] == [Loading(1, 3), Loading(2, 3), Loading(3, 3)]
    assert seen[-1] is None
    assert store.state.app.loading is None


def test_progress_is_throttled_but_always_reports_the_last_file(tmp_path):
    svc, store, _, sched, _, _ = make(tmp_path)
    seen = []
    store.subscribe(lambda secs: "app" in secs and seen.append(store.state.app.loading))
    svc.start()
    sched.settle()
    loads = [x for x in seen if x is not None]
    assert loads[-1] == Loading(3, 3)
    assert len(loads) < 3


def test_later_scans_do_not_show_loading(tmp_path):
    svc, store, _, sched, _, _ = make(tmp_path)
    svc.start()
    sched.settle()
    seen = []
    store.subscribe(lambda secs: seen.append(store.state.app.loading))
    svc.refresh_now()
    sched.settle()
    assert all(x is None for x in seen)


def test_a_tick_while_busy_is_skipped(tmp_path):
    svc, _, _, sched, _, _ = make(tmp_path)
    svc.start()
    (_, timer_fn), = sched.timers.values()
    assert timer_fn() is True                          # keeps the timer
    assert len(sched.threads) == 1                     # but no second worker
    sched.settle()
    assert timer_fn() is True
    assert len(sched.threads) == 1


def test_refresh_now_while_busy_runs_once_more_after(tmp_path):
    svc, _, _, sched, _, _ = make(tmp_path)
    svc.start()
    svc.refresh_now()
    svc.refresh_now()
    assert len(sched.threads) == 1
    sched.run_threads()
    sched.pump()                                       # apply -> rerun queued
    assert len(sched.threads) == 1
    sched.settle()
    assert not sched.threads


def test_stop_removes_the_timer(tmp_path):
    svc, _, _, sched, _, _ = make(tmp_path)
    svc.start()
    (_, timer_fn), = sched.timers.values()
    svc.stop()
    assert sched.timers == {}
    assert timer_fn() is False


def test_first_run_when_projects_dir_is_missing(tmp_path):
    svc, store, _, sched, _, _ = make(tmp_path, root=tmp_path / "missing")
    svc.start()
    sched.settle()
    assert store.state.app.first_run is True
    assert store.state.app.loading is None
    assert store.state.spend.cost_today is None


def test_first_run_when_projects_dir_has_no_transcripts(tmp_path):
    root = tmp_path / "projects"
    (root / "-home-x").mkdir(parents=True)
    (root / "-home-x" / "notes.txt").write_text("hi")
    svc, store, _, sched, _, _ = make(tmp_path, root=root)
    svc.start()
    sched.settle()
    assert store.state.app.first_run is True


def test_first_run_clears_once_a_transcript_appears(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    svc, store, _, sched, _, _ = make(tmp_path, root=root)
    svc.start()
    sched.settle()
    assert store.state.app.first_run is True
    copy_spend_tree(tmp_path / "later")
    import shutil
    shutil.copytree(tmp_path / "later" / "projects", root, dirs_exist_ok=True)
    svc.refresh_now()
    sched.settle()
    assert store.state.app.first_run is False


def test_a_failing_scan_clears_loading_and_frees_the_worker(tmp_path):
    svc, store, _, sched, _, _ = make(tmp_path)

    def boom(progress=None):
        if progress:
            progress(1, 3)
        raise RuntimeError("disk on fire")
    svc._agg.refresh = boom
    svc.start()
    sched.settle()
    assert store.state.app.loading is None
    assert svc._busy is False


# ── SP-1401: session cost ──

def test_session_cost_reads_the_per_file_cache(tmp_path):
    svc, store, _, sched, _, root = make(tmp_path)
    assert svc.session_cost(root / RESUMED) is None    # nothing scanned yet
    svc.start()
    sched.settle()
    snap = svc.snapshot
    assert svc.session_cost(root / RESUMED) == snap.per_file_cost[str(root / RESUMED)]
    assert svc.session_cost(str(root / ORIGINAL)) > 0
    assert svc.session_cost(root / "nope.jsonl") is None
    assert svc.session_cost(None) is None
    total = sum(svc.session_cost(root / p) for p in (ORIGINAL, RESUMED, OTHER))
    assert total == pytest.approx(store.state.spend.cost_lifetime)


def test_session_cost_does_not_reparse(tmp_path):
    svc, _, _, sched, _, root = make(tmp_path)
    svc.start()
    sched.settle()
    before = svc.session_cost(root / RESUMED)
    (root / RESUMED).write_text("")                     # changed on disk, not rescanned
    assert svc.session_cost(root / RESUMED) == before


def test_session_cost_through_a_symlinked_root(tmp_path):
    real = copy_spend_tree(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(real)
    svc, _, _, sched, _, _ = make(tmp_path, root=link)
    svc.start()
    sched.settle()
    assert svc.session_cost(real / RESUMED) == svc.session_cost(link / RESUMED)
    assert svc.session_cost(real / RESUMED) is not None


def test_days_on_demand(tmp_path):
    svc, _, _, sched, _, _ = make(tmp_path)
    assert svc.days(30) == []
    svc.start()
    sched.settle()
    assert len(svc.days(30)) == 30


# ── forecast + budget notifications ──

def test_forecast_follows_the_budget_setting(tmp_path):
    svc, store, cfg, sched, _, _ = make(tmp_path, budget=None)
    svc.start()
    sched.settle()
    assert store.state.spend.forecast.budget_usd is None
    cfg.set("budget.daily_usd", 100.0)
    fc = store.state.spend.forecast
    assert fc.budget_usd == 100.0
    assert fc.pct == pytest.approx(store.state.spend.cost_today)


def test_budget_notifications_once_per_day(tmp_path):
    svc, store, cfg, sched, sent, _ = make(tmp_path, budget=None)
    svc.start()
    sched.settle()
    today = store.state.spend.cost_today
    assert sent == []
    cfg.set("budget.daily_usd", today / 0.85)          # 85 %
    assert [k for k, *_ in sent] == ["budget_80"]
    assert sent[0][3] == {"label": "View", "page": "overview"}
    svc.refresh_now()
    sched.settle()
    assert len(sent) == 1
    cfg.set("budget.daily_usd", today / 1.2)           # 120 %
    assert [k for k, *_ in sent] == ["budget_80", "budget_100"]
    assert sent[1][1] == "Daily budget reached"
    svc.refresh_now()
    sched.settle()
    assert len(sent) == 2


def test_budget_over_on_first_scan_sends_only_100(tmp_path):
    svc, _, _, sched, sent, _ = make(tmp_path, budget=0.01)
    svc.start()
    sched.settle()
    assert [k for k, *_ in sent] == ["budget_100"]


def test_no_budget_notifications_when_turned_off(tmp_path):
    svc, _, _, sched, sent, _ = make(tmp_path, budget=0.01, notifications=False)
    svc.start()
    sched.settle()
    assert sent == []


def test_no_budget_no_notifications(tmp_path):
    svc, _, _, sched, sent, _ = make(tmp_path, budget=None)
    svc.start()
    sched.settle()
    assert sent == []


# ── default notifier speaks both notify.send signatures ──

def test_default_notifier_uses_the_v2_signature(monkeypatch):
    from smartpanel import notify
    calls = []

    def send(kind, title, body, action=None):
        calls.append((kind, title, body, action))
    monkeypatch.setattr(notify, "send", send)
    default_notifier("budget_80", "T", "B", {"label": "View", "page": "overview"})
    assert calls == [("budget_80", "T", "B", {"label": "View", "page": "overview"})]


def test_default_notifier_falls_back_to_the_v1_signature(monkeypatch):
    from smartpanel import notify
    calls = []

    def send(summary, body="", urgency="normal", key="smartpanel"):
        calls.append((summary, body, urgency, key))
    monkeypatch.setattr(notify, "send", send)
    default_notifier("budget_100", "T", "B", None)
    assert calls == [("T", "B", "normal", "budget_100")]


def test_default_notifier_never_raises(monkeypatch):
    from smartpanel import notify

    def send(*a, **k):
        raise OSError("dbus down")
    monkeypatch.setattr(notify, "send", send)
    default_notifier("budget_100", "T", "B", None)


# ── the real GLib hand-off: store is only touched on the main thread ──

def test_real_glib_scheduler_hands_results_to_the_main_thread(tmp_path):
    GLib = pytest.importorskip("gi.repository.GLib")
    root = copy_spend_tree(tmp_path)
    store = Store()
    threads = set()
    store.subscribe(lambda secs: threads.add(threading.current_thread()))
    svc = SpendService(store, Config({}), root,
                       aggregator=Aggregator(claude_root=root, now_fn=lambda: SPEND_NOW,
                                             tz=PARIS),
                       notifier=lambda *a: None)
    loop = GLib.MainLoop()
    store.subscribe(lambda secs: "spend" in secs and loop.quit())
    GLib.timeout_add_seconds(5, lambda: (loop.quit(), False)[1])
    svc.start()
    loop.run()
    svc.stop()
    assert store.state.spend.cost_today and store.state.spend.cost_today > 0
    assert threads == {threading.main_thread()}


def test_cold_scan_of_1200_files_keeps_every_main_loop_callback_under_50ms(tmp_path):
    import json
    import time
    root = tmp_path / "projects"
    for p in range(12):
        pdir = root / f"-home-u-proj{p}"
        pdir.mkdir(parents=True)
        for f in range(100):
            line = {"type": "assistant", "timestamp": "2026-11-17T09:00:00Z",
                    "message": {"id": f"m{p}-{f}", "model": "claude-opus-4-7",
                                "role": "assistant",
                                "content": [{"type": "text", "text": "Done."}],
                                "usage": {"input_tokens": 10, "output_tokens": 20}}}
            (pdir / f"s{f}.jsonl").write_text(json.dumps(line) + "\n")
    svc, store, _, sched, _, _ = make(tmp_path, root=root, budget=40.0)
    timings = []
    original_tick = svc._tick

    def timed(fn):
        def run():
            t0 = time.perf_counter()
            fn()
            timings.append(time.perf_counter() - t0)
        return run
    sched.idle_add = lambda fn: sched.idle.append(timed(fn))
    t0 = time.perf_counter()
    original_tick()
    timings.append(time.perf_counter() - t0)        # the tick itself
    sched.settle()
    assert store.state.spend.cost_today > 0
    assert store.state.app.first_run is False
    assert max(timings) < 0.05, max(timings)
