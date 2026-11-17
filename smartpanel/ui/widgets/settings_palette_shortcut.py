"""GNOME custom keybinding for the command palette (SP-2204).

Apps can't grab global keys on Wayland, so the palette shortcut is a GNOME *custom
shortcut* that runs `smartpanel --palette`
(as an absolute command, see `palette_command`). Settings → Desktop offers **Add shortcut**,
which (after asking) writes one here:

    org.gnome.settings-daemon.plugins.media-keys  custom-keybindings = [..., <path>]
    org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:<path>
        name / command / binding

Everything goes through a small backend so tests and demo runs use `MemoryBackend`
and never touch the user's real dconf. `GioBackend` is the only thing that writes,
and only when the user confirmed the dialog in a non-demo run.

GTK-free; Gio is imported lazily.
"""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import sys
from typing import Protocol

log = logging.getLogger(__name__)

MEDIA_KEYS_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
BASE_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
PALETTE_ARG = "--palette"
PALETTE_NAME = "smartPanel command palette"


def palette_command(*, which=shutil.which, python: str | None = None,
                    package_dir: str | None = None) -> str:
    """The command the keybinding runs, as an absolute path that works from GNOME.

    The supported install (scripts/install.sh + the systemd unit running
    `python -m smartpanel` from the repo) puts no `smartpanel` on PATH, so a bare
    `smartpanel --palette` would silently do nothing. Prefer a real console
    script when one resolves; otherwise run this interpreter on this checkout.
    """
    exe = which("smartpanel")
    if exe:
        return f"{shlex.quote(os.path.abspath(exe))} {PALETTE_ARG}"
    python = python or sys.executable
    if package_dir is None:
        package_dir = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
    return " ".join(shlex.quote(p) for p in (
        "env", f"PYTHONPATH={package_dir}", os.path.abspath(python),
        "-m", "smartpanel", PALETTE_ARG))


def is_palette_command(command: str) -> bool:
    """Ours, old (`smartpanel --palette`) or new (absolute / `python -m`) form."""
    command = (command or "").strip()
    return command.endswith(f" {PALETTE_ARG}") and "smartpanel" in command


class Backend(Protocol):
    def available(self) -> bool: ...
    def paths(self) -> list[str]: ...
    def set_paths(self, paths: list[str]) -> None: ...
    def read(self, path: str) -> dict[str, str]: ...
    def write(self, path: str, name: str, command: str, binding: str) -> None: ...


class MemoryBackend:
    """In-process stand-in for dconf (tests, --demo, snapshots)."""

    def __init__(self, entries: dict[str, dict[str, str]] | None = None,
                 available: bool = True):
        self.entries = {p: dict(v) for p, v in (entries or {}).items()}
        self.order = list(self.entries)
        self._available = available

    def available(self) -> bool:
        return self._available

    def paths(self) -> list[str]:
        return list(self.order)

    def set_paths(self, paths: list[str]) -> None:
        self.order = list(paths)

    def read(self, path: str) -> dict[str, str]:
        return dict(self.entries.get(path, {}))

    def write(self, path: str, name: str, command: str, binding: str) -> None:
        self.entries[path] = {"name": name, "command": command, "binding": binding}


class GioBackend:
    """The real thing: Gio.Settings on GNOME's media-keys schemas."""

    def __init__(self):
        self._schemas_ok: bool | None = None

    def available(self) -> bool:
        if self._schemas_ok is None:
            try:
                import gi
                gi.require_version("Gio", "2.0")
                from gi.repository import Gio
                src = Gio.SettingsSchemaSource.get_default()
                self._schemas_ok = bool(src and src.lookup(MEDIA_KEYS_SCHEMA, True)
                                        and src.lookup(CUSTOM_SCHEMA, True))
            except Exception:  # noqa: BLE001
                self._schemas_ok = False
        return self._schemas_ok

    @staticmethod
    def _gio():
        from gi.repository import Gio
        return Gio

    def paths(self) -> list[str]:
        if not self.available():
            return []
        return list(self._gio().Settings.new(MEDIA_KEYS_SCHEMA).get_strv("custom-keybindings"))

    def set_paths(self, paths: list[str]) -> None:
        self._gio().Settings.new(MEDIA_KEYS_SCHEMA).set_strv("custom-keybindings", list(paths))

    def read(self, path: str) -> dict[str, str]:
        s = self._gio().Settings.new_with_path(CUSTOM_SCHEMA, path)
        return {k: s.get_string(k) for k in ("name", "command", "binding")}

    def write(self, path: str, name: str, command: str, binding: str) -> None:
        s = self._gio().Settings.new_with_path(CUSTOM_SCHEMA, path)
        s.set_string("name", name)
        s.set_string("command", command)
        s.set_string("binding", binding)
        self._gio().Settings.sync()


def find_existing(backend: Backend) -> dict | None:
    """Our palette shortcut, if one exists, as {path, name, command, binding}."""
    try:
        if not backend.available():
            return None
        for path in backend.paths():
            entry = backend.read(path)
            if is_palette_command(entry.get("command") or ""):
                return {"path": path, **entry}
    except Exception:  # noqa: BLE001 — reading dconf must never break Settings
        log.debug("reading custom keybindings failed", exc_info=True)
    return None


def free_path(paths: list[str]) -> str:
    taken = set(paths)
    n = 0
    while f"{BASE_PATH}custom{n}/" in taken:
        n += 1
    return f"{BASE_PATH}custom{n}/"


def add_palette_shortcut(backend: Backend, binding: str, *,
                         command: str | None = None, name: str = PALETTE_NAME) -> str:
    """Write (or update) the custom shortcut; returns its dconf path.

    An existing palette shortcut (including the old bare `smartpanel --palette`
    form) is rewritten in place with the current, absolute command.

    Raises RuntimeError when GNOME's media-keys schema isn't installed.
    """
    if not backend.available():
        raise RuntimeError("GNOME keyboard settings aren’t available on this desktop")
    command = command or palette_command()
    existing = find_existing(backend)
    if existing is not None:
        path = existing["path"]
    else:
        paths = backend.paths()
        path = free_path(paths)
        backend.write(path, name, command, binding)
        backend.set_paths(paths + [path])
        return path
    backend.write(path, name, command, binding)
    return path


def accel_label(accel: str) -> str:
    """'<Ctrl><Alt>space' -> 'Ctrl+Alt+Space' (no GTK needed)."""
    import re
    mods = re.findall(r"<([^>]+)>", accel or "")
    key = re.sub(r"<[^>]+>", "", accel or "").strip()
    names = {"ctrl": "Ctrl", "control": "Ctrl", "primary": "Ctrl", "alt": "Alt",
             "shift": "Shift", "super": "Super", "meta": "Meta"}
    parts = [names.get(m.lower(), m) for m in mods]
    if key:
        parts.append(key[:1].upper() + key[1:] if len(key) > 1 else key.upper())
    return "+".join(parts)
