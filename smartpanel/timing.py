"""Main-loop performance budget (SP-2702): log any callback slower than 50 ms.

The architecture rule is "< 50 ms per GLib callback"; anything slower (aggregator
scan, journal, prompt confirmation) belongs on a thread. This module makes a
breach visible instead of a vague stutter:

    from smartpanel import timing
    timing.install()            # time every GLib timeout / idle callback + watch loop lag

    @timing.timed("tray.refresh")   # or time one function explicitly, any time
    def refresh(...): ...

    with timing.measure("spend.apply"):
        ...

`install()` does two things:

1. It wraps `GLib.timeout_add`, `GLib.timeout_add_seconds` and `GLib.idle_add`,
   so every source added *after* it is timed with no change to the callers (they
   all look `GLib.<fn>` up at call time). Each slow call logs one WARNING on the
   `smartpanel.timing` logger. A closure that only forwards to another callable
   (`GLibScheduler.idle_add`'s `once()`) is named after the callable it forwards
   to, and `__smartpanel_name__` on any callback overrides the name:

       slow main-loop callback: services.spend.GLibScheduler.idle_add.<locals>.once -> services.spend.SpendService._apply took 83.4 ms (budget 50 ms)

2. It starts a **loop-lag watchdog**: a 25 ms heartbeat that measures how late
   each tick fires. The wrappers cannot see GTK signal handlers (a click running
   `tmux kill`), Gio D-Bus method handlers or `Gio.Application` command-line
   handling, but all of those block the same loop, so they show up as lag:

       main loop blocked ~120 ms (budget 50 ms; last timed callback: tray.Tray._paint)

How to run it: `python -m smartpanel.timing [smartpanel args]` (or `smartpanel
--debug-timing ...` through the installed launcher) installs the hook first and
then runs the normal CLI, and prints `report()` on exit. A `--debug-timing` flag
on `smartpanel` itself needs a one-line hook in `app.py` (`timing.install()` at
the top of `SmartPanelApp._wire`); until that lands, the module entry point is
the way in.

`stats()` keeps a per-callback count / total / worst so a profiling run can end
with `timing.report()`. When nothing is installed and nothing is decorated, this
module costs nothing; a decorated function pays two `perf_counter()` calls.
"""
from __future__ import annotations

import functools
import logging
import threading
import time
import types
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

log = logging.getLogger("smartpanel.timing")

BUDGET_MS = 50.0

# Injectable for tests.
clock: Callable[[], float] = time.perf_counter


@dataclass
class Stat:
    calls: int = 0
    slow: int = 0
    total_ms: float = 0.0
    worst_ms: float = 0.0


_lock = threading.Lock()
_stats: dict[str, Stat] = {}
_budget_ms = BUDGET_MS
_originals: dict[str, Callable] = {}
_last_name: str | None = None

LAG_NAME = "main-loop lag (watchdog)"
HEARTBEAT_MS = 25


def budget_ms() -> float:
    return _budget_ms


def set_budget(ms: float) -> None:
    global _budget_ms
    _budget_ms = float(ms)


def callback_name(fn: Any) -> str:
    """`module.Qualname` for a function, bound method or partial; repr() otherwise.

    `__smartpanel_name__` wins when set. A local closure that captures exactly one
    other function (a forwarding wrapper such as `once()`) is named
    `outer -> inner`, so a profiling run can tell the hand-offs apart.
    """
    explicit = getattr(fn, "__smartpanel_name__", None)
    if isinstance(explicit, str):
        return explicit
    target = fn
    while isinstance(target, functools.partial):
        target = target.func
    base = _plain_name(target)
    if base is None:
        return repr(fn)
    inner = _forwarded_to(target)
    return f"{base} -> {inner}" if inner else base


def _plain_name(target: Any) -> str | None:
    mod = getattr(target, "__module__", None) or ""
    qual = getattr(target, "__qualname__", None) or getattr(target, "__name__", None)
    if qual is None:
        return None
    if mod.startswith("smartpanel."):
        mod = mod[len("smartpanel."):]
    return f"{mod}.{qual}" if mod else str(qual)


def _forwarded_to(target: Any) -> str | None:
    """Name of the single callable a `<locals>` closure captured, if exactly one."""
    qual = getattr(target, "__qualname__", "") or ""
    cells = getattr(target, "__closure__", None)
    if "<locals>" not in qual or not cells:
        return None
    found = []
    for cell in cells:
        try:
            v = cell.cell_contents
        except ValueError:      # empty cell
            continue
        if isinstance(v, (types.FunctionType, types.MethodType, functools.partial)):
            found.append(v)
    if len(found) != 1:
        return None
    inner = found[0]
    while isinstance(inner, functools.partial):
        inner = inner.func
    return _plain_name(inner)


def record(name: str, elapsed_ms: float, budget: float | None = None) -> bool:
    """Account one call; log it when over budget. Returns True if it was slow."""
    global _last_name
    budget = _budget_ms if budget is None else budget
    slow = elapsed_ms > budget
    with _lock:
        _last_name = name
        st = _stats.setdefault(name, Stat())
        st.calls += 1
        st.total_ms += elapsed_ms
        st.worst_ms = max(st.worst_ms, elapsed_ms)
        if slow:
            st.slow += 1
    if slow:
        log.warning("slow main-loop callback: %s took %.1f ms (budget %.0f ms)",
                    name, elapsed_ms, budget)
    return slow


@contextmanager
def measure(name: str, budget: float | None = None) -> Iterator[None]:
    start = clock()
    try:
        yield
    finally:
        record(name, (clock() - start) * 1000.0, budget)


def timed(name: str | Callable | None = None, *, budget: float | None = None):
    """Decorator. `@timed`, `@timed("label")` or `@timed(budget=10)`."""
    def deco(fn: Callable) -> Callable:
        label = name if isinstance(name, str) else callback_name(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            start = clock()
            try:
                return fn(*args, **kwargs)
            finally:
                record(label, (clock() - start) * 1000.0, budget)
        wrapper.__smartpanel_timed__ = True  # type: ignore[attr-defined]
        return wrapper

    if callable(name) and not isinstance(name, str):
        fn, name = name, None
        return deco(fn)
    return deco


def wrap_callback(fn: Callable) -> Callable:
    """`fn` timed under its own name (idempotent)."""
    if getattr(fn, "__smartpanel_timed__", False) or not callable(fn):
        return fn
    return timed(callback_name(fn))(fn)


# ───────────────────────── GLib hook ─────────────────────────

_HOOKED = ("timeout_add", "timeout_add_seconds", "idle_add")


def install(budget: float | None = None, glib: Any = None, *, watchdog: bool = True) -> None:
    """Time every GLib timeout / idle callback added from now on, and watch the
    loop's lag for everything else (signal and D-Bus handlers). Idempotent."""
    if budget is not None:
        set_budget(budget)
    if glib is None:
        from gi.repository import GLib as glib  # noqa: N813
    with _lock:
        if _originals:
            return
        for attr in _HOOKED:
            orig = getattr(glib, attr)
            _originals[attr] = orig
            setattr(glib, attr, _hook(attr, orig))
        _originals["__module__"] = glib  # type: ignore[assignment]
    if watchdog:
        start_watchdog(glib)
    log.info("main-loop timing on: callbacks over %.0f ms are logged", _budget_ms)


def uninstall() -> None:
    stop_watchdog()
    with _lock:
        glib = _originals.pop("__module__", None)
        for attr in _HOOKED:
            orig = _originals.pop(attr, None)
            if glib is not None and orig is not None:
                setattr(glib, attr, orig)


def installed() -> bool:
    return bool(_originals)


def _hook(attr: str, orig: Callable) -> Callable:
    if attr == "idle_add":
        def idle_add(function, *args, **kwargs):
            return orig(wrap_callback(function), *args, **kwargs)
        return idle_add

    def timeout(interval, function, *args, **kwargs):
        return orig(interval, wrap_callback(function), *args, **kwargs)
    timeout.__name__ = attr
    return timeout


# ───────────────────────── loop-lag watchdog ─────────────────────────

class Watchdog:
    """Heartbeat every `interval_ms`; any tick later than the budget means
    something blocked the loop. Pure bookkeeping in `tick(now)` for tests."""

    def __init__(self, interval_ms: float = HEARTBEAT_MS):
        self.interval_ms = float(interval_ms)
        self.last: float | None = None
        self.ticks = 0
        self.source_id: int | None = None
        self._glib: Any = None

    def tick(self, now: float | None = None) -> float:
        """Account one heartbeat; returns the lag in ms (0 on the first tick)."""
        now = clock() if now is None else now
        lag = 0.0
        if self.last is not None:
            lag = max(0.0, (now - self.last) * 1000.0 - self.interval_ms)
            if lag > _budget_ms:
                if self.ticks == 0:
                    # Nothing could tick before the loop first ran: this is startup
                    # (imports, `_wire`), not a stall of the running app.
                    culprit = "startup, before the first heartbeat"
                else:
                    culprit = _last_name or "none (signal / D-Bus handler?)"
                with _lock:
                    st = _stats.setdefault(LAG_NAME, Stat())
                    st.calls += 1
                    st.slow += 1
                    st.total_ms += lag
                    st.worst_ms = max(st.worst_ms, lag)
                log.warning("main loop blocked ~%.0f ms (budget %.0f ms; last timed callback: %s)",
                            lag, _budget_ms, culprit)
            self.ticks += 1
        self.last = now
        return lag

    def _on_tick(self) -> bool:
        self.tick()
        return True             # GLib.SOURCE_CONTINUE

    def start(self, glib: Any) -> None:
        self._glib = glib
        self.last = clock()
        add = _originals.get("timeout_add", glib.timeout_add)   # never time ourselves
        self.source_id = add(int(self.interval_ms), self._on_tick)

    def stop(self) -> None:
        if self.source_id is not None and self._glib is not None:
            try:
                self._glib.source_remove(self.source_id)
            except Exception:  # noqa: BLE001 — already gone
                pass
        self.source_id = None


_watchdog: Watchdog | None = None


def start_watchdog(glib: Any = None, interval_ms: float = HEARTBEAT_MS) -> Watchdog:
    global _watchdog
    if _watchdog is not None:
        return _watchdog
    if glib is None:
        from gi.repository import GLib as glib  # noqa: N813
    _watchdog = Watchdog(interval_ms)
    _watchdog.start(glib)
    return _watchdog


def stop_watchdog() -> None:
    global _watchdog
    if _watchdog is not None:
        _watchdog.stop()
        _watchdog = None


# ───────────────────────── reporting ─────────────────────────

def stats() -> dict[str, Stat]:
    with _lock:
        return {k: Stat(**vars(v)) for k, v in _stats.items()}


def reset() -> None:
    global _last_name
    with _lock:
        _stats.clear()
        _last_name = None


def report(limit: int = 15) -> str:
    """Slowest callbacks first: name, calls, slow calls, mean and worst ms."""
    rows = sorted(stats().items(), key=lambda kv: kv[1].worst_ms, reverse=True)[:limit]
    if not rows:
        return "no callbacks timed"
    width = max(len(n) for n, _ in rows)
    lines = [f"{'callback':<{width}}  {'calls':>6}  {'slow':>5}  {'mean ms':>8}  {'worst ms':>8}"]
    for name, st in rows:
        mean = st.total_ms / st.calls if st.calls else 0.0
        lines.append(f"{name:<{width}}  {st.calls:>6}  {st.slow:>5}  {mean:>8.1f}  "
                     f"{st.worst_ms:>8.1f}")
    return "\n".join(lines)


# ───────────────────────── entry point ─────────────────────────

def run(argv: list[str] | None = None) -> int:
    """`python -m smartpanel.timing [args]`: the normal CLI with timing on.

    `--debug-timing` is accepted and dropped, so the launcher can forward it.
    The report goes to stderr when the app exits.
    """
    import sys
    args = [a for a in (sys.argv[1:] if argv is None else argv) if a != "--debug-timing"]
    install()
    from smartpanel.cli import main
    try:
        return main(args)
    finally:
        print(report(), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(run())
