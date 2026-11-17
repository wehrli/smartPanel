"""Single place that pins the GTK 4 / libadwaita versions for the widget kit."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402,F401
