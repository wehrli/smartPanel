"""Desktop notifications, with a fallback and a hard no-crash guarantee.

Public API (what collectors and services call)::

    from smartpanel import notify
    notify.send("journal_ok", "Journal written · Fix login bug",
                "Appended to JOURNAL/2026-11.md. Session closed.",
                action=notify.Action("View", lambda: store.emit(
                    Event("open_dashboard", {"page": "journal:<id>"}))))

`send(kind, title, body="", *, action=None, key=None) -> bool`

* `kind` is one of `KINDS` (SP-2501). The kind decides the urgency, the icon,
  the *replacement key* (a new bubble of the same key replaces the old one on
  screen instead of stacking) and whether it may break through Do Not Disturb:

  ============== ======== ========= ===================================
  kind           urgency  key       notes
  ============== ======== ========= ===================================
  swap_warning   normal   swap      warning → critical → ok replace each other
  swap_critical  critical swap      the ONLY kind sent while DND is on
  swap_ok        low      swap
  journal_ok     low      journal   pass `key=` per session for Close all
  journal_failed normal   journal
  budget_80      normal   budget    100 % replaces the 80 % bubble
  budget_100     normal   budget
  ============== ======== ========= ===================================

* `action` is an optional `Action(label, callback)`: a button on the bubble
  (and the bubble's default click). The callback runs on the GLib main loop
  with no arguments. Only libnotify on a server advertising the `actions`
  capability shows it; the `notify-send` and log fallbacks drop it silently.
* `key` overrides the kind's replacement key (e.g. `"journal:claude-ledgerly"`
  so two sessions closing at once get two bubbles).
* Returns True when the bubble was handed to a backend; False when it was
  logged instead (no backend, DND, or a failure). **It never raises.**

`close(key) -> bool` withdraws the bubble last shown under that replacement
key (GNOME never expires critical bubbles, so a cleared alert closes its own).
A notification held by Do Not Disturb also closes the bubble it would have
replaced, so a critical swap bubble can't outlive a recovery DND swallowed.

Do Not Disturb (SP-2503): when `org.gnome.desktop.notifications show-banners`
is false, everything but `swap_critical` goes to the log. The setting is read
through `Gio.Settings`, guarded so a missing schema (non-GNOME desktop) means
"banners on" rather than a crash.

Backends, tried in order:

1. **libnotify via gi** (`gir1.2-notify-0.7`). In-process and sub-millisecond,
   which is what the main-loop rule demands. One notification handle is reused
   per key, so a repeating swap alert replaces its own bubble.
2. **`notify-send`**, spawned and deliberately never waited on (a shellout on
   the GLib loop would block; `Popen` without `wait()` does not). Children are
   reaped on the next send.
3. A WARNING log line.

Tests build their own `Notifier(backend=..., banners_enabled=...)`; nothing in
the test suite ever reaches a real notification daemon.

Short form: `send(summary, body, urgency, key=...)` (used by `app.py` for
toasts when no window is showing) also works — a first argument that isn't a known kind is treated as a summary.
A first argument that *looks* like a kind (`snake_case`) or an urgency outside
low/normal/critical logs one WARNING per name, so a mistyped kind is visible.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

APP_NAME = "smartPanel"
# Themed icon shown on the bubble. Present in every standard icon theme.
DEFAULT_ICON = "dialog-warning"

_ICON_BY_URGENCY = {
    "low": "dialog-information",
    "normal": "dialog-warning",
    "critical": "dialog-error",
}


@dataclass(frozen=True)
class Kind:
    urgency: str
    key: str
    icon: str
    bypass_dnd: bool = False


KINDS: dict[str, Kind] = {
    "swap_warning": Kind("normal", "swap", "dialog-warning"),
    "swap_critical": Kind("critical", "swap", "dialog-error", bypass_dnd=True),
    "swap_ok": Kind("low", "swap", "dialog-information"),
    "journal_ok": Kind("low", "journal", "document-save"),
    "journal_failed": Kind("normal", "journal", "dialog-error"),
    "budget_80": Kind("normal", "budget", "dialog-warning"),
    "budget_100": Kind("normal", "budget", "dialog-warning"),
}


@dataclass(frozen=True)
class Action:
    """A button on the bubble. `callback()` runs on the GLib main loop."""

    label: str
    callback: Callable[[], None]


# ───────────────────────── backends ─────────────────────────

class _Backend:
    """Marker base — subclasses implement `show`."""

    def show(self, summary: str, body: str, urgency: str, key: str,
             icon: str | None = None, action: Action | None = None) -> bool:
        raise NotImplementedError

    def close(self, key: str) -> bool:
        """Withdraw the bubble last shown under `key`. Default: nothing to close."""
        return False


class _LibNotify(_Backend):
    def __init__(self, notify_mod):
        self._Notify = notify_mod
        self._handles: dict[str, object] = {}
        self._actions_ok: bool | None = None

    def _supports_actions(self) -> bool:
        if self._actions_ok is None:
            try:
                self._actions_ok = "actions" in (self._Notify.get_server_caps() or [])
            except Exception:  # noqa: BLE001
                self._actions_ok = False
        return self._actions_ok

    def show(self, summary: str, body: str, urgency: str, key: str,
             icon: str | None = None, action: Action | None = None) -> bool:
        urgencies = {
            "low": self._Notify.Urgency.LOW,
            "normal": self._Notify.Urgency.NORMAL,
            "critical": self._Notify.Urgency.CRITICAL,
        }
        icon = icon or _ICON_BY_URGENCY.get(urgency, DEFAULT_ICON)
        note = self._handles.get(key)
        if note is None:
            note = self._Notify.Notification.new(summary, body, icon)
            self._handles[key] = note
        else:
            # Same key => same on-screen bubble, updated in place.
            note.update(summary, body, icon)
            note.clear_actions()
        note.set_urgency(urgencies.get(urgency, self._Notify.Urgency.NORMAL))
        if action is not None and self._supports_actions():
            cb = _guarded(action)
            note.add_action("default", action.label, cb, None)
            note.add_action("open", action.label, cb, None)
        note.show()
        return True

    def close(self, key: str) -> bool:
        # Critical bubbles never expire on their own in GNOME, so a cleared
        # alert must take its bubble down explicitly.
        note = self._handles.pop(key, None)
        if note is None:
            return False
        try:
            note.close()
        except Exception as e:  # noqa: BLE001 — already gone, daemon restarted…
            log.debug("closing notification %r failed: %s", key, e)
        return True


def _guarded(action: Action):
    def cb(_note, _action_id, _user_data=None):
        try:
            action.callback()
        except Exception:  # noqa: BLE001 — a click must not kill the loop
            log.exception("notification action %r failed", action.label)
    return cb


class _NotifySend(_Backend):
    def __init__(self):
        self._pending: list[subprocess.Popen] = []

    def show(self, summary: str, body: str, urgency: str, key: str,
             icon: str | None = None, action: Action | None = None) -> bool:
        self._reap()
        cmd = [
            "notify-send",
            "--app-name", APP_NAME,
            "--urgency", urgency if urgency in _ICON_BY_URGENCY else "normal",
            "--icon", icon or _ICON_BY_URGENCY.get(urgency, DEFAULT_ICON),
            summary,
            body,
        ]
        # Never wait: this runs on the GLib main loop. Actions would need a
        # blocking `--wait`, so this route drops them.
        self._pending.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL))
        return True

    def _reap(self) -> None:
        """Collect finished `notify-send` children so they don't linger as zombies."""
        for proc in list(self._pending):
            if proc.poll() is not None:
                self._pending.remove(proc)


def _default_backend() -> _Backend | None:
    try:
        import gi
        gi.require_version("Notify", "0.7")
        from gi.repository import Notify
        if Notify.init(APP_NAME):
            log.debug("notifications via libnotify")
            return _LibNotify(Notify)
        log.debug("Notify.init() refused — falling back to notify-send")
    except (ImportError, ValueError, AttributeError) as e:
        log.debug("libnotify unavailable (%s) — falling back to notify-send", e)

    if shutil.which("notify-send"):
        log.debug("notifications via notify-send")
        return _NotifySend()
    return None


# ───────────────────────── Do Not Disturb ─────────────────────────

DND_SCHEMA = "org.gnome.desktop.notifications"
DND_KEY = "show-banners"


class _GnomeBanners:
    """`show-banners` via Gio.Settings. Missing gi/schema/key => banners on."""

    def __init__(self):
        self._settings = None
        self._tried = False

    def __call__(self) -> bool:
        if not self._tried:
            self._tried = True
            try:
                from gi.repository import Gio
                source = Gio.SettingsSchemaSource.get_default()
                schema = source.lookup(DND_SCHEMA, True) if source else None
                if schema is not None and schema.has_key(DND_KEY):
                    self._settings = Gio.Settings.new(DND_SCHEMA)
            except Exception as e:  # noqa: BLE001
                log.debug("DND setting unavailable: %s", e)
        if self._settings is None:
            return True
        try:
            return bool(self._settings.get_boolean(DND_KEY))
        except Exception:  # noqa: BLE001
            return True


# ───────────────────────── notifier ─────────────────────────

_UNSET = object()
_URGENCIES = ("low", "normal", "critical")
_KIND_LIKE = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)+")


class Notifier:
    """Kind → bubble policy around a backend. Injectable for tests."""

    def __init__(self, backend=_UNSET, banners_enabled: Callable[[], bool] | None = None):
        self._backend = backend
        self._banners_enabled = banners_enabled or _GnomeBanners()
        self._warned_unknown: set[str] = set()

    def _get_backend(self) -> _Backend | None:
        if self._backend is _UNSET:
            self._backend = _default_backend()
        return self._backend  # type: ignore[return-value]

    def _dnd(self) -> bool:
        try:
            return not self._banners_enabled()
        except Exception:  # noqa: BLE001
            return False

    def send(self, kind: str, title: str = "", body: str = "", *,
             action: Action | None = None, key: str | None = None,
             urgency: str | None = None) -> bool:
        try:
            spec = KINDS.get(kind)
            if spec is None:
                # Legacy positional form: send(summary, body, urgency, key=...).
                summary, body, legacy_urgency = kind, title, (body or "normal")
                self._check_legacy(kind, urgency or legacy_urgency)
                return self._show(summary, body, urgency or legacy_urgency,
                                  key or "smartpanel", None, action,
                                  bypass_dnd=(urgency or legacy_urgency) == "critical")
            return self._show(title, body, urgency or spec.urgency, key or spec.key,
                              spec.icon, action, bypass_dnd=spec.bypass_dnd)
        except Exception as e:  # noqa: BLE001 — an alert must not kill the loop
            log.warning("notification failed (%s): %s — %s", e, title, body)
            return False

    def _check_legacy(self, first: str, urgency: str) -> None:
        """Make a mistyped kind loud: `send("journal_succeeded", ...)` would
        otherwise show a bubble titled with the identifier."""
        if first in self._warned_unknown:
            return
        if _KIND_LIKE.fullmatch(first) or urgency not in _URGENCIES:
            self._warned_unknown.add(first)
            log.warning("notify.send: %r is not a known kind (%s); treated as a "
                        "legacy summary with urgency %r", first,
                        ", ".join(sorted(KINDS)), urgency)

    def close(self, key: str) -> bool:
        """Take down the bubble shown under `key`, if any. Never raises."""
        try:
            backend = self._backend
            if backend is _UNSET or backend is None:
                return False     # nothing was ever shown
            return bool(backend.close(key))
        except Exception as e:  # noqa: BLE001
            log.debug("close(%r) failed: %s", key, e)
            return False

    def _show(self, summary, body, urgency, key, icon, action, *, bypass_dnd) -> bool:
        if not bypass_dnd and self._dnd():
            log.info("notification held by Do Not Disturb: %s — %s", summary, body)
            # The update is held, but a bubble it would have replaced (e.g. a
            # never-expiring critical swap bubble) must not outlive its alert.
            self.close(key)
            return False
        backend = self._get_backend()
        if backend is None:
            log.warning("notification (no backend): %s — %s", summary, body)
            return False
        return backend.show(summary, body, urgency, key, icon=icon, action=action)


_default: Notifier | None = None


def default_notifier() -> Notifier:
    global _default
    if _default is None:
        _default = Notifier()
    return _default


def send(kind: str, title: str = "", body: str = "", *,
         action: Action | None = None, key: str | None = None,
         urgency: str | None = None) -> bool:
    """Show a notification of `kind` (see the module docstring). Never raises."""
    try:
        return default_notifier().send(kind, title, body, action=action, key=key,
                                       urgency=urgency)
    except Exception as e:  # noqa: BLE001
        log.warning("notification failed (%s): %s — %s", e, title, body)
        return False


def close(key: str) -> bool:
    """Withdraw the bubble last shown under replacement `key`. Never raises."""
    try:
        return default_notifier().close(key)
    except Exception as e:  # noqa: BLE001
        log.debug("close(%r) failed: %s", key, e)
        return False
