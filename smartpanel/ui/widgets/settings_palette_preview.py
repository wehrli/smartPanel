"""Live preview of the real top bar (SP-1908), for Settings → Top bar.

A black strip, like the GNOME panel, holding exactly what the tray would show:
`tray.barspec.compose_bar(state, settings)` for the model and
`tray.barrender.render_png` for the A6 strip / A4 graphs, with the A1/A5 label
beside it. `refresh()` is cheap when nothing changed: the PNG is only rendered
again when the `IconSpec` (hashable) or the scale differs.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

from smartpanel.ui.widgets._gi import GLib, Gtk  # noqa: F401

import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk  # noqa: E402

from smartpanel.tray import barrender, barspec  # noqa: E402

log = logging.getLogger(__name__)

EMPTY_NOTE = "Everything is off: the bar shows only the smartPanel icon"


class BarPreview(Gtk.Box):
    def __init__(self):
        super().__init__(spacing=6, hexpand=True, accessible_role=Gtk.AccessibleRole.IMG)
        self.add_css_class("sp-sp-preview")
        inner = Gtk.Box(spacing=6, halign=Gtk.Align.END, hexpand=True,
                        valign=Gtk.Align.CENTER)
        self.picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.FILL,
                                   valign=Gtk.Align.CENTER)
        self.icon = Gtk.Image(pixel_size=16, valign=Gtk.Align.CENTER)
        self.icon.add_css_class("sp-sp-preview-icon")
        self.label = Gtk.Label(valign=Gtk.Align.CENTER)
        self.label.add_css_class("sp-sp-preview-label")
        for w in (self.picture, self.icon, self.label):
            inner.append(w)
        self.append(inner)
        self._key: tuple | None = None
        self.spec: barspec.BarSpec | None = None

    def refresh(self, state, settings: Mapping[str, Any] | Any) -> barspec.BarSpec:
        try:
            spec = barspec.compose_bar(state, settings)
        except Exception:  # noqa: BLE001 — the preview must never break Settings
            log.exception("bar preview failed")
            spec = barspec.BarSpec(label="")
        self.spec = spec
        scale = max(1, self.get_scale_factor() or 1)
        key = (spec.icon, scale, spec.icon_name, spec.label)
        if key == self._key:
            return spec
        self._key = key
        if spec.icon is not None:
            try:
                png = barrender.render_png(spec.icon, scale=scale)
                lay = barrender.layout(spec.icon)
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
                self.picture.set_paintable(texture)
                self.picture.set_size_request(lay["width"], lay["height"])
                self.picture.set_visible(True)
                self.icon.set_visible(False)
            except Exception:  # noqa: BLE001
                log.exception("bar preview render failed")
                self.picture.set_visible(False)
                self.icon.set_from_icon_name(barspec.ICON_PLAIN)
                self.icon.set_visible(True)
        else:
            self.picture.set_visible(False)
            self.icon.set_from_icon_name(spec.icon_name or barspec.ICON_PLAIN)
            self.icon.set_visible(True)
        self.label.set_text(spec.label)
        self.label.set_visible(bool(spec.label))
        desc = spec.accessible_desc if (spec.icon or spec.label) else EMPTY_NOTE
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"Top bar preview: {desc}"])
        self.set_tooltip_text(None if (spec.icon or spec.label or spec.loading) else EMPTY_NOTE)
        return spec

    @property
    def showing_icon_only(self) -> bool:
        return self.spec is not None and self.spec.icon is None and not self.spec.label
