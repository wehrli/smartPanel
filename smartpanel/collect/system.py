"""System metrics collector: GLib timeouts -> Store `system` section.

CONTRACT STUB (wave 0). WP-system implements; API fixed:
    SystemCollector(store, config, theme=None).start() / .stop()
Owns swap alerting (alerts.AlertTracker + notify.py) and metric history (history.RingBuffer).
"""
from __future__ import annotations


class SystemCollector:
    def __init__(self, store, config, theme=None):
        self.store, self.config, self.theme = store, config, theme

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass
