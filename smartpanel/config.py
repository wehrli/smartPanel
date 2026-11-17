"""Settings: ~/.config/smartpanel/config.yaml with env-var precedence.

CONTRACT STUB (wave 0). WP-core replaces the body; the public API below is fixed:
    cfg = Config.load(path=None)        # never raises; bad file -> defaults + warning
    cfg.get("swap.warn") -> value
    cfg.set("swap.warn", 70)            # validates, persists atomically, notifies
    cfg.overridden_by("swap.warn") -> "SMARTPANEL_SWAP_WARN" | None
    cfg.subscribe(fn(key, value)) -> unsubscribe
    cfg.all() -> dict[key, effective value]
Keys and defaults: docs/architecture/v2.md "Settings keys".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

DEFAULTS: dict[str, Any] = {
    "bar.strip": True, "bar.graphs": True, "bar.text": True, "bar.ticker": False,
    "bar.ticker_seconds": 5,
    "swap.warn": 60, "swap.crit": 85, "swap.hysteresis": 5, "swap.repeat_s": 600,
    "budget.daily_usd": None, "notifications.budget": True,
    "sessions.journal": True, "sessions.min_age_s": 120, "sessions.confirm_seconds": 5,
    "sessions.reattach": True, "sessions.dir": "~/dev/omdev", "sessions.terminal": "terminator",
    "disk.mount": "/",
    "hud.enabled": False, "hud.corner": "top-right",
    "appearance.style": "system",
    "palette.shortcut": "<Ctrl><Alt>space",
}


class Config:
    def __init__(self, values: dict[str, Any] | None = None, path: Path | None = None):
        self._values = dict(DEFAULTS)
        self._values.update(values or {})
        self.path = path
        self._subs: list[Callable[[str, Any], None]] = []

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        return cls(path=path)

    def get(self, key: str) -> Any:
        return self._values.get(key, DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value
        for fn in list(self._subs):
            fn(key, value)

    def overridden_by(self, key: str) -> str | None:
        return None

    def subscribe(self, fn: Callable[[str, Any], None]) -> Callable[[], None]:
        self._subs.append(fn)
        return lambda: self._subs.remove(fn) if fn in self._subs else None

    def all(self) -> dict[str, Any]:
        return dict(self._values)
