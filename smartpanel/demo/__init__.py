"""Demo mode: the prototype's scenarios without real sensors or sessions (SP-0012).

API (fixed by the v2 contract):
    SCENARIOS: tuple[str, ...]  = ("busy","calm","alert","empty","many","cold","first","notmux","noswap")
    build_state(name, now=None) -> PanelState   # pure, deterministic for a given now
    DemoController(store, config)               # implements state.actions.Actions with GLib timers
"""
from __future__ import annotations

from smartpanel.demo.controller import DemoController
from smartpanel.demo.scenarios import DEMO_NOW, DESCRIPTIONS, SCENARIOS, build_state

__all__ = ["SCENARIOS", "DESCRIPTIONS", "DEMO_NOW", "build_state", "DemoController"]
