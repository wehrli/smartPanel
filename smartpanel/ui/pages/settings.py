"""Dashboard page 'settings'. Wave-1 placeholder; replaced wholesale in wave 2.

API (fixed for every page): build(store, actions, config) -> Gtk.Widget
The returned widget subscribes to the store itself and updates in place.
"""
from __future__ import annotations


def build(store, actions, config):
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    return Adw.StatusPage(title="settings".capitalize(), description="Coming in wave 2")
