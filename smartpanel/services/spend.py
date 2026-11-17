"""Claude spend service: aggregator on a worker thread -> Store `spend` + app.loading/first_run.

API (fixed by the v2 contract):
    SpendService(store, config, claude_root=None).start() / .stop() / .refresh_now()
    SpendService.session_cost(transcript_path) -> float | None   # cached per-file cost (E14)

How it runs (SP-0801):
- Every `INTERVAL_S` (15 s) a main-loop tick starts `Aggregator.refresh()` on a worker
  thread. Only one refresh is ever in flight; a tick that finds one running is skipped.
  `refresh_now()` while busy queues exactly one follow-up run instead.
- The worker builds the whole `SpendState` (pure, `claude_stats.spend_state`) and hands
  it back with `idle_add`; only the main thread touches the Store.
- The first scan reports `app.loading = Loading(done, total)` (files), throttled to
  4 updates a second (each one repaints the tray and the dashboard), and
  clears it when the result lands (SP-2601). `app.first_run` is true while
  `~/.claude/projects` is missing or holds no `*.jsonl` (SP-2603).
- Budget: the forecast is recomputed on every result and whenever `budget.daily_usd`
  changes; 80 % / 100 % notifications fire once each per local day (SP-0902) when a
  budget is set and `notifications.budget` is on. A threshold crossed while
  notifications are off is not recorded, so it fires once they are turned back on.
  The bubble's "View" button opens the dashboard's overview (Event open_dashboard).
- After `stop()`, a result still in flight is discarded: nothing reaches the Store.

Everything GLib-specific sits behind a small scheduler object, so tests drive the
thread hand-off deterministically without a main loop.
"""
from __future__ import annotations

import inspect
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from smartpanel.claude_stats.aggregator import Aggregator, Snapshot
from smartpanel.claude_stats.budget import BudgetAlerts, budget_message
from smartpanel.claude_stats.forecast import forecast
from smartpanel.claude_stats.spend_state import build_spend_state, days
from smartpanel.state.model import Event, Loading

log = logging.getLogger(__name__)

INTERVAL_S = 15
# Loading-progress hand-offs: at most 4 a second. Each one changes `app`, which
# repaints every surface showing the progress (tray label + menu, dashboard), and
# at 10/s those idle callbacks measured 61-87 ms on a cold scan.
PROGRESS_MIN_INTERVAL_S = 0.25

Notifier = Callable[[str, str, str, Any], None]   # (kind, title, body, action)


# ───────────────────────── scheduling ─────────────────────────

class GLibScheduler:
    """Real scheduling: GLib timers on the main loop, plain daemon threads for work."""

    def __init__(self) -> None:
        from gi.repository import GLib
        self._GLib = GLib

    def timeout_add_seconds(self, seconds: int, fn: Callable[[], bool]) -> int:
        return self._GLib.timeout_add_seconds(seconds, fn)

    def source_remove(self, source_id: int) -> None:
        try:
            self._GLib.source_remove(source_id)
        except Exception:  # noqa: BLE001 — already gone
            pass

    def idle_add(self, fn: Callable[[], Any]) -> None:
        def once():
            try:
                fn()
            except Exception:  # noqa: BLE001 — services never raise into the loop
                log.exception("spend hand-off failed")
            return False        # GLib.SOURCE_REMOVE
        self._GLib.idle_add(once)

    def run_in_thread(self, fn: Callable[[], None], name: str = "smartpanel-spend") -> None:
        threading.Thread(target=fn, name=name, daemon=True).start()


# ───────────────────────── notifications ─────────────────────────

@dataclass(frozen=True)
class BudgetAction:
    """The bubble's button: `label`, the dashboard `page` it opens, and the
    main-loop `callback` that opens it. Injected notifiers receive this as is;
    `default_notifier` converts it to whatever `smartpanel.notify` expects."""

    label: str
    page: str
    callback: Callable[[], None]


def _to_notify_action(notify, action: Any):
    """Translate a BudgetAction into `notify.Action(label, callback)`.

    v2's libnotify backend reads `action.label` / `action.callback`, so a dict or
    any other shape would make it raise and drop the bubble. Without a usable
    callback or an `Action` type, send no button rather than a broken one.
    """
    if action is None:
        return None
    action_cls = getattr(notify, "Action", None)
    label = getattr(action, "label", None)
    callback = getattr(action, "callback", None)
    if action_cls is None or not label or not callable(callback):
        return None
    try:
        return action_cls(label, callback)
    except Exception:  # noqa: BLE001
        return None


def default_notifier(kind: str, title: str, body: str, action: Any = None) -> None:
    """Send through `smartpanel.notify`, whichever signature it has.

    v2 adds `notify.send(kind, title, body, *, action: Action | None)`; the v1
    function is `send(summary, body, urgency, key)`. The two clash positionally, so
    look at the parameter names rather than guessing.
    """
    try:
        from smartpanel import notify
        send = getattr(notify, "send", None)
        if send is None:
            return
        try:
            params = inspect.signature(send).parameters
        except (TypeError, ValueError):
            params = {}
        if "kind" in params:
            send(kind, title, body, action=_to_notify_action(notify, action))
        else:
            send(title, body, urgency="normal", key=kind)
    except Exception:  # noqa: BLE001 — a notification must never break the loop
        log.warning("budget notification failed: %s — %s", title, body, exc_info=True)


def _with_resolved_keys(per_file: dict[str, float], root: Path) -> dict[str, float]:
    """Also key each cost by its symlink-free path, if the root isn't already one,
    so a caller holding either spelling of a transcript path finds it."""
    costs = dict(per_file)
    try:
        real = Path(root).resolve()
    except (OSError, RuntimeError):
        return costs
    raw = Path(root)
    if real == raw:
        return costs
    prefix, real_prefix = str(raw), str(real)
    for path, cost in per_file.items():
        if path.startswith(prefix):
            costs.setdefault(real_prefix + path[len(prefix):], cost)
    return costs


# ───────────────────────── service ─────────────────────────

class SpendService:
    def __init__(self, store, config, claude_root=None, *,
                 scheduler=None, aggregator: Aggregator | None = None,
                 notifier: Notifier | None = None,
                 interval_s: int = INTERVAL_S,
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic):
        self.store, self.config = store, config
        self.claude_root = Path(claude_root) if claude_root else None
        self._agg = aggregator or Aggregator(claude_root=self.claude_root)
        self._sched = scheduler
        self._notify = notifier or default_notifier
        self._interval = interval_s
        self._clock = clock
        self._monotonic = monotonic

        self._timer: int | None = None
        self._unsub_config: Callable[[], None] | None = None
        self._running = False
        self._busy = False          # main-thread flag: a refresh is in flight
        self._again = False         # refresh_now() arrived while busy
        self._scanned = False       # first result has landed
        self._last_progress: float | None = None   # worker-thread throttle
        self._snapshot: Snapshot | None = None
        self._file_costs: dict[str, float] = {}
        self._alerts = BudgetAlerts()

    # ── lifecycle ──
    def _scheduler(self):
        if self._sched is None:
            self._sched = GLibScheduler()
        return self._sched

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        sched = self._scheduler()
        self._timer = sched.timeout_add_seconds(self._interval, self._on_timer)
        subscribe = getattr(self.config, "subscribe", None)
        if subscribe is not None:
            self._unsub_config = subscribe(self._on_config)
        self._tick()

    def stop(self) -> None:
        self._running = False
        if self._timer is not None and self._sched is not None:
            self._sched.source_remove(self._timer)
        self._timer = None
        if self._unsub_config is not None:
            self._unsub_config()
            self._unsub_config = None

    def refresh_now(self) -> None:
        if self._busy:
            self._again = True
            return
        self._tick()

    def session_cost(self, transcript_path) -> float | None:
        """USD charged to one transcript at the last scan, or None if unknown.

        Read from the aggregator's per-file figures (after the cross-file
        message-id dedupe), never by re-parsing. Copies a resumed session
        inherited from its original are charged to the original.
        """
        if transcript_path is None:
            return None
        key = str(transcript_path)
        cost = self._file_costs.get(key)
        if cost is None:
            try:
                cost = self._file_costs.get(str(Path(transcript_path).expanduser().resolve()))
            except (OSError, RuntimeError):
                return None
        return cost

    @property
    def snapshot(self) -> Snapshot | None:
        """The last aggregator result (main thread)."""
        return self._snapshot

    def days(self, count: int, end=None):
        """On-demand day list beyond the 14 in the state (up to DAYS_KEPT have data)."""
        if self._snapshot is None:
            return []
        return days(self._snapshot, count=count, end=end)

    # ── main thread ──
    def _on_timer(self) -> bool:
        if not self._running:
            return False
        self._tick()
        return True             # GLib.SOURCE_CONTINUE

    def _tick(self) -> None:
        if self._busy:
            log.debug("spend refresh still running; tick skipped")
            return
        self._busy = True
        first = not self._scanned
        budget = self._budget()
        try:
            self._scheduler().run_in_thread(lambda: self._work(first, budget))
        except Exception:  # noqa: BLE001
            log.exception("could not start the spend worker")
            self._busy = False

    def _budget(self) -> float | None:
        try:
            value = self.config.get("budget.daily_usd")
        except Exception:  # noqa: BLE001
            return None
        try:
            value = float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
        return value if value and value > 0 else None

    # ── worker thread ──
    def _work(self, first: bool, budget: float | None) -> None:
        sched = self._scheduler()
        try:
            progress = self._progress if first else None
            snap = self._agg.refresh(progress=progress)
            spend = build_spend_state(snap, budget_usd=budget, updated_at=self._clock())
            costs = _with_resolved_keys(snap.per_file_cost, self._agg.root)
        except Exception:  # noqa: BLE001 — never raise out of a worker
            log.exception("spend refresh failed")
            sched.idle_add(self._failed)
            return
        sched.idle_add(lambda: self._apply(snap, spend, costs))

    def _progress(self, done: int, total: int) -> None:
        now = self._monotonic()
        last = self._last_progress
        if done < total and last is not None and now - last < PROGRESS_MIN_INTERVAL_S:
            return
        self._last_progress = now
        self._scheduler().idle_add(lambda: self._show_progress(done, total))

    # ── main thread (hand-offs) ──
    def _show_progress(self, done: int, total: int) -> None:
        if not self._running or self._scanned or total <= 0:
            return
        self.store.update("app", loading=Loading(done=done, total=total))

    def _apply(self, snap: Snapshot, spend, costs: dict[str, float]) -> None:
        self._busy = False
        if not self._running:
            return      # stopped while the worker ran: nothing reaches the Store
        self._scanned = True
        self._snapshot = snap
        self._file_costs = costs
        with self.store.batch():
            self.store.replace("spend", spend)
            self.store.update("app", loading=None, first_run=snap.files_total == 0)
        self._check_budget()
        self._rerun_if_asked()

    def _failed(self) -> None:
        self._busy = False
        if not self._running:
            return
        if not self._scanned:
            # Don't leave a progress bar up forever after a failed cold scan.
            self.store.update("app", loading=None)
        self._rerun_if_asked()

    def _rerun_if_asked(self) -> None:
        if self._again and self._running:
            self._again = False
            self._tick()

    def _on_config(self, key: str, _value: Any) -> None:
        if key == "budget.daily_usd":
            self._reforecast()
            self._check_budget()
        elif key == "notifications.budget":
            self._check_budget()

    def _reforecast(self) -> None:
        if self._snapshot is None:
            return
        spend = self.store.state.spend
        now = self._snapshot.now
        if now is None:
            return
        fc = forecast(spend.cost_today, self._budget(), spend.burn_per_hour, now)
        self.store.update("spend", forecast=fc)

    def _check_budget(self) -> None:
        snap = self._snapshot
        if snap is None or snap.now is None:
            return
        budget = self._budget()
        # Check the switch *before* recording thresholds: one crossed while
        # notifications are off must still fire when they are turned back on.
        if budget is None or not self._notifications_on():
            return
        spend = self.store.state.spend
        kinds = self._alerts.update(spend.cost_today, budget, snap.now.date())
        for kind in kinds:
            title, body = budget_message(kind, spend.cost_today or 0.0, budget,
                                         spend.burn_per_hour, snap.now)
            try:
                self._notify(kind, title, body, self._view_action())
            except Exception:  # noqa: BLE001 — a notifier must never break the loop
                log.warning("budget notification failed: %s", title, exc_info=True)

    def _notifications_on(self) -> bool:
        try:
            return bool(self.config.get("notifications.budget"))
        except Exception:  # noqa: BLE001
            return True

    def _view_action(self) -> BudgetAction:
        page = "overview"
        return BudgetAction("View", page, lambda: self.store.emit(
            Event("open_dashboard", {"page": page})))
