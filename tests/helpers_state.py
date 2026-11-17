"""State test harness (SP-0013): scenario -> Store, fixtures, and virtual time.

Usable by any test module:

    from tests.helpers_state import store_for, demo_controller, FakeScheduler

    store = store_for("busy")                    # Store filled with the prototype's data
    store, ctl, sched, events = demo_controller("busy")
    ctl.close_session("claude-ledgerly", journal=True)
    sched.advance(10_000)                        # run the timers due in the next 10 s
    assert events.toasts()[-1]["text"].startswith("Journaled")

Everything is GTK-free and deterministic: scenarios are built at `DEMO_NOW`
(Tue 17 Nov 2026 14:32, local time) and the fake scheduler's clock starts there.
"""
from __future__ import annotations

import dataclasses
import heapq
import itertools
import types
import typing
from pathlib import Path
from typing import Any, Callable

import yaml

from smartpanel.config import Config
from smartpanel.demo.scenarios import DEMO_NOW, build_state
from smartpanel.state import Event, PanelState, Store, to_dict

FIXTURES = Path(__file__).parent / "fixtures" / "demo"


# ───────────────────────── stores ─────────────────────────

def state_for(name: str, now: float = DEMO_NOW, **kw) -> PanelState:
    return build_state(name, now, **kw)


def store_for(name: str, now: float = DEMO_NOW, **kw) -> Store:
    return Store(state_for(name, now, **kw))


class Recorder:
    """Collects a store's change notifications and events."""

    def __init__(self, store: Store):
        self.changes: list[set[str]] = []
        self.events: list[Event] = []
        store.subscribe(self.changes.append)
        store.on_event(self.events.append)

    def of(self, kind: str) -> list[dict]:
        return [e.data for e in self.events if e.kind == kind]

    def toasts(self) -> list[dict]:
        return self.of("toast")

    def last_toast(self) -> dict | None:
        t = self.toasts()
        return t[-1] if t else None

    def clear(self) -> None:
        self.changes.clear()
        self.events.clear()


# ───────────────────────── virtual time ─────────────────────────

class FakeScheduler:
    """Drop-in for `demo.controller.GLibScheduler`: timers fire only on `advance()`.

    `time()` is a clock that moves with it (seconds, starting at `start`), so pass
    `clock=sched.time` to code that reads the wall clock.
    """

    def __init__(self, start: float = DEMO_NOW):
        self.now_ms = 0
        self.start = start
        self._q: list[tuple[int, int, int]] = []
        self._fns: dict[int, Callable[[], None]] = {}
        self._ids = itertools.count(1)

    def add(self, delay_ms: int, fn: Callable[[], None]) -> int:
        h = next(self._ids)
        self._fns[h] = fn
        heapq.heappush(self._q, (self.now_ms + int(delay_ms), h, h))
        return h

    def cancel(self, handle: int) -> None:
        self._fns.pop(handle, None)

    def time(self) -> float:
        return self.start + self.now_ms / 1000

    @property
    def pending(self) -> int:
        return len(self._fns)

    def advance(self, ms: int) -> None:
        """Run every timer due within `ms`, in order, including ones they schedule."""
        until = self.now_ms + int(ms)
        while self._q and self._q[0][0] <= until:
            due, _, h = heapq.heappop(self._q)
            fn = self._fns.pop(h, None)
            self.now_ms = max(self.now_ms, due)
            if fn is not None:
                fn()
        self.now_ms = until

    def run_all(self, limit_ms: int = 600_000) -> None:
        """Advance until nothing is pending (bounded, for repeating timers)."""
        while self._fns and self.now_ms < limit_ms:
            nxt = min(d for d, _, h in self._q if h in self._fns)
            self.advance(nxt - self.now_ms)


def demo_controller(name: str = "busy", *, config: Config | None = None, env: dict | None = None,
                    live: bool = False, **kw):
    """(store, controller, scheduler, recorder) for scenario `name`, all in virtual time.
    Notifications are recorded in `controller.notifications`, never shown."""
    from smartpanel.demo.controller import DemoController

    store = store_for(name)
    sched = FakeScheduler()
    rec = Recorder(store)
    notes: list[tuple[str, str, str]] = []
    ctl = DemoController(store, config if config is not None else Config(), scheduler=sched,
                         clock=sched.time, env=env or {},
                         notifier=lambda *a: notes.append(a), live=live, **kw)
    ctl.notifications = notes
    rec.clear()
    return store, ctl, sched, rec


# ───────────────────────── fixtures (tests/fixtures/demo/*.yaml) ─────────────────────────

def _hints(cls) -> dict[str, Any]:
    import smartpanel.state.model as m
    return typing.get_type_hints(cls, vars(m))


def from_dict(tp: Any, data: Any) -> Any:
    """Rebuild a model dataclass tree from `to_dict` output (the inverse of `to_dict`)."""
    if data is None:
        return None
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        return from_dict(args[0], data) if len(args) == 1 else data
    if origin is list:
        (arg,) = typing.get_args(tp) or (Any,)
        return [from_dict(arg, v) for v in data]
    if origin is dict or tp is dict:
        return dict(data)
    if dataclasses.is_dataclass(tp):
        hints = _hints(tp)
        return tp(**{f.name: from_dict(hints[f.name], data[f.name])
                     for f in dataclasses.fields(tp) if f.name in data})
    if tp is float and isinstance(data, int):
        return float(data)
    return data


def state_from_dict(data: dict) -> PanelState:
    return from_dict(PanelState, data)


def fixture_path(name: str) -> Path:
    return FIXTURES / f"{name}.yaml"


def load_fixture(name: str) -> PanelState:
    """PanelState from `tests/fixtures/demo/<name>.yaml` (a snapshot of build_state at
    DEMO_NOW, in UTC — see test_demo_scenarios.test_fixtures_match_build_state)."""
    return state_from_dict(yaml.safe_load(fixture_path(name).read_text(encoding="utf-8")))


def dump_fixture(state: PanelState) -> str:
    return yaml.safe_dump(to_dict(state), sort_keys=False, allow_unicode=True, width=100)
