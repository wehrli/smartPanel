"""Dashboard page 'settings' (SP-2401, SP-2404, SP-1908, SP-0905, SP-2204, SP-2303, SP-2304).

    build(store, actions, config, *, shortcut_backend=None) -> Adw.PreferencesPage

One `Adw.PreferencesGroup` per `config.SPECS` group, in the prototype's order, with a
row for every key, so nothing in the settings file is unreachable from here:

    Top bar      live preview of the real bar · Session strip A6 · Live graphs A4 ·
                 Numbers A1 · Rotate readings A5 · Rotate every
    Swap alert   Warning at / Critical at (sliders, %) · Hysteresis · Repeat critical every
    Spending     Daily budget (empty = off) · Notify when over budget
    Sessions     Journal before closing · Only if open longer than (min, stored in s) ·
                 Auto-confirm after · Re-attach if focus fails · New sessions start in
                 (folder chooser) · Terminal
    Desktop      Desktop HUD B6 · HUD corner · HUD margin · Command palette shortcut B5
    System       Disk to watch
    Appearance   Window style: Follow system / Light / Dark

Rules this page keeps:
- **Every change goes through `actions.set_setting(key, value)`**; the page never
  writes the config itself. Values flow back through `store.settings` and every
  control re-syncs in place (no rebuild, focus and scroll survive).
- **Env-owned keys** (`store.settings.overridden_by`) are insensitive and say
  "Set by SMARTPANEL_…" instead of their hint.
- **Validation (SP-2404)**: sliders can't put Critical below Warning (dragging
  Warning past Critical pushes it up, as `Config.set` does); numbers can't go below
  their spec's minimum; a folder that doesn't exist, a bad amount or path shows an
  inline error on its row and is not saved.
- Sliders and spin buttons are debounced (`DEBOUNCE_MS`) so a drag writes the
  file once, not once per pixel.
- **Add shortcut** writes a GNOME custom keybinding only after an Adw.AlertDialog
  confirmation, through an injectable backend: `--demo` runs and tests get the
  in-memory one, so the user's real dconf is never touched by them.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from smartpanel.config import DEFAULTS, HUD_CORNERS, SPECS, STYLES, Spec  # noqa: E402
from smartpanel.ui import tokens  # noqa: E402
from smartpanel.ui.widgets import settings_palette_css as css  # noqa: E402
from smartpanel.ui.widgets import settings_palette_shortcut as shortcut  # noqa: E402
from smartpanel.ui.widgets.settings_palette_preview import BarPreview  # noqa: E402
from smartpanel.uiformat import home_path  # noqa: E402

log = logging.getLogger(__name__)

GROUP_ORDER = ("Top bar", "Swap alert", "Spending", "Sessions", "Desktop", "System",
               "Appearance")
TAGS = {"bar.strip": "A6", "bar.graphs": "A4", "bar.text": "A1", "bar.ticker": "A5",
        "hud.enabled": "B6", "palette.shortcut": "B5"}
SLIDER_RANGE = {"swap.warn": (20, 95), "swap.crit": (25, 99)}
# Keys shown in another unit than they're stored in: key -> (unit, factor).
DISPLAY_UNIT = {"sessions.min_age_s": ("min", 60)}
NUMBER_MAX = {"swap.repeat_s": 86_400, "sessions.min_age_s": 86_400}
CORNER_LABELS = {"top-right": "Top right", "top-left": "Top left",
                 "bottom-right": "Bottom right", "bottom-left": "Bottom left"}
STYLE_LABELS = {"system": "Follow system", "light": "Light", "dark": "Dark"}
DEBOUNCE_MS = 300
PREVIEW_SECTIONS = {"system", "sessions", "spend", "app"}

ERR_FOLDER = "That folder doesn’t exist"
ERR_MONEY = "Enter an amount in dollars, or leave it empty to turn the budget off"
ERR_PATH = "Enter an absolute path, like /home"
ERR_EMPTY = "This can’t be empty"


def _markup(text: str) -> str:
    return GLib.markup_escape_text(text)


def _title(key: str, spec: Spec) -> str:
    tag = TAGS.get(key)
    t = _markup(spec.label)
    if tag:
        t += f"  <span font_family=\"monospace\" size=\"small\" alpha=\"65%\">{tag}</span>"
    return t


def format_money(v: Any) -> str:
    if v is None:
        return ""
    v = float(v)
    return f"{v:.0f}" if v == int(v) else f"{v:.2f}"


def parse_money(text: str) -> tuple[bool, float | None]:
    """(ok, value): '' -> (True, None), '25' / '$25.50' -> (True, 25.5)."""
    t = (text or "").strip().lstrip("$").strip().replace(",", ".")
    if not t:
        return True, None
    try:
        v = float(t)
    except ValueError:
        return False, None
    if v != v or v in (float("inf"), float("-inf")) or v < 0:
        return False, None
    return True, (None if v == 0 else round(v, 2))


# ───────────────────────── controls ─────────────────────────

class Control:
    """One row bound to one key. Subclasses build `row` and implement `_show`."""

    def __init__(self, page: "SettingsPage", key: str):
        self.page, self.key, self.spec = page, key, SPECS[key]
        self.hint = self.spec.hint
        self.row: Adw.PreferencesRow
        self._syncing = False
        self._timer = 0
        self._error: str | None = None
        self._env: str | None = None

    # values in, values out
    def sync(self, value: Any, env: str | None) -> None:
        if self._timer or self.editing():
            return              # don't fight the user's hand
        self._syncing = True
        try:
            self._show(value)
        finally:
            self._syncing = False
        self.set_override(env)

    def _show(self, value: Any) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def editing(self) -> bool:
        return False

    def commit(self, value: Any) -> None:
        if self._syncing or self._env:
            return
        self.page.set_setting(self.key, value)

    def commit_later(self, fn: Callable[[], Any]) -> None:
        if self._syncing:
            return
        if self._timer:
            GLib.source_remove(self._timer)

        def fire():
            self._timer = 0
            self.commit(fn())
            return GLib.SOURCE_REMOVE
        self._timer = GLib.timeout_add(DEBOUNCE_MS, fire)
        self.page.pending[self.key] = self

    def flush(self) -> None:
        """Run a pending debounced commit now (tests, page teardown)."""
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
            self.commit(self.pending_value())
        self.page.pending.pop(self.key, None)

    def pending_value(self) -> Any:  # pragma: no cover - overridden where debounced
        return None

    # subtitle: hint / "Set by ENV" / inline error
    def set_override(self, env: str | None) -> None:
        self._env = env
        self.row.set_sensitive(env is None)
        self._paint_subtitle()

    def set_error(self, message: str | None) -> None:
        self._error = message
        self._paint_subtitle()

    @property
    def error(self) -> str | None:
        return self._error

    def _paint_subtitle(self) -> None:
        if not hasattr(self.row, "set_subtitle"):
            return
        if self._env:
            text = f"Set by {self._env}"
        elif self._error:
            text = f"⚠ {self._error}"
        else:
            text = self.hint
        if self._error and not self._env:
            self.row.add_css_class("sp-sp-row-error")
        else:
            self.row.remove_css_class("sp-sp-row-error")
        if self.row.get_subtitle() != _markup(text):
            self.row.set_subtitle(_markup(text))


class SwitchControl(Control):
    def __init__(self, page, key):
        super().__init__(page, key)
        self.row = Adw.SwitchRow(title=_title(key, self.spec), subtitle=_markup(self.hint))
        self.row.connect("notify::active", lambda r, _p: self.commit(r.get_active()))

    def _show(self, value):
        if self.row.get_active() != bool(value):
            self.row.set_active(bool(value))


class ScaleControl(Control):
    """Warning / Critical sliders with a right-aligned `60%` readout."""

    def __init__(self, page, key):
        super().__init__(page, key)
        lo, hi = SLIDER_RANGE.get(key, (self.spec.min or 0, self.spec.max or 100))
        self.row = Adw.ActionRow(title=_title(key, self.spec), subtitle=_markup(self.hint))
        self.adj = Gtk.Adjustment(lower=lo, upper=hi, step_increment=1, page_increment=5)
        self.scale = Gtk.Scale(adjustment=self.adj, digits=0, draw_value=False,
                               valign=Gtk.Align.CENTER, width_request=160)
        self.scale.update_property([Gtk.AccessibleProperty.LABEL], [self.spec.label])
        self.readout = Gtk.Label(xalign=1, width_chars=4)
        self.readout.add_css_class("sp-sp-value")
        self.row.add_suffix(self.scale)
        self.row.add_suffix(self.readout)
        self.adj.connect("value-changed", self._changed)

    def value(self) -> int:
        return int(round(self.adj.get_value()))

    def _show(self, value):
        v = float(value)
        if v < self.adj.get_lower():
            self.adj.set_lower(v)
        if v > self.adj.get_upper():
            self.adj.set_upper(v)
        if self.value() != round(v):
            self.adj.set_value(v)
        self._readout()

    def _readout(self):
        self.readout.set_text(f"{self.value()}%")

    def set_local(self, v: float) -> None:
        """Move the slider without committing (the config does the same push)."""
        was, self._syncing = self._syncing, True
        try:
            if v > self.adj.get_upper():
                self.adj.set_upper(v)
            self.adj.set_value(v)
            self._readout()
        finally:
            self._syncing = was

    def _changed(self, *_):
        self._readout()
        if self._syncing:
            return
        other = self.page.controls.get("swap.crit" if self.key == "swap.warn" else "swap.warn")
        if isinstance(other, ScaleControl):
            if self.key == "swap.warn" and self.value() > other.value():
                other.set_local(self.value())        # SP-2404: Critical is pushed up
            elif self.key == "swap.crit" and self.value() < other.value():
                self.set_local(other.value())        # …and can't go below Warning
        self.commit_later(self.value)

    def pending_value(self):
        return self.value()


class NumberControl(Control):
    """SpinButton + unit. `sessions.min_age_s` shows minutes and stores seconds."""

    def __init__(self, page, key):
        super().__init__(page, key)
        unit, self.factor = DISPLAY_UNIT.get(key, (self.spec.unit, 1))
        lo = (self.spec.min or 0) / self.factor
        hi = (self.spec.max if self.spec.max is not None
              else NUMBER_MAX.get(key, 9_999)) / self.factor
        self.row = Adw.ActionRow(title=_title(key, self.spec), subtitle=_markup(self.hint))
        self.adj = Gtk.Adjustment(lower=lo, upper=hi, step_increment=1, page_increment=10)
        self.spin = Gtk.SpinButton(adjustment=self.adj, digits=0, numeric=True,
                                   valign=Gtk.Align.CENTER, width_chars=4, xalign=1)
        self.spin.add_css_class("sp-sp-value")
        self.spin.update_property([Gtk.AccessibleProperty.LABEL],
                                  [f"{self.spec.label} ({unit})" if unit else self.spec.label])
        self.row.add_suffix(self.spin)
        if unit:
            u = Gtk.Label(label=unit, width_chars=3, xalign=0)
            u.add_css_class("sp-sp-unit")
            self.row.add_suffix(u)
        self.adj.connect("value-changed", lambda *_: self.commit_later(self.pending_value))

    def pending_value(self):
        return int(round(self.adj.get_value() * self.factor))

    def _show(self, value):
        v = float(value) / self.factor
        if v > self.adj.get_upper():
            self.adj.set_upper(v)
        if self.adj.get_value() != round(v):
            self.adj.set_value(round(v))

    def editing(self):
        return self.spin.has_focus() or bool(self.spin.get_focus_child())


class EntryControl(Control):
    """Text entry, validated inline on Enter / focus-out; optional folder button."""

    def __init__(self, page, key, *, width: int = 16, prefix: str = "",
                 placeholder: str = "", folder: bool = False):
        super().__init__(page, key)
        self.row = Adw.ActionRow(title=_title(key, self.spec), subtitle=_markup(self.hint))
        if prefix:
            p = Gtk.Label(label=prefix)
            p.add_css_class("sp-sp-unit")
            self.row.add_suffix(p)
        self.entry = Gtk.Entry(width_chars=width, valign=Gtk.Align.CENTER,
                               placeholder_text=placeholder)
        self.entry.update_property([Gtk.AccessibleProperty.LABEL], [self.spec.label])
        self.row.add_suffix(self.entry)
        self.button = None
        if folder:
            self.button = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER)
            self.button.add_css_class("flat")
            self.button.set_tooltip_text("Choose a folder")
            self.button.update_property([Gtk.AccessibleProperty.LABEL], ["Choose a folder"])
            self.button.connect("clicked", lambda *_: self.page.choose_folder(self))
            self.row.add_suffix(self.button)
        self.entry.connect("activate", lambda *_: self.submit())
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: self.submit())
        self.entry.add_controller(focus)
        self._shown = ""

    def editing(self):
        return bool(self.entry.get_focus_child()) or self.entry.has_focus()

    def set_error(self, message):
        super().set_error(message)
        if message and not self._env:
            self.entry.add_css_class("error")
        else:
            self.entry.remove_css_class("error")

    def text_for(self, value) -> str:
        return "" if value is None else str(value)

    def _show(self, value):
        text = self.text_for(value)
        if self._error is not None and text == self._shown:
            # The stored value didn't change: keep the user's rejected text and
            # its inline error instead of silently reverting them on an
            # unrelated settings update.
            return
        self._shown = text
        if self.entry.get_text() != text:
            self.entry.set_text(text)
        self.set_error(None)

    def validate(self, text: str) -> tuple[str | None, Any]:
        """(error, value-to-save)."""
        t = text.strip()
        if not t:
            return ERR_EMPTY, None
        if self.key == "sessions.dir":
            return (None, t) if os.path.isdir(os.path.expanduser(t)) else (ERR_FOLDER, None)
        if self.key == "disk.mount":
            if not t.startswith("/"):
                return ERR_PATH, None
            return (None, t) if os.path.isdir(t) else (ERR_FOLDER, None)
        return None, t

    def submit(self, text: str | None = None) -> bool:
        if self._env:
            return False
        if text is None:
            text = self.entry.get_text()
        elif self.entry.get_text() != text:
            self.entry.set_text(text)
        if text == self._shown and self._error is None:
            return False
        err, value = self.validate(text)
        self.set_error(err)
        if err:
            return False
        self._shown = text
        self.commit(value)
        return True


class MoneyControl(EntryControl):
    def __init__(self, page, key):
        super().__init__(page, key, width=7, prefix="$", placeholder="Off")
        self.entry.set_alignment(1)
        self.entry.set_input_purpose(Gtk.InputPurpose.NUMBER)
        self.entry.add_css_class("sp-sp-value")

    def text_for(self, value):
        return format_money(value)

    def validate(self, text):
        ok, v = parse_money(text)
        return (None, v) if ok else (ERR_MONEY, None)


class FolderControl(EntryControl):
    def __init__(self, page, key):
        super().__init__(page, key, width=18, folder=True)

    def text_for(self, value):
        return "" if value is None else home_path(os.path.expanduser(str(value)))


class ChoiceControl(Control):
    def __init__(self, page, key, labels: dict[str, str]):
        super().__init__(page, key)
        self.choices = list(self.spec.choices)
        self.row = Adw.ComboRow(title=_title(key, self.spec), subtitle=_markup(self.hint))
        self.row.set_model(Gtk.StringList.new([labels.get(c, c) for c in self.choices]))
        self.row.connect("notify::selected", self._changed)

    def _changed(self, *_):
        i = self.row.get_selected()
        if 0 <= i < len(self.choices):
            self.commit(self.choices[i])

    def _show(self, value):
        i = self.choices.index(value) if value in self.choices else 0
        if self.row.get_selected() != i:
            self.row.set_selected(i)


class StyleControl(Control):
    """Follow system / Light / Dark, as a linked toggle group."""

    def __init__(self, page, key):
        super().__init__(page, key)
        self.row = Adw.ActionRow(title=_title(key, self.spec), subtitle=_markup(self.hint))
        box = Gtk.Box(valign=Gtk.Align.CENTER)
        box.add_css_class("linked")
        self.buttons: dict[str, Gtk.ToggleButton] = {}
        first = None
        for style in STYLES:
            b = Gtk.ToggleButton(label=STYLE_LABELS[style])
            if first is None:
                first = b
            else:
                b.set_group(first)
            b.connect("toggled", self._toggled, style)
            box.append(b)
            self.buttons[style] = b
        self.row.add_suffix(box)

    def _toggled(self, button, style):
        if button.get_active():
            self.commit(style)

    def _show(self, value):
        b = self.buttons.get(value) or self.buttons["system"]
        if not b.get_active():
            b.set_active(True)


class ShortcutControl(Control):
    """Shows the palette shortcut; **Add shortcut** writes it into GNOME (SP-2204)."""

    def __init__(self, page, key):
        super().__init__(page, key)
        self.row = Adw.ActionRow(title=_title(key, self.spec), subtitle=_markup(self.hint))
        self.code = Gtk.Label(valign=Gtk.Align.CENTER)
        self.code.add_css_class("sp-sp-code")
        self.button = Gtk.Button(label="Add shortcut", valign=Gtk.Align.CENTER)
        self.button.connect("clicked", lambda *_: self.page.confirm_shortcut(self))
        self.row.add_suffix(self.code)
        self.row.add_suffix(self.button)
        self.binding = DEFAULTS[key]

    def _show(self, value):
        self.binding = str(value or DEFAULTS[self.key])
        self.refresh_state()

    def refresh_state(self):
        backend = self.page.shortcut_backend
        existing = shortcut.find_existing(backend)
        binding = existing.get("binding") if existing else self.binding
        label = shortcut.accel_label(binding) or binding
        self.code.set_text(label)
        if existing:
            self.button.set_label("Shortcut added")
            self.button.set_sensitive(False)
            self.button.set_tooltip_text("It’s in GNOME Settings → Keyboard → Custom Shortcuts")
        elif not backend.available():
            self.button.set_label("Add shortcut")
            self.button.set_sensitive(False)
            self.button.set_tooltip_text("GNOME keyboard settings aren’t available here")
        else:
            self.button.set_label("Add shortcut")
            self.button.set_sensitive(True)
            self.button.set_tooltip_text(f"Make {label} open the command palette")

    def set_override(self, env):
        # The shortcut row itself is informational; only the button can be off.
        self._env = env
        self._paint_subtitle()


# ───────────────────────── page ─────────────────────────

def _control_for(page: "SettingsPage", key: str) -> Control:
    spec = SPECS[key]
    if key in SLIDER_RANGE:
        return ScaleControl(page, key)
    if key == "palette.shortcut":
        return ShortcutControl(page, key)
    if key == "appearance.style":
        return StyleControl(page, key)
    if key == "hud.corner":
        return ChoiceControl(page, key, CORNER_LABELS)
    if key == "sessions.dir":
        return FolderControl(page, key)
    match spec.kind:
        case "bool":
            return SwitchControl(page, key)
        case "int" | "num":
            return NumberControl(page, key)
        case "money":
            return MoneyControl(page, key)
        case "choice":
            return ChoiceControl(page, key, {})
    return EntryControl(page, key)


class SettingsPage(Adw.PreferencesPage):
    def __init__(self, store, actions, config, *, shortcut_backend=None):
        tokens.install()
        css.install()
        super().__init__(title="Settings")
        self.add_css_class("sp-sp-settings")
        self.store, self.actions, self.config = store, actions, config
        if shortcut_backend is None:
            shortcut_backend = (shortcut.MemoryBackend() if store.state.app.demo
                                else shortcut.GioBackend())
        self.shortcut_backend = shortcut_backend
        self.controls: dict[str, Control] = {}
        self.pending: dict[str, Control] = {}
        self.groups: dict[str, Adw.PreferencesGroup] = {}

        by_group: dict[str, list[str]] = {}
        for key, spec in SPECS.items():
            by_group.setdefault(spec.group, []).append(key)
        order = list(GROUP_ORDER) + [g for g in by_group if g not in GROUP_ORDER]
        for name in order:
            keys = by_group.get(name)
            if not keys:
                continue
            group = Adw.PreferencesGroup(title=name)
            self.groups[name] = group
            if name == "Top bar":
                self.preview = BarPreview()
                prow = Gtk.ListBoxRow(activatable=False, selectable=False)
                prow.set_child(self.preview)
                self.preview.set_margin_top(10)
                self.preview.set_margin_bottom(10)
                self.preview.set_margin_start(12)
                self.preview.set_margin_end(12)
                group.add(prow)
            for key in keys:
                try:
                    control = _control_for(self, key)
                except Exception:  # noqa: BLE001 — one bad row must not lose the page
                    log.exception("settings row %s failed", key)
                    continue
                self.controls[key] = control
                group.add(control.row)
            self.add(group)

        self._unsubscribe = store.subscribe(self._on_store_changed)
        self.connect("map", lambda *_: self.refresh_preview())
        self.connect("destroy", self._on_destroy)
        self.sync_all()
        self.refresh_preview()

    # ── values ──
    def value(self, key: str) -> Any:
        values = self.store.state.settings.values or {}
        if key in values:
            return values[key]
        if self.config is not None:
            try:
                v = self.config.get(key)
                if v is not None:
                    return v
            except Exception:  # noqa: BLE001
                pass
        return DEFAULTS.get(key)

    def overridden_by(self, key: str) -> str | None:
        env = (self.store.state.settings.overridden_by or {}).get(key)
        if env:
            return env
        getter = getattr(self.config, "overridden_by", None)
        if callable(getter):
            try:
                return getter(key)
            except Exception:  # noqa: BLE001
                return None
        return None

    def settings_view(self) -> dict[str, Any]:
        return {k: self.value(k) for k in DEFAULTS}

    def set_setting(self, key: str, value: Any) -> None:
        try:
            self.actions.set_setting(key, value)
        except Exception:  # noqa: BLE001 — Actions should toast, never raise; be safe
            log.exception("set_setting(%s) failed", key)
        self.refresh_preview()

    # ── live updates ──
    def sync_all(self) -> None:
        for key, control in self.controls.items():
            try:
                control.sync(self.value(key), self.overridden_by(key))
            except Exception:  # noqa: BLE001
                log.exception("settings row %s failed to sync", key)

    def _on_store_changed(self, sections: set[str]) -> None:
        if "settings" in sections:
            self.sync_all()
            self.refresh_preview()
        elif sections & PREVIEW_SECTIONS and self.get_mapped():
            self.refresh_preview()

    def refresh_preview(self) -> None:
        preview = getattr(self, "preview", None)
        if preview is not None:
            preview.refresh(self.store.state, self.settings_view())

    def flush(self) -> None:
        for control in list(self.pending.values()):
            control.flush()

    # ── folder chooser ──
    def choose_folder(self, control: EntryControl) -> None:
        dialog = Gtk.FileDialog(title="New sessions start in", modal=True)
        start = os.path.expanduser(control.entry.get_text().strip() or "~")
        if os.path.isdir(start):
            from gi.repository import Gio
            dialog.set_initial_folder(Gio.File.new_for_path(start))

        def done(dlg, result):
            try:
                folder = dlg.select_folder_finish(result)
            except GLib.Error:
                return              # cancelled
            if folder is not None and folder.get_path():
                text = home_path(folder.get_path())
                control.entry.set_text(text)
                control.submit(text)
        dialog.select_folder(self.get_root() if isinstance(self.get_root(), Gtk.Window)
                             else None, None, done)

    # ── palette shortcut (SP-2204) ──
    def confirm_shortcut(self, control: ShortcutControl) -> Adw.AlertDialog:
        label = shortcut.accel_label(control.binding) or control.binding
        dialog = Adw.AlertDialog(
            heading="Add the palette shortcut?",
            body=(f"smartPanel adds a GNOME custom shortcut, {label}, that runs "
                  f"“{shortcut.PALETTE_COMMAND}”. You can change or remove it in "
                  "GNOME Settings → Keyboard → Custom Shortcuts."))
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add shortcut")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, r: r == "add" and self.add_shortcut(control))
        dialog.present(self)
        return dialog

    def add_shortcut(self, control: ShortcutControl) -> bool:
        label = shortcut.accel_label(control.binding) or control.binding
        try:
            shortcut.add_palette_shortcut(self.shortcut_backend, control.binding)
        except Exception as e:  # noqa: BLE001
            self.store.toast(f"Couldn’t add the shortcut: {e}", error=True)
            control.refresh_state()
            return False
        self.store.toast(f"Shortcut added: {label} opens the command palette")
        control.refresh_state()
        return True

    def _on_destroy(self, *_):
        self.flush()
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None


def build(store, actions, config, *, shortcut_backend=None):
    return SettingsPage(store, actions, config, shortcut_backend=shortcut_backend)
