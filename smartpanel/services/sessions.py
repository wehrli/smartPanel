"""Session refresh: tmux -> the Store's `sessions` section (SP-1201..1204, 1304, 1401, 2605).

Every 2 s on the GLib loop: one `list-sessions`, then one `capture-pane` per
session (~2.4 ms each, a 4-session sweep measured 14 ms). That single capture
feeds all three readers — `activity.classify_pane`, `doing.describe_running`
and `prompt.parse_prompt` — so adding the detail lines cost no extra tmux calls.

On top of what tmux says, the panel layers its own phases:

- **launching**: a session the panel just started, shown as a placeholder row
  until tmux lists it (then a "Session started" toast) or for at most 10 s
  (then an error toast). The placeholder is replaced in the same tick the real
  row appears, so nothing flickers.
- **journaling**: close-with-journal in progress. The row keeps its last known
  shape and carries the current `JournalProgress`, and it stays listed after the
  journal run kills the tmux session, until the run reports its outcome — the UI
  never sees a session vanish and come back.

The result is sorted (`sessions.order`) and written only when it differs from
what the Store holds (dataclass equality is the signature), so an unchanged
list notifies nobody.

Slow things stay off the loop: per-session cost (transcript lookup + the spend
service's per-file cost) runs on a worker thread at most every 15 s and on the
spend aggregator's tick; results come back through `loop.idle`.
"""
from __future__ import annotations

import dataclasses
import logging
import re
import shutil
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from smartpanel.sessions import journal, tmux
from smartpanel.sessions.activity import RUNNING, WAITING, classify_pane
from smartpanel.sessions.doing import describe_running
from smartpanel.sessions.order import sort_sessions
from smartpanel.sessions.prompt import parse_prompt
from smartpanel.state.model import (
    PHASE_JOURNALING,
    PHASE_LAUNCHING,
    PHASE_NONE,
    UNKNOWN,
    JournalProgress,
    Session,
)
from smartpanel.uiformat import home_path, session_heading

log = logging.getLogger(__name__)

REFRESH_S = 2
ENV_CHECK_S = 30
COST_MIN_INTERVAL_S = 15.0
LAUNCH_TIMEOUT_S = 10.0
LAUNCH_PREFIX = "launching:"


# ───────────────────────── main-loop adapter ─────────────────────────

class GLibLoop:
    """The real loop: GLib timers and idle callbacks, daemon threads."""

    def __init__(self):
        from gi.repository import GLib
        self._GLib = GLib

    def timeout(self, seconds: float, fn: Callable[[], bool]):
        return self._GLib.timeout_add_seconds(max(1, int(seconds)), fn)

    def cancel(self, handle) -> None:
        try:
            self._GLib.source_remove(handle)
        except Exception:  # noqa: BLE001 — already gone
            pass

    def idle(self, fn: Callable[[], object]) -> None:
        def once():
            try:
                fn()
            except Exception:  # noqa: BLE001 — a callback must not kill the loop
                log.exception("idle callback failed")
            return False
        self._GLib.idle_add(once)

    def thread(self, fn: Callable[[], None], name: str = "smartpanel-worker") -> None:
        threading.Thread(target=fn, name=name, daemon=True).start()


def default_loop():
    try:
        return GLibLoop()
    except Exception:  # noqa: BLE001 — no gi: tests and headless tools pass their own
        log.warning("GLib unavailable; session refresh will not be scheduled")
        return None


# ───────────────────────── panel-owned state ─────────────────────────

@dataclass
class Launch:
    directory: str
    started: float
    known: frozenset[str]                 # tmux names that existed at launch time
    name_rx: re.Pattern = field(repr=False, default=None)  # type: ignore[assignment]

    @property
    def placeholder(self) -> str:
        return LAUNCH_PREFIX + self.directory


def _sanitize(name: str) -> str:
    """scripts/claude-tmux's sanitize(): what a folder name becomes in tmux."""
    s = re.sub(r"[^A-Za-z0-9_-]", "-", name)
    return re.sub(r"-+", "-", s).strip("-")


@dataclass
class _Journaling:
    progress: JournalProgress
    snapshot: Session


class SessionsService:
    def __init__(self, store, config, spend=None, *, run: tmux.Runner | None = None,
                 loop=None, clock: Callable[[], float] = time.time,
                 which: Callable[[str], str | None] | None = None,
                 find_transcript: Callable[[str], Path | None] | None = None,
                 hostname: str | None = None):
        self.store, self.config, self.spend = store, config, spend
        self.run = run or tmux._run
        self.loop = loop
        self.clock = clock
        self.which = which or shutil.which
        self.find_transcript = find_transcript or journal.find_transcript
        self.hostname = socket.gethostname() if hostname is None else hostname

        self.pushed_titles: dict[str, str] = {}   # tmux name -> terminal title we set
        self.waiting_since: dict[str, float] = {}
        self.cwds: dict[str, str | None] = {}
        self.costs: dict[str, float | None] = {}
        self.journaling: dict[str, _Journaling] = {}
        self.launches: list[Launch] = []
        self.live: list[tmux.TmuxSession] = []

        self._timers: list = []
        self._unsubscribe: Callable[[], None] | None = None
        self._cost_running = False
        self._cost_last = float("-inf")

    # ── lifecycle ──
    def start(self) -> None:
        self.check_environment()
        self.refresh()
        if self.loop is None:
            return
        self._timers = [
            self.loop.timeout(REFRESH_S, self._tick),
            self.loop.timeout(ENV_CHECK_S, self._env_tick),
            self.loop.timeout(COST_MIN_INTERVAL_S, self._cost_tick),
        ]
        self._unsubscribe = self.store.subscribe(self._on_store_change)
        self.request_costs()

    def stop(self) -> None:
        if self.loop is not None:
            for t in self._timers:
                self.loop.cancel(t)
        self._timers = []
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    def _tick(self) -> bool:
        try:
            self.refresh()
        except Exception:  # noqa: BLE001 — never let a refresh kill the timer
            log.exception("session refresh failed")
        return True

    def _env_tick(self) -> bool:
        self.check_environment()
        return True

    def _cost_tick(self) -> bool:
        self.request_costs()
        return True

    def _on_store_change(self, sections: set[str]) -> None:
        # The spend aggregator's tick: per-file costs may have moved.
        if "spend" in sections:
            self.request_costs()

    # ── environment (SP-2605) ──
    def terminal(self) -> str:
        return str(self.config.get("sessions.terminal") or "terminator")

    def check_environment(self) -> tuple[bool, bool]:
        tmux_ok = tmux.tmux_available(run=self.run)
        terminal_ok = self.which(self.terminal()) is not None
        self.store.update("sessions", tmux_ok=tmux_ok, terminal_ok=terminal_ok)
        return tmux_ok, terminal_ok

    # ── the refresh ──
    def refresh(self) -> bool:
        """Re-read tmux and write the `sessions` section. Returns True if it changed."""
        now = self.clock()
        live = tmux.list_sessions(run=self.run)
        self.live = live
        names = {s.name for s in live}

        items: list[Session] = []
        for s in live:
            items.append(self._session(s, now))

        # Journaling sessions stay listed after the kill, until their outcome.
        for name, j in self.journaling.items():
            if name not in names:
                items.append(dataclasses.replace(
                    j.snapshot, phase=PHASE_JOURNALING, prompt=None, doing=None,
                    waiting_since=None, journal=dataclasses.replace(j.progress)))

        items.extend(self._launch_rows(live, now))

        for gone in [n for n in self.waiting_since if n not in names]:
            del self.waiting_since[gone]
        for gone in [n for n in self.cwds if n not in names and n not in self.journaling]:
            del self.cwds[gone]
        self._sync_terminal_titles(live)

        items = sort_sessions(items)
        if items == self.store.state.sessions.items:
            return False
        self.store.update("sessions", items=items, updated_at=now)
        return True

    def _session(self, s: tmux.TmuxSession, now: float) -> Session:
        pane = tmux.capture_pane(s.name, run=self.run)
        activity = classify_pane(pane)
        if activity == WAITING:
            self.waiting_since.setdefault(s.name, now)
        else:
            self.waiting_since.pop(s.name, None)
        if s.name not in self.cwds:
            # Once per session: a Claude session never changes directory.
            self.cwds[s.name] = tmux.session_cwd(s.name, run=self.run)
        cwd = self.cwds[s.name]
        j = self.journaling.get(s.name)
        session = Session(
            name=s.name,
            project=Path(cwd).name if cwd else s.project,
            title=s.title,
            cwd=cwd,
            attached=s.attached,
            created=float(s.created),
            activity=activity,
            phase=PHASE_JOURNALING if j else PHASE_NONE,
            doing=describe_running(pane) if activity == RUNNING else None,
            prompt=parse_prompt(pane) if activity == WAITING else None,
            waiting_since=self.waiting_since.get(s.name),
            cost_usd=self.costs.get(s.name),
            journal=dataclasses.replace(j.progress) if j else None,
        )
        if j:
            j.snapshot = session
        return session

    def find(self, name: str) -> Session | None:
        return next((s for s in self.store.state.sessions.items if s.name == name), None)

    def tmux_session(self, name: str) -> tmux.TmuxSession | None:
        return next((s for s in self.live if s.name == name), None)

    # ── terminal titles (from cli.py) ──
    def _sync_terminal_titles(self, live: list[tmux.TmuxSession]) -> None:
        """Push `uiformat.session_heading` as each session's terminal title.

        Recorded even if the push failed, so a broken session logs once instead
        of every 2 s; ⟳ (retitle) forgets it and retries.
        """
        alive = {s.name for s in live}
        for name in [n for n in self.pushed_titles if n not in alive]:
            del self.pushed_titles[name]
        for s in live:
            heading = session_heading(s)
            if self.pushed_titles.get(s.name) != heading:
                tmux.set_terminal_title(s.name, heading, run=self.run)
                self.pushed_titles[s.name] = heading

    def heading(self, name: str) -> str:
        """The window title focus should match on: exactly what we pushed."""
        if name in self.pushed_titles:
            return self.pushed_titles[name]
        s = self.tmux_session(name)
        return session_heading(s) if s else name

    def retitle(self, name: str) -> None:
        self.pushed_titles.pop(name, None)
        self.refresh()

    # ── launching phase (SP-1203) ──
    def add_launch(self, directory: str) -> None:
        directory = str(Path(directory).expanduser())
        base = _sanitize(Path(directory).name)
        rx = re.compile(rf"^{re.escape(tmux.SESSION_PREFIX + base)}(-\d+)?$")
        self.launches = [l for l in self.launches if l.directory != directory]
        self.launches.append(Launch(directory, self.clock(),
                                    frozenset(s.name for s in self.live), rx))
        self.refresh()

    def _launch_rows(self, live: list[tmux.TmuxSession], now: float) -> list[Session]:
        rows: list[Session] = []
        keep: list[Launch] = []
        for launch in self.launches:
            fresh = [s for s in live if s.name not in launch.known
                     and (launch.name_rx.match(s.name)
                          or self.cwds.get(s.name) == launch.directory)]
            if fresh:
                launch.known = launch.known | {fresh[0].name}
                self.store.toast(f"Session started in {home_path(launch.directory)}")
                continue
            if now - launch.started > LAUNCH_TIMEOUT_S:
                self.store.toast(
                    f"The session in {home_path(launch.directory)} didn’t start within "
                    f"{int(LAUNCH_TIMEOUT_S)} s. Check the terminal window for errors.",
                    error=True)
                continue
            keep.append(launch)
            rows.append(Session(name=launch.placeholder, project=Path(launch.directory).name,
                                cwd=launch.directory, created=launch.started,
                                activity=UNKNOWN, phase=PHASE_LAUNCHING))
        self.launches = keep
        return rows

    # ── journaling phase (SP-1601) ──
    def begin_journal(self, name: str) -> None:
        snap = self.find(name) or Session(name=name)
        self.journaling[name] = _Journaling(JournalProgress(step=0), snap)
        self.refresh()

    def journal_progress(self, name: str, step: int, *, headless: bool = False,
                         failed: bool = False) -> None:
        j = self.journaling.get(name)
        if j is None:
            return
        p = j.progress
        j.progress = JournalProgress(step=max(p.step, step), failed=p.failed or failed,
                                     outcome=p.outcome, headless=p.headless or headless)
        self.refresh()

    def end_journal(self, name: str) -> None:
        self.journaling.pop(name, None)
        self.costs.pop(name, None)
        self.refresh()

    # ── per-session cost (SP-1401) ──
    def request_costs(self, force: bool = False) -> None:
        """Recompute costs on a worker thread, at most every 15 s."""
        if self.spend is None or self.loop is None or self._cost_running:
            return
        now = time.monotonic()
        if not force and now - self._cost_last < COST_MIN_INTERVAL_S:
            return
        self._cost_last = now
        self._cost_running = True
        sessions = [(s.name, self.cwds.get(s.name), float(s.created)) for s in self.live]

        def work() -> None:
            costs: dict[str, float | None] = {}
            try:
                costs = compute_costs(sessions, self.find_transcript, self.spend.session_cost)
            except Exception:  # noqa: BLE001
                log.exception("per-session cost failed")
            self.loop.idle(lambda: self._apply_costs(costs))

        self.loop.thread(work, "smartpanel-session-cost")

    def _apply_costs(self, costs: dict[str, float | None]) -> None:
        self._cost_running = False
        if costs != self.costs:
            self.costs = costs
            self.refresh()


def compute_costs(sessions: list[tuple[str, str | None, float]],
                  find_transcript: Callable[[str], Path | None],
                  session_cost: Callable[[Path], float | None]) -> dict[str, float | None]:
    """{tmux name: cost or None}. Pure apart from the two injected lookups.

    A transcript is found by cwd (newest `*.jsonl` in its project dir). When
    several sessions share a cwd that mapping is ambiguous, so only the newest
    session gets the cost and the others get None — never a wrong number.
    """
    by_cwd: dict[str, list[tuple[str, float]]] = {}
    out: dict[str, float | None] = {}
    for name, cwd, created in sessions:
        out[name] = None
        if cwd:
            by_cwd.setdefault(cwd, []).append((name, created))
    for cwd, group in by_cwd.items():
        newest = max(group, key=lambda g: (g[1], g[0]))[0]
        path = find_transcript(cwd)
        if path is None:
            continue
        try:
            cost = session_cost(path)
        except Exception:  # noqa: BLE001 — the spend service degrades to unknown
            cost = None
        out[newest] = cost
    return out
