"""Real implementation of state.actions.Actions + the session refresh loop.

CONTRACT STUB (wave 0). WP-sessions implements; API fixed:
    Controller(store, config, spend=None)   # spend: SpendService for per-session cost
    .start() / .stop()                       # starts the 2 s session refresh, journal index
    + every method of state.actions.Actions
Navigation actions (open_dashboard, open_palette, request_*) only emit Events; the app
turns those into windows.
"""
from __future__ import annotations

from smartpanel.state import Event


class Controller:
    def __init__(self, store, config, spend=None):
        self.store, self.config, self.spend = store, config, spend

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def open_dashboard(self, page: str = "") -> None:
        self.store.emit(Event("open_dashboard", {"page": page}))

    def open_palette(self) -> None:
        self.store.emit(Event("open_palette", {}))

    def __getattr__(self, name):
        def _noop(*_a, **_k):
            self.store.toast(f"{name} is not wired yet")
        return _noop
