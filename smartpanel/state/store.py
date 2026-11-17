"""The single mutable owner of `PanelState`, with change notification.

Threading: mutate ONLY on the GLib main thread. Worker threads hand results back with
`GLib.idle_add(lambda: store.update(...))`. The store itself never imports GLib, so it
stays usable in tests and headless tools.

Usage:
    store = Store()
    store.subscribe(lambda sections: print(sections))      # set[str] of SECTIONS
    store.update("system", updated_at=now)                  # top-level fields of a section
    store.mutate("sessions", lambda s: s.items.append(x))   # arbitrary in-place change
    with store.batch():                                     # coalesce into one notification
        ...
    store.emit(Event("toast", {"text": "Copied"}))          # one-shot feedback
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
from typing import Any, Callable

from smartpanel.state.model import SECTIONS, Event, PanelState

log = logging.getLogger(__name__)

ChangeListener = Callable[[set[str]], None]
EventListener = Callable[[Event], None]


def to_dict(obj: Any) -> Any:
    """Dataclass tree -> plain JSON-able structure (properties are not included)."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [to_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): to_dict(v) for k, v in obj.items()}
    return obj


class Store:
    def __init__(self, state: PanelState | None = None):
        self.state = state or PanelState()
        self._listeners: list[ChangeListener] = []
        self._event_listeners: list[EventListener] = []
        self._batch_depth = 0
        self._pending: set[str] = set()

    # ── subscriptions ──
    def subscribe(self, fn: ChangeListener) -> Callable[[], None]:
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def on_event(self, fn: EventListener) -> Callable[[], None]:
        self._event_listeners.append(fn)
        return lambda: self._event_listeners.remove(fn) if fn in self._event_listeners else None

    # ── mutation ──
    def section(self, name: str) -> Any:
        if name not in SECTIONS:
            raise KeyError(f"unknown section {name!r}")
        return getattr(self.state, name)

    def update(self, section: str, **fields: Any) -> bool:
        """Set top-level fields of a section. Notifies only if something changed."""
        obj = self.section(section)
        changed = False
        for key, value in fields.items():
            if not hasattr(obj, key):
                raise AttributeError(f"{section}.{key} is not a field")
            if getattr(obj, key) != value:
                setattr(obj, key, value)
                changed = True
        if changed:
            self.touch(section)
        return changed

    def replace(self, section: str, value: Any) -> None:
        """Swap a whole section object (e.g. a freshly computed SpendState)."""
        self.section(section)
        if getattr(self.state, section) != value:
            setattr(self.state, section, value)
            self.touch(section)

    def mutate(self, section: str, fn: Callable[[Any], Any]) -> None:
        """Arbitrary in-place change; always notifies."""
        fn(self.section(section))
        self.touch(section)

    def touch(self, section: str) -> None:
        self._pending.add(section)
        if self._batch_depth == 0:
            self._flush()

    @contextlib.contextmanager
    def batch(self):
        self._batch_depth += 1
        try:
            yield self
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0:
                self._flush()

    def _flush(self) -> None:
        if not self._pending:
            return
        sections, self._pending = self._pending, set()
        for fn in list(self._listeners):
            try:
                fn(set(sections))
            except Exception:  # a broken surface must never break the others
                log.exception("state listener failed")

    # ── events ──
    def emit(self, event: Event) -> None:
        for fn in list(self._event_listeners):
            try:
                fn(event)
            except Exception:
                log.exception("event listener failed")

    def toast(self, text: str, *, error: bool = False,
              action: dict | None = None) -> None:
        self.emit(Event("toast", {"text": text, "error": error, "action": action}))

    # ── serialization ──
    def to_dict(self, sections: set[str] | None = None) -> dict:
        names = sections or set(SECTIONS)
        return {n: to_dict(getattr(self.state, n)) for n in SECTIONS if n in names}

    def to_json(self, sections: set[str] | None = None) -> str:
        return json.dumps(self.to_dict(sections), separators=(",", ":"))
