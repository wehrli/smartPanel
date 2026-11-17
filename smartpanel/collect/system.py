"""System metrics collector: GLib timeouts -> Store `system` section.

    SystemCollector(store, config, theme=None).start() / .stop()

One `GLib.timeout_add_seconds` per category (`DEFAULT_INTERVALS`, or the theme's
`INTERVAL` for a category it lists, the shortest one if it lists several). Each
tick reads a sensor from `sensors/system.py` / `sensors/gpu.py` (they never
raise; unknown values are None) and writes one sub-state of `system`. Every
category is primed synchronously in `start()` so surfaces open on real numbers.

Each tick is a psutil call plus a few small sysfs reads: well under the 50 ms
main-loop budget (measured ~1-5 ms). A tick slower than that is logged once per
category at WARNING so a regression is visible. The exception is DISK: `statvfs`
on `disk.mount` can block indefinitely when that is a hung NFS/SSHFS mount, so
once started, DISK reads run on a daemon thread and come back via
`GLib.idle_add` (only the main thread touches the Store). While a read is still
in flight the next one is skipped, so a hung mount costs one stuck thread, never
the panel.

**History.** Each series keeps a `history.RingBuffer` sized from its poll
interval to hold 15 minutes (`HISTORY_WINDOW_S`), never fewer than 300 points,
and the state exports exactly the last 15 minutes of it, rounded to 0.1: CPU/NET
at 3 s → 300 points, MEMORY/GPU at 5 s → 180, SWAP at 10 s → 90, and a theme
that polls NET at 2 s gets 450. That is what "15 min history" in the UI means;
at the defaults the D-Bus JSON of the whole `system` section is around 8 KB
(polling is floored at 1 s, so the worst case is 900 points per series). Values that are None (no swap, no GPU, no rate yet)
are not pushed, so an empty list is the honest "no data" state.

**Swap alert.** The collector owns the one `alerts.AlertTracker`, built from the
`swap.*` settings (the config layer already applies the env-var overrides).
`apply_swap()` is the whole per-reading step: feed the tracker, publish
`SwapState` (level, thresholds, alerted), then send the notification the tracker
asked for. Policy is entirely the tracker's: notify going up, stay quiet on
CRITICAL→WARNING, one recovery if we alerted, critical repeats every `repeat_s`.
A `swap.*` change reconfigures the tracker live (`AlertTracker.reconfigure`):
the level is kept, nothing re-fires, and a drop it causes is silent — except
that a drop to OK withdraws the bubble still on screen (`notify.close("swap")`),
since GNOME never expires a critical one.

The clock and the notifier are injectable so the swap journey can be tested
without GLib, a display, or sleeping (see tests/test_collect_system.py).
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from typing import Any, Callable

from smartpanel import alerts, notify
from smartpanel.history import DEFAULT_SIZE, RingBuffer
from smartpanel.sensors import gpu as gpu_sensor
from smartpanel.sensors import system as sys_sensor
from smartpanel.state.model import (
    CpuState,
    DiskState,
    Event,
    GpuState,
    MemoryState,
    NetState,
    SwapState,
)

log = logging.getLogger(__name__)

DEFAULT_INTERVALS: dict[str, float] = {
    "CPU": 3, "MEMORY": 5, "SWAP": 10, "DISK": 60, "NET": 3, "GPU": 5,
}
CATEGORIES = tuple(DEFAULT_INTERVALS)
HISTORY_WINDOW_S = 15 * 60
TICK_BUDGET_S = 0.050

Reader = Callable[[], dict[str, Any]]


def intervals_for(theme=None) -> dict[str, float]:
    """Poll interval per category: defaults, overridden by the theme's INTERVAL."""
    out = dict(DEFAULT_INTERVALS)
    if theme is None:
        return out
    themed: dict[str, float] = {}
    for m in getattr(theme, "metrics", []) or []:
        cat = getattr(m, "category", None)
        if cat in out:
            try:
                iv = float(m.interval)
            except (TypeError, ValueError):
                continue
            if iv > 0:
                themed[cat] = min(iv, themed.get(cat, iv))
    out.update(themed)
    return out


def export_points(interval: float) -> int:
    """How many samples cover HISTORY_WINDOW_S at this interval (polls floor at 1 s)."""
    return max(1, math.ceil(HISTORY_WINDOW_S / max(interval, 1)))


def ring_size(interval: float) -> int:
    """Ring capacity for a series polled every `interval` s: ≥ 15 min, ≥ 300."""
    return max(DEFAULT_SIZE, export_points(interval))


_SERIES_CATEGORY = {"cpu": "CPU", "memory": "MEMORY", "swap": "SWAP",
                    "net_down": "NET", "net_up": "NET", "gpu": "GPU"}
# Categories whose sensor may block (statvfs on a network mount): read off-loop.
OFF_LOOP = frozenset({"DISK"})


def _r(v: Any, nd: int = 1) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


def _gb(v: float | None) -> str:
    """'5.3', '8' — the prototype's "5.3 of 8 GB" style."""
    if v is None:
        return "?"
    return f"{v:.1f}".removesuffix(".0")


def swap_notification(level: alerts.Level, percent: float | None,
                      used_gb: float | None, total_gb: float | None) -> tuple[str, str, str]:
    """(kind, title, body) for a tracker event. Pure; copy from the prototype."""
    size = ""
    if used_gb is not None and total_gb:
        size = f" ({_gb(used_gb)} of {_gb(total_gb)} GB)"
    pct = "?" if percent is None else f"{percent:.0f}"
    if level is alerts.Level.CRITICAL:
        return ("swap_critical", "Swap critically full",
                f"{pct}% used{size}. Close something heavy before the system stalls.")
    if level is alerts.Level.WARNING:
        return ("swap_warning", "Swap is filling up",
                f"{pct}% used{size}. Close something heavy before the system stalls.")
    head = "No swap configured" if percent is None else f"{pct}% used"
    return ("swap_ok", "Swap back to normal", f"{head}. The alert is cleared.")


class SystemCollector:
    def __init__(self, store, config, theme=None, *,
                 readers: dict[str, Reader] | None = None,
                 notifier: Callable[..., Any] | None = None,
                 closer: Callable[[str], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.store, self.config, self.theme = store, config, theme
        self.intervals = intervals_for(theme)
        self._default_disk: Reader = lambda: sys_sensor.disk(self._mount())
        self.readers: dict[str, Reader] = {
            "CPU": sys_sensor.cpu,
            "MEMORY": sys_sensor.memory,
            "SWAP": sys_sensor.swap,
            "DISK": self._default_disk,
            "NET": sys_sensor.network,
            "GPU": gpu_sensor.gpu,
        }
        self.readers.update(readers or {})
        self._notify = notifier or notify.send
        self._close = closer or notify.close
        self._clock = clock
        self._hist = {name: RingBuffer(ring_size(self.intervals[cat]))
                      for name, cat in _SERIES_CATEGORY.items()}
        self._in_flight: set[str] = set()
        self._stuck_logged: set[str] = set()
        self.tracker = alerts.AlertTracker(self._thresholds())
        self._swap_reading: tuple[float | None, float | None, float | None] = (None, None, None)
        self._timers: list[int] = []
        self._unsubscribe: Callable[[], None] | None = None
        self._slow_logged: set[str] = set()
        self._publish_swap()

    # ── lifecycle ──
    def start(self) -> None:
        if self._timers:
            return
        from gi.repository import GLib

        try:
            self._unsubscribe = self.config.subscribe(self._on_config)
        except Exception:  # noqa: BLE001 — a config without observers still polls
            log.exception("config.subscribe failed")
        for cat in CATEGORIES:
            # prime: surfaces open on real values (DISK arrives a moment later)
            self._run(cat)
            iv = int(max(1, round(self.intervals[cat])))
            self._timers.append(GLib.timeout_add_seconds(iv, self._on_timer, cat))
            log.debug("system: %s every %ds", cat, iv)

    def stop(self) -> None:
        if self._timers:
            from gi.repository import GLib
            for tid in self._timers:
                GLib.source_remove(tid)
            self._timers.clear()
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def _on_timer(self, cat: str) -> bool:
        self._run(cat)
        return True  # GLib.SOURCE_CONTINUE

    def _run(self, cat: str) -> None:
        """Tick from the loop: inline, or on a thread for OFF_LOOP categories."""
        if cat in OFF_LOOP:
            self.tick_async(cat)
        else:
            self.tick(cat)

    # ── off-loop tick (DISK) ──
    def _reader_snapshot(self, cat: str) -> Reader:
        """The reader with any settings resolved now, on the main thread."""
        reader = self.readers[cat]
        if cat == "DISK" and reader is self._default_disk:
            mount = self._mount()
            return lambda: sys_sensor.disk(mount)
        return reader

    def tick_async(self, cat: str, *, spawn: Callable[[Callable[[], None]], Any] | None = None,
                   post: Callable[..., Any] | None = None) -> bool:
        """Read `cat` on a daemon thread, apply it on the main loop.

        Returns False (and reads nothing) while the previous read is still
        running: a hung network mount must not pile up threads. `spawn`/`post`
        are injectable for tests; they default to a daemon thread and
        `GLib.idle_add`.
        """
        if cat in self._in_flight:
            if cat not in self._stuck_logged:
                self._stuck_logged.add(cat)
                log.warning("system: %s read still pending; skipping until it returns", cat)
            return False
        if post is None:
            from gi.repository import GLib
            post = GLib.idle_add
        reader = self._reader_snapshot(cat)
        self._in_flight.add(cat)

        def work() -> None:
            try:
                values = reader() or {}
            except Exception:  # noqa: BLE001
                log.exception("sensor %s failed", cat)
                values = {}
            post(self._finish_async, cat, values)

        if spawn is None:
            threading.Thread(target=work, name=f"smartpanel-{cat.lower()}",
                             daemon=True).start()
        else:
            spawn(work)
        return True

    def _finish_async(self, cat: str, values: dict[str, Any]) -> bool:
        self._in_flight.discard(cat)
        self._stuck_logged.discard(cat)
        try:
            self.apply(cat, values)
            if cat == "DISK" and values.get("MOUNT") not in (None, self._mount()):
                self.tick_async(cat)   # the mount changed while we were reading
        except Exception:  # noqa: BLE001
            log.exception("applying %s failed", cat)
        return False  # GLib.SOURCE_REMOVE: one-shot idle

    # ── one tick ──
    def tick(self, cat: str) -> bool:
        t0 = time.perf_counter()
        try:
            values = self.readers[cat]() or {}
        except Exception:  # noqa: BLE001 — sensors shouldn't raise; belt and braces
            log.exception("sensor %s failed", cat)
            values = {}
        try:
            self.apply(cat, values)
        except Exception:  # noqa: BLE001 — never break the main loop
            log.exception("applying %s failed", cat)
        elapsed = time.perf_counter() - t0
        if elapsed > TICK_BUDGET_S and cat not in self._slow_logged:
            self._slow_logged.add(cat)
            log.warning("system: %s tick took %.0f ms (budget %d ms)",
                        cat, elapsed * 1000, TICK_BUDGET_S * 1000)
        return True

    def apply(self, cat: str, values: dict[str, Any]) -> None:
        match cat:
            case "CPU":
                self.apply_cpu(values)
            case "MEMORY":
                self.apply_memory(values)
            case "SWAP":
                self.apply_swap(values)
            case "DISK":
                self.apply_disk(values)
            case "NET":
                self.apply_net(values)
            case "GPU":
                self.apply_gpu(values)

    def _history(self, name: str, cat: str, nd: int = 1) -> list[float]:
        return [round(v, nd) for v in self._hist[name].last(export_points(self.intervals[cat]))]

    def _write(self, **sub: Any) -> None:
        self.store.update("system", updated_at=time.time(), **sub)

    # ── per-category steps (pure-ish: values in, state out) ──
    def apply_cpu(self, v: dict[str, Any]) -> None:
        pct = _r(v.get("PERCENTAGE"))
        self._hist["cpu"].push(pct)
        prev = self.store.state.system.cpu
        self._write(cpu=CpuState(
            percent=pct,
            temp_c=_r(v.get("TEMPERATURE")),
            cores=int(v.get("CORES") or prev.cores or 0),
            history=self._history("cpu", "CPU"),
        ))

    def apply_memory(self, v: dict[str, Any]) -> None:
        pct = _r(v.get("PERCENTAGE"))
        self._hist["memory"].push(pct)
        self._write(memory=MemoryState(
            percent=pct,
            used_gb=_r(v.get("USED"), 2),
            total_gb=_r(v.get("TOTAL"), 2),
            history=self._history("memory", "MEMORY"),
        ))

    def apply_swap(self, v: dict[str, Any], now: float | None = None) -> None:
        pct = _r(v.get("PERCENTAGE"))
        has_swap = pct is not None
        used = _r(v.get("USED"), 2) if has_swap else None
        total = _r(v.get("TOTAL"), 2) if has_swap else None
        self._hist["swap"].push(pct)          # None (no swap) pushes nothing
        self._swap_reading = (pct, used, total)
        event = self.tracker.update(pct, used, total,
                                    now=self._clock() if now is None else now)
        self._publish_swap()
        if event is not None:
            self._send_swap(event.level, pct, used, total)

    def _publish_swap(self) -> None:
        pct, used, total = self._swap_reading
        t = self.tracker.thresholds
        self._write(swap=SwapState(
            percent=pct, used_gb=used, total_gb=total,
            level=int(self.tracker.level),
            warn_at=float(t.warning), crit_at=float(t.critical),
            history=self._history("swap", "SWAP"),
            alerted=self.tracker.alerted,
        ))

    def _send_swap(self, level: alerts.Level, pct, used, total) -> None:
        kind, title, body = swap_notification(level, pct, used, total)
        action = None
        if level is not alerts.Level.OK:
            action = notify.Action("Open System", self._open_system)
        log.info("swap alert: %s — %s", title, body)
        try:
            self._notify(kind, title, body, action=action)
        except Exception:  # noqa: BLE001 — notify.send can't raise; a fake might
            log.exception("swap notification failed")

    def _open_system(self) -> None:
        self.store.emit(Event("open_dashboard", {"page": "system"}))

    def apply_disk(self, v: dict[str, Any]) -> None:
        self._write(disk=DiskState(
            mount=str(v.get("MOUNT") or self._mount()),
            percent=_r(v.get("USED_PERCENT")),
            used_gb=_r(v.get("USED"), 2),
            total_gb=_r(v.get("TOTAL"), 2),
        ))

    def apply_net(self, v: dict[str, Any]) -> None:
        down, up = _r(v.get("DOWNLOAD_BPS"), 0), _r(v.get("UPLOAD_BPS"), 0)
        self._hist["net_down"].push(down)
        self._hist["net_up"].push(up)
        self._write(net=NetState(
            down_bps=down, up_bps=up,
            iface=v.get("IFACE") or None,
            history_down=self._history("net_down", "NET", 0),
            history_up=self._history("net_up", "NET", 0),
        ))

    def apply_gpu(self, v: dict[str, Any]) -> None:
        available = bool(v.get("AVAILABLE"))
        pct = _r(v.get("PERCENTAGE")) if available else None
        self._hist["gpu"].push(pct)
        self._write(gpu=GpuState(
            available=available,
            vendor=v.get("VENDOR") if available else None,
            percent=pct,
            vram_used_gb=_r(v.get("MEMORY_USED"), 2),
            vram_total_gb=_r(v.get("MEMORY_TOTAL"), 2),
            temp_c=_r(v.get("TEMPERATURE")),
            history=self._history("gpu", "GPU"),
        ))

    # ── settings ──
    def _mount(self) -> str:
        try:
            m = self.config.get("disk.mount")
        except Exception:  # noqa: BLE001
            m = None
        return os.path.expanduser(str(m)) if m else "/"

    def _thresholds(self) -> alerts.Thresholds:
        def get(key: str):
            try:
                return self.config.get(key)
            except Exception:  # noqa: BLE001
                return None
        return alerts.make_thresholds(get("swap.warn"), get("swap.crit"),
                                      get("swap.hysteresis"), get("swap.repeat_s"))

    def reconfigure_swap(self) -> None:
        """Apply the current `swap.*` settings live (SP-0103). Never notifies.

        A drop all the way to OK closes the bubble still on screen instead: a
        critical one never expires by itself, and no recovery bubble will come
        to replace it.
        """
        before = self.tracker.level
        after = self.tracker.reconfigure(self._thresholds())
        self._publish_swap()
        if before is not alerts.Level.OK and after is alerts.Level.OK:
            try:
                self._close("swap")
            except Exception:  # noqa: BLE001
                log.exception("closing the swap notification failed")

    def _on_config(self, key: str, _value: Any = None) -> None:
        try:
            if key is None or key.startswith("swap."):
                self.reconfigure_swap()
            if key is None or key == "disk.mount":
                if self._timers:
                    self.tick_async("DISK")
                else:
                    self.tick("DISK")
        except Exception:  # noqa: BLE001
            log.exception("system: config change %r failed", key)
