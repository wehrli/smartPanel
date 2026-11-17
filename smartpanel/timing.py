"""Main-loop performance budget (SP-2702): log any callback slower than 50 ms.

The architecture rule is "< 50 ms per GLib callback"; anything slower (aggregator
scan, journal, prompt confirmation) belongs on a thread. This module makes a
breach visible instead of a vague stutter:

    from smartpanel import timing
    timing.install()            # `--debug-timing`: time every GLib timeout / idle callback

    @timing.timed("tray.refresh")   # or time one function explicitly, any time
    def refresh(...): ...

    with timing.measure("spend.apply"):
        ...

`install()` wraps `GLib.timeout_add`, `GLib.timeout_add_seconds` and
`GLib.idle_add`, so every source added *after* it is timed with no change to the
callers (they all look `GLib.<fn>` up at call time). Each slow call logs one
WARNING on the `smartpanel.timing` logger:

    slow main-loop callback: services.spend.SpendService._apply took 83.4 ms (budget 50 ms)

`stats()` keeps a per-callback count / total / worst so a profiling run can end
with `timing.report()`. When nothing is installed and nothing is decorated, this
module costs nothing; a decorated function pays two `perf_counter()` calls.
"""
from __future__ import annotations

import functools
import logging
import threading
import time
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


def budget_ms() -> float:
    return _budget_ms


def set_budget(ms: float) -> None:
    global _budget_ms
    _budget_ms = float(ms)


def callback_name(fn: Any) -> str:
    """`module.Qualname` for a function, bound method or partial; repr() otherwise."""
    target = fn
    while isinstance(target, functools.partial):
        target = target.func
    mod = getattr(target, "__module__", None) or ""
    qual = getattr(target, "__qualname__", None) or getattr(target, "__name__", None)
    if qual is None:
        return repr(fn)
    if mod.startswith("smartpanel."):
        mod = mod[len("smartpanel."):]
    return f"{mod}.{qual}" if mod else str(qual)


def record(name: str, elapsed_ms: float, budget: float | None = None) -> bool:
    """Account one call; log it when over budget. Returns True if it was slow."""
    budget = _budget_ms if budget is None else budget
    slow = elapsed_ms > budget
    with _lock:
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


# ───────────────────────── GLib hook (`--debug-timing`) ─────────────────────────

_HOOKED = ("timeout_add", "timeout_add_seconds", "idle_add")


def install(budget: float | None = None, glib: Any = None) -> None:
    """Time every GLib timeout / idle callback added from now on. Idempotent."""
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
    log.info("main-loop timing on: callbacks over %.0f ms are logged", _budget_ms)


def uninstall() -> None:
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


# ───────────────────────── reporting ─────────────────────────

def stats() -> dict[str, Stat]:
    with _lock:
        return {k: Stat(**vars(v)) for k, v in _stats.items()}


def reset() -> None:
    with _lock:
        _stats.clear()


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
