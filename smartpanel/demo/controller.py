"""`DemoController`: the prototype's flows, simulated with timers (SP-0012, SP-1610).

Implements every verb of `state.actions.Actions` against a `Store` filled by
`demo.build_state`, with the prototype's timings and copy, so every FE task can be
checked (and screenshotted) without tmux, terminals or the real `/journal` skill.

Timers go through an injectable scheduler (`add(delay_ms, fn) -> handle`,
`cancel(handle)`); the default is `GLib.timeout_add`. Tests pass a fake scheduler and
drive virtual time, so no flow here needs a main loop to be tested. Each callback is a
few dict updates — well inside the 50 ms main-loop budget — and runs on the main thread,
the only one that mutates the Store.

Failure switches, as in the prototype's control panel (also readable from env):
    journal_fails     SMARTPANEL_DEMO_JOURNAL_FAILS=1    the skill fails at step 3
    focus_route       SMARTPANEL_DEMO_FOCUS_ROUTE=raise  'reattach' (stock Wayland) | 'raise'
    terminal_missing  SMARTPANEL_DEMO_TERMINAL_MISSING=1 Terminator isn't installed
"""
from __future__ import annotations

import itertools
import logging
import os
import random
import time
from typing import Any, Callable, Mapping

from smartpanel.alerts import Thresholds, next_level, Level
from smartpanel.demo import scenarios as sc
from smartpanel.state.model import (
    IDLE, JOURNAL_STEPS, OUTCOME_JOURNALED, OUTCOME_SKILL_FAILED, PHASE_JOURNALING,
    PHASE_LAUNCHING, PHASE_NONE, RUNNING, UNKNOWN, WAITING,
    Event, JournalEntry, JournalProgress, Session,
)

log = logging.getLogger(__name__)

# Prototype timings (ms).
JOURNAL_DELAYS_MS = (800, 1200, 2000, 700, 600)   # step 0 is 2400 when the session is busy
JOURNAL_BUSY_WAIT_MS = 2400
JOURNAL_FAIL_MS = 1500
CLOSE_ALL_GAP_MS = 300
LAUNCH_MS = 1800
ANSWER_SETTLE_MS = 5000
RETITLE_MS = 700
LOAD_TICK_MS, LOAD_STEP = 350, 97
LIVE_TICK_MS = 3000
HISTORY_MAX = 300
APPLYING = "Edit · applying your answer"


class GLibScheduler:
    """One-shot timers on the GLib main loop."""

    def add(self, delay_ms: int, fn: Callable[[], None]) -> int:
        from gi.repository import GLib

        def _once() -> bool:
            try:
                fn()
            except Exception:
                log.exception("demo timer failed")
            return False
        return GLib.timeout_add(int(delay_ms), _once)

    def cancel(self, handle: int) -> None:
        from gi.repository import GLib
        try:
            GLib.source_remove(handle)
        except Exception:  # already fired
            pass


def _flag(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _fmt_age(minutes: float) -> str:
    m = round(minutes)
    if m < 1:
        return "now"
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h{m % 60:02d}"


def _fmt_threshold(seconds: float) -> str:
    """A minimum age as the prototype words it: "2 min", "30 s", "4 h", "90 s"."""
    s = int(round(seconds))
    if s >= 3600 and s % 3600 == 0:
        return f"{s // 3600} h"
    if s >= 60 and s % 60 == 0:
        return f"{s // 60} min"
    return f"{s} s"


def _tilde(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


class DemoController:
    def __init__(self, store, config, *, scheduler=None, clock: Callable[[], float] | None = None,
                 env: Mapping[str, str] | None = None,
                 notifier: Callable[[str, str, str], None] | None = None,
                 autostart: bool = True, live: bool = True, seed: int = 17,
                 persist: bool = False):
        """`store` holds a demo state (see `build_state`); `config` a `Config`.

        `notifier(title, body, kind)` shows the prototype's desktop notifications; the
        default is `smartpanel.notify.send`. Tests pass a recorder. `live` jitters CPU,
        memory and network every 3 s like the prototype; `autostart` starts that (and the
        cold scenario's loading animation) immediately.

        Unless `persist=True`, the config is detached from its file (`config.path = None`)
        so toggling settings in `--demo` never rewrites the user's real config.yaml.
        """
        self.store, self.config = store, config
        self.scheduler = scheduler or GLibScheduler()
        self.clock = clock or time.time
        env = os.environ if env is None else env
        self.journal_fails = _flag(env, "SMARTPANEL_DEMO_JOURNAL_FAILS")
        route = env.get("SMARTPANEL_DEMO_FOCUS_ROUTE", "reattach").strip().lower()
        self.focus_route = route if route in ("reattach", "raise") else "reattach"
        self.terminal_missing = _flag(env, "SMARTPANEL_DEMO_TERMINAL_MISSING")
        self._notifier = notifier
        self._live = live
        self._rng = random.Random(seed)
        self._note_ids = itertools.count(1)
        self._timers: set[int] = set()
        self._journaling: set[str] = set()
        self._started = False
        self._unsub: list[Callable[[], None]] = []
        self._swap_level = Level(store.state.system.swap.level)
        self._budget = store.state.spend.forecast.budget_usd
        if self._budget is None and store.state.app.loading is not None:
            self._budget = sc.DEMO_BUDGET

        if config is not None:
            if not persist and hasattr(config, "path"):
                config.path = None
            if hasattr(config, "bind_store"):
                self._unsub.append(config.bind_store(store))
            self._unsub.append(config.subscribe(self._on_setting))
            self._apply_thresholds()
            if self._cfg("budget.daily_usd", None) is not None:
                self._budget = self._cfg("budget.daily_usd")
                self._apply_budget()
        if autostart:
            self.start()

    # ───────────────────────── lifecycle ─────────────────────────
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if self.store.state.app.loading is not None:
            self._after(LOAD_TICK_MS, self._load_tick)
        if self._live:
            self._after(LIVE_TICK_MS, self._live_tick)

    def stop(self) -> None:
        for h in list(self._timers):
            self.scheduler.cancel(h)
        self._timers.clear()
        for u in self._unsub:
            u()
        self._unsub.clear()
        self._started = False

    def _after(self, ms: int, fn: Callable[[], None]) -> None:
        box: list[int] = []

        def run() -> None:
            if box:
                self._timers.discard(box[0])
            fn()
        h = self.scheduler.add(ms, run)
        box.append(h)
        self._timers.add(h)

    # ───────────────────────── helpers ─────────────────────────
    def _cfg(self, key: str, default: Any = None) -> Any:
        if self.config is None:
            return default
        try:
            return self.config.get(key)
        except Exception:
            return default

    def _find(self, name: str) -> Session | None:
        for s in self.store.state.sessions.items:
            if s.name == name:
                return s
        return None

    def _known(self, name: str) -> Session | None:
        s = self._find(name)
        if s is None:
            self.store.toast(f"No session named “{name}”", error=True)
        return s

    def _age_s(self, s: Session) -> float:
        return max(0.0, self.clock() - s.created)

    def _sessions_changed(self, fn: Callable[[list[Session]], None] | None = None) -> None:
        def mutate(sec) -> None:
            if fn:
                fn(sec.items)
            sec.items[:] = sc.sort_sessions(sec.items)
            sec.updated_at = self.clock()
        self.store.mutate("sessions", mutate)

    def _remove(self, name: str) -> None:
        self._sessions_changed(lambda items: items.__setitem__(
            slice(None), [x for x in items if x.name != name]))

    def _notify(self, title: str, body: str, kind: str) -> None:
        try:
            if self._notifier is not None:
                self._notifier(title, body, kind)
            else:
                from smartpanel import notify
                notify.send(title, body, urgency="critical" if kind == "crit" else "normal",
                            key=f"smartpanel-demo-{next(self._note_ids)}")
        except Exception:
            log.exception("demo notification failed")

    def _busy(self, s: Session) -> bool:
        return s.phase in (PHASE_JOURNALING, PHASE_LAUNCHING)

    # ───────────────────────── navigation / surfaces ─────────────────────────
    def open_dashboard(self, page: str = "") -> None:
        self.store.emit(Event("open_dashboard", {"page": page}))

    def open_palette(self) -> None:
        self.store.emit(Event("open_palette", {}))

    def set_hud(self, enabled: bool) -> None:
        self.set_setting("hud.enabled", bool(enabled))

    def pin_ticker(self, pinned: bool) -> None:
        self.store.update("app", ticker_pinned=bool(pinned))

    def quit(self) -> None:
        self.stop()
        self.store.emit(Event("quit", {}))

    # ───────────────────────── sessions ─────────────────────────
    def focus_session(self, name: str) -> None:
        s = self._known(name)
        if s is None or self._busy(s):
            return
        self._sessions_changed(lambda _i: setattr(s, "attached", True))
        title = s.display_title
        if self.focus_route == "raise":
            self.store.toast(f"Raised the terminal for “{title}”")
        elif self._cfg("sessions.reattach", True):
            self.store.toast(f"Re-attached “{title}” in a new terminal. The old window closed.")
        else:
            self.store.toast("Couldn’t raise the terminal. Turn on re-attach in Settings.",
                             error=True, action={"label": "Settings", "page": "settings"})

    def open_path(self, path: str) -> None:
        """Simulated: says what would happen, launches nothing."""
        name = os.path.basename(str(path or "").rstrip("/")) or str(path)
        self.store.toast(f"Opened {name} in the default editor")

    def retitle_session(self, name: str) -> None:
        s = self._known(name)
        if s is None or self._busy(s):
            return

        def done() -> None:
            if self._find(name) is not None:
                self.store.emit(Event("retitled", {"name": name}))
                self.store.toast("Title re-read from tmux")
        self._after(RETITLE_MS, done)

    def request_close(self, name: str) -> None:
        s = self._known(name)
        if s is None or self._busy(s):
            return
        journal_on = bool(self._cfg("sessions.journal", True))
        min_age = float(self._cfg("sessions.min_age_s", 120))
        if journal_on and self._age_s(s) >= min_age:
            self.store.emit(Event("confirm_close", {"name": name}))
            return
        title = s.display_title
        self._remove(name)
        if journal_on:
            self.store.toast(f"Closed “{title}”. Open under {_fmt_threshold(min_age)}, "
                             "so no journal.")
        else:
            self.store.toast(f"Closed “{title}”.")

    def close_session(self, name: str, journal: bool) -> None:
        s = self._known(name)
        if s is None or self._busy(s):
            return
        if not journal:
            self._remove(name)
            self.store.toast("Closed without a journal entry")
            return
        self._run_journal(name)

    def _run_journal(self, name: str, on_done: Callable[[], None] | None = None) -> None:
        """The five JOURNAL_STEPS with the prototype's delays; `journal_fails` breaks
        step 3 ("Skill appends…") and the session closes anyway."""
        s = self._find(name)
        if s is None:
            if on_done:
                on_done()
            return
        busy = s.activity != IDLE
        self._journaling.add(name)

        def start(_items) -> None:
            s.phase = PHASE_JOURNALING
            s.journal = JournalProgress(step=0)
            s.prompt = None
        self._sessions_changed(start)
        delays = (JOURNAL_BUSY_WAIT_MS if busy else JOURNAL_DELAYS_MS[0],) + JOURNAL_DELAYS_MS[1:]
        state = {"i": 0}

        def finish(failed: bool) -> None:
            self._journaling.discard(name)
            cur = self._find(name)
            if cur is None:
                if on_done:
                    on_done()
                return
            outcome = OUTCOME_SKILL_FAILED if failed else OUTCOME_JOURNALED
            cur.journal = JournalProgress(step=min(state["i"], len(JOURNAL_STEPS) - 1),
                                          failed=failed, outcome=outcome)
            entry = self._receipt(cur, failed)
            title = cur.display_title
            with self.store.batch():
                self._remove(name)
                self.store.mutate("journal", lambda j: j.entries.insert(0, entry))
            action = {"label": "View", "page": f"journal:{entry.id}"}
            if failed:
                self._notify(f"Journal failed · {title}",
                             "The session was closed anyway. A receipt with its activity "
                             "was saved.", "crit")
                self.store.toast("Closed. The journal failed, receipt saved.", error=True,
                                 action=action)
            else:
                self._notify(f"Journal written · {title}",
                             f"Appended to {self._log_name()}. Session closed.", "ok")
                self.store.toast(f"Journaled & closed “{title}”", action=action)
            if on_done:
                on_done()

        def step() -> None:
            cur = self._find(name)
            if cur is None:
                self._journaling.discard(name)
                if on_done:
                    on_done()
                return
            if state["i"] == 2 and self.journal_fails:
                self._sessions_changed(lambda _i: setattr(cur.journal, "failed", True))
                self._after(JOURNAL_FAIL_MS, lambda: finish(True))
                return
            state["i"] += 1
            if state["i"] >= len(JOURNAL_STEPS):
                finish(False)
                return
            self._sessions_changed(lambda _i: setattr(cur.journal, "step", state["i"]))
            self._after(delays[state["i"]], step)

        self._after(delays[0], step)

    def _log_name(self) -> str:
        path = self.store.state.journal.monthly_log_path or sc.monthly_log_path(self.clock())
        return "JOURNAL/" + path.rsplit("/", 1)[-1]

    def _receipt(self, s: Session, failed: bool) -> JournalEntry:
        now = self.clock()
        rid = base = sc.receipt_id(now, s.project or s.name)
        taken = {e.id for e in self.store.state.journal.entries}
        n = 2
        while rid in taken:
            rid, n = f"{base}-{n}", n + 1
        title = s.display_title
        line = ("The /journal skill returned no confirmation line. The session was still "
                "closed; this receipt holds the Activity section only." if failed else
                f"Journaled {s.project} to {self._log_name()}. {title}: what landed, what "
                "was decided, and what is still open.")
        return JournalEntry(
            id=rid, path=sc.receipt_path(rid), time=now, title=title, project=s.project,
            outcome=OUTCOME_SKILL_FAILED if failed else OUTCOME_JOURNALED, confirmation=line,
            duration_s=int(self._age_s(s)),
            files=[f"src/{s.project.lower()}/main.go", "README.md"], commands=["make test"],
            tools={"Read": 9, "Edit": 4, "Bash": 3})

    def request_close_all(self) -> None:
        if not self.store.state.sessions.items:
            self.store.toast("No sessions to close", error=True)
            return
        self.store.emit(Event("confirm_close_all", {}))

    def close_all(self) -> None:
        """Sequential, like the real skill lock: journal the old ones one at a time,
        close the young ones (or all, with journaling off) straight away."""
        names = [s.name for s in self.store.state.sessions.items]
        if not names:
            self.store.toast("No sessions to close", error=True)
            return

        def nxt(k: int) -> None:
            if k >= len(names):
                self.store.toast("All sessions closed")
                return
            s = self._find(names[k])
            if s is None or s.name in self._journaling:
                nxt(k + 1)
                return
            if (self._cfg("sessions.journal", True)
                    and s.phase != PHASE_LAUNCHING
                    and self._age_s(s) >= float(self._cfg("sessions.min_age_s", 120))):
                self._run_journal(s.name, on_done=lambda: nxt(k + 1))
            else:
                title = s.display_title
                self._remove(s.name)
                self.store.toast(f"Closed “{title}”")
                self._after(CLOSE_ALL_GAP_MS, lambda: nxt(k + 1))
        nxt(0)

    def request_new_session(self) -> None:
        if not self.store.state.sessions.tmux_ok:
            self.store.emit(Event("open_dashboard", {"page": "sessions"}))
            self.store.toast("Install tmux first", error=True)
            return
        self.store.emit(Event("new_session", {}))

    def _start_dir(self) -> str:
        return str(self._cfg("sessions.dir", sc.SESSIONS_DIR))

    def new_session(self, directory: str) -> None:
        if not self.store.state.sessions.tmux_ok:
            self.store.toast("Install tmux first", error=True)
            return
        terminal = str(self._cfg("sessions.terminal", "terminator"))
        term_label = "Terminator" if terminal == "terminator" else terminal
        if self.terminal_missing:
            self.store.update("sessions", terminal_ok=False)
            self.store.toast(f"Couldn’t open {term_label}. Install it, or set "
                             "SMARTPANEL_TERMINAL.", error=True)
            return
        directory = (directory or "").rstrip("/") or self._start_dir()
        if not directory.startswith(("/", "~")):
            directory = f"{self._start_dir().rstrip('/')}/{directory}"
        project = directory.rsplit("/", 1)[-1] or "session"
        taken = {s.name for s in self.store.state.sessions.items}
        name = sc.unique_name(project, taken)
        s = Session(name=name, project=project, title="", cwd=directory,
                    created=self.clock(), activity=UNKNOWN, phase=PHASE_LAUNCHING,
                    cost_usd=0.0)
        self._sessions_changed(lambda items: items.append(s))

        def ready() -> None:
            cur = self._find(name)
            if cur is None or cur.phase != PHASE_LAUNCHING:
                return

            def up(_i) -> None:
                cur.phase, cur.activity, cur.attached = PHASE_NONE, IDLE, True
            self._sessions_changed(up)
            self.store.toast(f"Session started in {_tilde(os.path.expanduser(directory))}")
        self._after(LAUNCH_MS, ready)

    def answer_prompt(self, name: str, option: int, fingerprint: str) -> None:
        s = self._known(name)
        if s is None:
            return
        title = s.display_title
        if s.activity != WAITING or s.prompt is None:
            self.store.toast(f"“{title}” isn’t waiting for an answer any more", error=True)
            return
        p = s.prompt
        if fingerprint and fingerprint != p.fingerprint:
            self.store.toast(f"The question in “{title}” changed. Nothing was sent.",
                             error=True)
            return
        try:
            idx = int(option)
        except (TypeError, ValueError):
            self.store.toast(f"“{title}” has no option {option!r}", error=True)
            return
        if not 0 <= idx < len(p.options):
            self.store.toast(f"“{title}” has no option {idx + 1}", error=True)
            return
        # The last of three+ options is "No, tell Claude what to do differently": back to you.
        to_idle = len(p.options) >= 3 and idx == len(p.options) - 1

        def up(_i) -> None:
            s.prompt, s.waiting_since = None, None
            s.activity = IDLE if to_idle else RUNNING
            s.doing = None if to_idle else APPLYING
        self._sessions_changed(up)
        self.store.toast(f"Sent “{idx + 1}” to {title}")
        if not to_idle:
            def settle() -> None:
                cur = self._find(name)
                if cur is not None and cur.activity == RUNNING and cur.doing == APPLYING:
                    def idle(_i) -> None:
                        cur.activity, cur.doing = IDLE, None
                    self._sessions_changed(idle)
            self._after(ANSWER_SETTLE_MS, settle)

    # ───────────────────────── settings ─────────────────────────
    def set_setting(self, key: str, value: Any) -> None:
        if self.config is None:
            self.store.toast("Settings aren’t available", error=True)
            return
        try:
            self.config.set(key, value)
        except ValueError as e:
            self.store.toast(str(e), error=True)

    def _on_setting(self, key: str, _value: Any) -> None:
        if key.startswith("swap."):
            self._apply_thresholds()
        elif key == "budget.daily_usd":
            self._budget = _value
            self._apply_budget()

    def _thresholds(self) -> Thresholds:
        return Thresholds(warning=float(self._cfg("swap.warn", 60)),
                          critical=float(self._cfg("swap.crit", 85)),
                          hysteresis=float(self._cfg("swap.hysteresis", 5)),
                          repeat_s=float(self._cfg("swap.repeat_s", 600)))

    def _apply_thresholds(self) -> None:
        th = self._thresholds()
        swap = self.store.state.system.swap
        level = next_level(swap.percent, self._swap_level, th)
        self._swap_level = level

        def up(sysstate) -> None:
            sysstate.swap.warn_at, sysstate.swap.crit_at = th.warning, th.critical
            sysstate.swap.level = int(level)
        self.store.mutate("system", up)

    def _apply_budget(self) -> None:
        """The demo keeps the prototype's $40 until a budget is configured; after that the
        config wins, and turning it off hides the forecast."""
        sp = self.store.state.spend
        if self.store.state.app.loading is not None or sp.cost_today is None:
            return
        fc = sc.compute_forecast(sp.cost_today, sp.burn_per_hour, self._budget, self.clock())
        self.store.update("spend", forecast=fc)

    def list_start_folders(self) -> list[dict]:
        base = self._start_dir().rstrip("/")
        active = {s.project for s in self.store.state.sessions.items}
        return [{"name": p, "path": os.path.expanduser(f"{base}/{p}"), "has_session": p in active}
                for p in sc.PROJECTS]

    def recheck_environment(self) -> None:
        sess = self.store.state.sessions
        if not sess.tmux_ok:
            self.store.toast("tmux still not found on PATH", error=True)
            return
        if self.terminal_missing:
            self.store.update("sessions", terminal_ok=False)
            self.store.toast("Terminator still not found on PATH", error=True)
            return
        self.store.update("sessions", terminal_ok=True)
        self.store.toast("tmux and Terminator found")

    # ───────────────────────── simulated clocks ─────────────────────────
    def _load_tick(self) -> None:
        loading = self.store.state.app.loading
        if loading is None:
            return
        done = min(loading.total, loading.done + LOAD_STEP)
        if done >= loading.total:
            spend = sc.loaded_spend(self.clock(), self._budget)
            with self.store.batch():
                self.store.update("app", loading=None)
                self.store.replace("spend", spend)
            self.store.toast(f"Read {loading.total:,} transcripts")
            return
        self.store.update("app", loading=type(loading)(done=done, total=loading.total))
        self._after(LOAD_TICK_MS, self._load_tick)

    def _live_tick(self) -> None:
        r = self._rng

        def jitter(sysstate) -> None:
            cpu, mem, net = sysstate.cpu, sysstate.memory, sysstate.net
            if cpu.percent is not None:
                cpu.percent = round(min(100.0, max(1.0, cpu.percent + (r.random() - .5) * 10)), 1)
                cpu.history = (cpu.history + [cpu.percent])[-HISTORY_MAX:]
            if mem.percent is not None:
                mem.percent = round(min(100.0, max(1.0, mem.percent + (r.random() - .5) * 1.2)), 1)
                mem.history = (mem.history + [mem.percent])[-HISTORY_MAX:]
                if mem.total_gb:
                    mem.used_gb = round(mem.percent / 100 * mem.total_gb, 2)
            if net.down_bps is not None:
                net.down_bps = round(min(9e6, max(1e5, net.down_bps + (r.random() - .5) * 1.4e6)))
                net.history_down = (net.history_down + [float(net.down_bps)])[-HISTORY_MAX:]
                if net.up_bps is not None:
                    net.history_up = (net.history_up + [float(net.up_bps)])[-HISTORY_MAX:]
            sysstate.updated_at = self.clock()
        self.store.mutate("system", jitter)
        self._after(LIVE_TICK_MS, self._live_tick)
