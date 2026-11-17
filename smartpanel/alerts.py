"""Threshold alerting — pure, GTK-free, unit-testable.

Today this carries one alert: **swap is nearly full**, which is the moment a
Linux desktop stops being merely busy and starts thrashing. It is a genuinely
useful early warning precisely because swap pressure is invisible in the usual
CPU/RAM readouts — RAM sits pinned at ~100% long before the machine stalls,
while the swap gauge keeps climbing and tells you how much runway is left.

Two things come out of here and nothing else:

* `swap_status()` — the always-on top-bar gauge ('🔃12%'), plus `swap_badge()`,
  its alert-only variant for themes that don't place the gauge themselves.
* `AlertTracker.update()` — a state machine that says *when* to raise a desktop
  notification, so a machine parked at 90% swap notifies once and then goes
  quiet instead of firing on every poll tick.

Both are pure functions of their inputs (the tracker owns only its own state),
so the whole policy is testable without a display server. GTK/libnotify live in
`notify.py`; the wiring lives in `collect/system.py` (`SystemCollector` owns the
tracker and raises the notification).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import IntEnum

log = logging.getLogger(__name__)


class Level(IntEnum):
    """Severity. IntEnum so escalation is a plain `>` comparison."""

    OK = 0
    WARNING = 1
    CRITICAL = 2


# Glyphs. The top bar is width-constrained (~30 chars — see CLAUDE.md), so the
# badge is severity + the swap arrows + the number, with no padding.
SWAP_GLYPH = "🔃"
LEVEL_GLYPH: dict[Level, str] = {
    Level.OK: "",
    Level.WARNING: "⚠",
    Level.CRITICAL: "🔴",
}
# Shown in place of a percentage when the machine has no swap configured.
NO_SWAP = "—"


@dataclass(frozen=True)
class Thresholds:
    """Percent boundaries plus the hysteresis band used to step back down.

    `hysteresis` is why the badge doesn't flicker: swap hovering either side of
    a boundary would otherwise toggle the alert (and the notification) on every
    poll. Escalation is immediate; de-escalation waits until the value has
    fallen this many points *below* the boundary it crossed.
    """

    warning: float = 60.0
    critical: float = 85.0
    hysteresis: float = 5.0
    # Seconds before a still-critical alert notifies again. WARNING never
    # repeats — one nudge is enough for a condition that isn't urgent yet.
    repeat_s: float = 600.0


def thresholds_from_env(env: dict[str, str] | None = None) -> Thresholds:
    """Read `SMARTPANEL_SWAP_*` overrides, falling back to the defaults.

    Never raises: a garbled value is logged and the default is kept, because a
    typo in a user's environment must not take the whole panel down.
    """
    env = os.environ if env is None else env
    d = Thresholds()

    def num(name: str, default: float) -> float:
        raw = env.get(name)
        if raw is None or raw == "":
            return default
        try:
            return float(raw)
        except ValueError:
            log.warning("%s=%r is not a number — using %g", name, raw, default)
            return default

    warning = num("SMARTPANEL_SWAP_WARN", d.warning)
    critical = num("SMARTPANEL_SWAP_CRIT", d.critical)
    if critical < warning:
        log.warning("SMARTPANEL_SWAP_CRIT (%g) is below SMARTPANEL_SWAP_WARN (%g) "
                    "— raising it to match", critical, warning)
        critical = warning
    return Thresholds(
        warning=warning,
        critical=critical,
        hysteresis=max(0.0, num("SMARTPANEL_SWAP_HYSTERESIS", d.hysteresis)),
        repeat_s=max(0.0, num("SMARTPANEL_SWAP_REPEAT", d.repeat_s)),
    )


def make_thresholds(warning, critical, hysteresis=5.0, repeat_s=600.0) -> Thresholds:
    """Thresholds from loosely-typed settings values (config file / Settings UI).

    Same guarantees as `thresholds_from_env`: never raises (a non-number keeps
    the default), a negative hysteresis/repeat is floored at 0, and a critical
    below warning is clamped up to it rather than honoured.
    """
    d = Thresholds()

    def num(name: str, raw, default: float) -> float:
        if raw is None or raw == "" or isinstance(raw, bool):
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            log.warning("swap.%s=%r is not a number — using %g", name, raw, default)
            return default

    w = num("warn", warning, d.warning)
    c = num("crit", critical, d.critical)
    if c < w:
        log.warning("swap.crit (%g) is below swap.warn (%g) — raising it to match", c, w)
        c = w
    return Thresholds(
        warning=w,
        critical=c,
        hysteresis=max(0.0, num("hysteresis", hysteresis, d.hysteresis)),
        repeat_s=max(0.0, num("repeat_s", repeat_s, d.repeat_s)),
    )


def raw_level(percent: float | None, t: Thresholds) -> Level:
    """Severity for a reading, ignoring history. `None` (no swap) is always OK."""
    if percent is None:
        return Level.OK
    if percent >= t.critical:
        return Level.CRITICAL
    if percent >= t.warning:
        return Level.WARNING
    return Level.OK


def next_level(percent: float | None, current: Level, t: Thresholds) -> Level:
    """Apply hysteresis: escalate at once, de-escalate only past the band."""
    raw = raw_level(percent, t)
    if raw >= current:
        return raw
    boundary = t.critical if current is Level.CRITICAL else t.warning
    if percent is not None and percent >= boundary - t.hysteresis:
        return current  # still inside the sticky band — hold the current level
    return raw


def swap_status(level: Level, percent: float | None) -> str:
    """Always-on top-bar readout, e.g. '🔃12%', '⚠🔃62%', '🔴🔃92%'.

    This is the permanent gauge: swap pressure is the one number the usual
    CPU/RAM readouts hide, so it is worth a few characters even while nothing
    is wrong. Only the severity glyph comes and goes. A box with no swap reads
    '🔃—' — the same "not configured" dash the menu row shows, never a
    reassuring 0%.
    """
    if percent is None:
        return f"{SWAP_GLYPH}{NO_SWAP}"
    return f"{LEVEL_GLYPH[level]}{SWAP_GLYPH}{percent:.0f}%"


def swap_badge(level: Level, percent: float | None) -> str:
    """Compact top-bar *alert*, e.g. '🔴🔃92%'. Empty string when everything is OK.

    The alert-only counterpart to `swap_status`: it is what themes that don't
    carry the permanent gauge get prefixed onto their label while swap is high.
    """
    if level is Level.OK or percent is None:
        return ""
    return swap_status(level, percent)


def swap_headline(level: Level) -> str:
    match level:
        case Level.CRITICAL:
            return "Swap almost full"
        case Level.WARNING:
            return "Swap filling up"
        case _:
            return "Swap back to normal"


def swap_detail(level: Level, percent: float | None, used_gib: float | None,
                total_gib: float | None) -> str:
    """One human sentence for the notification body and dashboard banner."""
    if percent is None:
        return "No swap configured."
    size = ""
    if used_gib is not None and total_gib:
        size = f" ({used_gib:.1f} of {total_gib:.1f} GiB)"
    match level:
        case Level.CRITICAL:
            return (f"Swap is {percent:.0f}% full{size}. The system will start "
                    f"thrashing — close something heavy.")
        case Level.WARNING:
            return f"Swap is {percent:.0f}% full{size}. Memory pressure is building."
        case _:
            return f"Swap is back down to {percent:.0f}%{size}."


@dataclass(frozen=True)
class AlertEvent:
    """A notification the caller should raise. `level is OK` means 'recovered'."""

    level: Level
    summary: str
    body: str
    urgency: str  # "low" | "normal" | "critical" — libnotify vocabulary


class AlertTracker:
    """Turns a stream of swap readings into the few notifications worth showing.

    Call `update(percent, used, total, now)` on every poll. It returns an
    `AlertEvent` only on a state change (or a critical repeat) and `None`
    otherwise, so the caller can hand the result straight to `notify.send()`
    without keeping any state of its own. `self.level` is the current severity
    and is what the badge renders from.
    """

    def __init__(self, thresholds: Thresholds | None = None):
        self.thresholds = thresholds or thresholds_from_env()
        self.level = Level.OK
        self.percent: float | None = None
        self._last_notified_at: float | None = None

    @property
    def badge(self) -> str:
        return swap_badge(self.level, self.percent)

    @property
    def status(self) -> str:
        """The always-on gauge — see `swap_status`."""
        return swap_status(self.level, self.percent)

    @property
    def active(self) -> bool:
        return self.level is not Level.OK

    @property
    def alerted(self) -> bool:
        """True between an alert notification and its recovery (or a quiet reset)."""
        return self._last_notified_at is not None

    def reconfigure(self, thresholds: Thresholds) -> Level:
        """Swap in new thresholds live (Settings), without a notification.

        The current level is kept unless the new thresholds *lower* it past the
        hysteresis band (e.g. warn raised well above the current reading); that
        drop is applied here, silently, so the next `update()` can't turn it into
        a "back to normal" bubble the user didn't need. A level the new
        thresholds would *raise* is left for the next reading, which notifies
        as any escalation does. The critical repeat clock is not reset.
        Returns the (possibly lowered) level.
        """
        self.thresholds = thresholds
        target = next_level(self.percent, self.level, thresholds)
        if target < self.level:
            self.level = target
            if target is Level.OK:
                self._last_notified_at = None   # nothing left to recover from
        return self.level

    def update(self, percent: float | None, used_gib: float | None = None,
               total_gib: float | None = None, now: float = 0.0) -> AlertEvent | None:
        t = self.thresholds
        previous = self.level
        self.percent = percent
        self.level = next_level(percent, previous, t)

        if self.level is previous:
            # Same severity. Only a lingering CRITICAL nags again, and only
            # after repeat_s — WARNING stays a single nudge.
            if self.level is not Level.CRITICAL or not t.repeat_s:
                return None
            if self._last_notified_at is None:
                return None
            if now - self._last_notified_at < t.repeat_s:
                return None
        elif self.level is Level.OK:
            # Recovered — worth saying once, but only if we actually alerted.
            if self._last_notified_at is None:
                return None
        elif self.level < previous:
            # CRITICAL -> WARNING: the situation improved. Update the badge but
            # stay quiet; a notification here would read as a fresh alarm.
            return None

        self._last_notified_at = None if self.level is Level.OK else now
        return AlertEvent(
            level=self.level,
            summary=swap_headline(self.level),
            body=swap_detail(self.level, percent, used_gib, total_gib),
            urgency=_URGENCY[self.level],
        )


_URGENCY: dict[Level, str] = {
    Level.OK: "low",
    Level.WARNING: "normal",
    Level.CRITICAL: "critical",
}
