"""Demo mode: the prototype's scenarios without real sensors or sessions.

CONTRACT STUB (wave 0). WP-core implements; the API is fixed:
    SCENARIOS: tuple[str, ...]  = ("busy","calm","alert","empty","many","cold","first","notmux","noswap")
    build_state(name) -> PanelState          # pure, deterministic, prototype data
    DemoController(store, config)            # implements state.actions.Actions with GLib timers
"""
from __future__ import annotations

from smartpanel.state import PanelState

SCENARIOS: tuple[str, ...] = ("busy", "calm", "alert", "empty", "many",
                              "cold", "first", "notmux", "noswap")


def build_state(name: str) -> PanelState:
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}; choose from {', '.join(SCENARIOS)}")
    return PanelState()


class DemoController:
    def __init__(self, store, config):
        self.store, self.config = store, config

    def __getattr__(self, name):          # stub: every action just toasts
        def _noop(*_a, **_k):
            self.store.toast(f"demo: {name} not implemented yet")
        return _noop
