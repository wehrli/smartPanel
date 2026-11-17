"""Panel item: StatusNotifierItem + DBusMenu over Gio, wide PNG bar icon + XAyatanaLabel.

CONTRACT STUB (wave 0). WP-tray implements; API fixed:
    Tray(store, actions, config).start(connection: Gio.DBusConnection) / .stop()
"""
from __future__ import annotations


class Tray:
    def __init__(self, store, actions, config):
        self.store, self.actions, self.config = store, actions, config

    def start(self, connection) -> None:
        pass

    def stop(self) -> None:
        pass
