"""Desktop notifications, with a fallback and a hard no-crash guarantee.

The alert policy lives in `alerts.py`; this module only knows how to put a
bubble on screen. Two routes, tried in order:

1. **libnotify via gi** (`gir1.2-notify-0.7`). In-process and sub-millisecond,
   which is what the main-loop rule demands. It also lets us *reuse* one
   notification handle per alert key, so a repeating swap alert replaces its
   own bubble instead of stacking a new one every ten minutes.
2. **`notify-send`**, spawned and deliberately never waited on. A shellout on
   the GLib loop would block; `Popen` without `wait()` does not. The child is
   reaped opportunistically on the next send.

If neither is available (no notification daemon, headless, a session over SSH)
the message is logged at WARNING and the panel carries on. An alert failing to
display must never be the reason the panel dies, so every entry point here is
wrapped — `send()` cannot raise.
"""
from __future__ import annotations

import logging
import shutil
import subprocess

log = logging.getLogger(__name__)

APP_NAME = "smartPanel"
# Themed icon shown on the bubble. Present in every standard icon theme.
DEFAULT_ICON = "dialog-warning"

_ICON_BY_URGENCY = {
    "low": "dialog-information",
    "normal": "dialog-warning",
    "critical": "dialog-error",
}

_backend: "_Backend | None" = None
_pending: list[subprocess.Popen] = []


class _Backend:
    """Marker base — subclasses implement `show`."""

    def show(self, summary: str, body: str, urgency: str, key: str) -> bool:
        raise NotImplementedError


class _LibNotify(_Backend):
    def __init__(self, notify_mod):
        self._Notify = notify_mod
        self._handles: dict[str, object] = {}

    def show(self, summary: str, body: str, urgency: str, key: str) -> bool:
        urgencies = {
            "low": self._Notify.Urgency.LOW,
            "normal": self._Notify.Urgency.NORMAL,
            "critical": self._Notify.Urgency.CRITICAL,
        }
        icon = _ICON_BY_URGENCY.get(urgency, DEFAULT_ICON)
        note = self._handles.get(key)
        if note is None:
            note = self._Notify.Notification.new(summary, body, icon)
            self._handles[key] = note
        else:
            # Same key => same on-screen bubble, updated in place.
            note.update(summary, body, icon)
        note.set_urgency(urgencies.get(urgency, self._Notify.Urgency.NORMAL))
        note.show()
        return True


class _NotifySend(_Backend):
    def show(self, summary: str, body: str, urgency: str, key: str) -> bool:
        _reap()
        cmd = [
            "notify-send",
            "--app-name", APP_NAME,
            "--urgency", urgency if urgency in _ICON_BY_URGENCY else "normal",
            "--icon", _ICON_BY_URGENCY.get(urgency, DEFAULT_ICON),
            summary,
            body,
        ]
        # Never wait: this runs on the GLib main loop.
        _pending.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL))
        return True


def _reap() -> None:
    """Collect finished `notify-send` children so they don't linger as zombies."""
    for proc in list(_pending):
        if proc.poll() is not None:
            _pending.remove(proc)


def _get_backend() -> _Backend | None:
    global _backend
    if _backend is not None:
        return _backend
    try:
        import gi
        gi.require_version("Notify", "0.7")
        from gi.repository import Notify
        if Notify.init(APP_NAME):
            _backend = _LibNotify(Notify)
            log.debug("notifications via libnotify")
            return _backend
        log.debug("Notify.init() refused — falling back to notify-send")
    except (ImportError, ValueError, AttributeError) as e:
        log.debug("libnotify unavailable (%s) — falling back to notify-send", e)

    if shutil.which("notify-send"):
        _backend = _NotifySend()
        log.debug("notifications via notify-send")
        return _backend
    return None


def send(summary: str, body: str = "", urgency: str = "normal",
         key: str = "smartpanel") -> bool:
    """Show a notification. Returns True if it was handed to a backend.

    `key` groups repeats of the same alert onto one bubble (libnotify route).
    Failure is never fatal — it degrades to a log line.
    """
    try:
        backend = _get_backend()
        if backend is None:
            log.warning("notification (no backend): %s — %s", summary, body)
            return False
        return backend.show(summary, body, urgency, key)
    except Exception as e:  # noqa: BLE001 — an alert must not kill the loop
        log.warning("notification failed (%s): %s — %s", e, summary, body)
        return False
