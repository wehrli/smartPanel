"""Exports dev.omdev.SmartPanel1 (panel1.xml) at /dev/omdev/SmartPanel.

CONTRACT STUB (wave 0). WP-dbus implements; API fixed:
    PanelService(store, actions).export(connection) -> None ; .unexport()
Called from the Adw.Application's do_dbus_register (same connection as the app).
"""
from __future__ import annotations

OBJECT_PATH = "/dev/omdev/SmartPanel"
INTERFACE = "dev.omdev.SmartPanel1"


class PanelService:
    def __init__(self, store, actions):
        self.store, self.actions = store, actions

    def export(self, connection) -> None:
        pass

    def unexport(self) -> None:
        pass
