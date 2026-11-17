"""GNOME custom keybinding writer (SP-2204), against the in-memory backend only."""
import pytest

pytest.importorskip("gi")

from smartpanel.ui.widgets import settings_palette_shortcut as ks  # noqa: E402

BASE = ks.BASE_PATH


def test_adds_first_free_slot_and_appends_to_list():
    b = ks.MemoryBackend({f"{BASE}custom0/": {"name": "Term", "command": "terminator",
                                               "binding": "<Super>t"}})
    path = ks.add_palette_shortcut(b, "<Ctrl><Alt>space")
    assert path == f"{BASE}custom1/"
    assert b.paths() == [f"{BASE}custom0/", f"{BASE}custom1/"]
    entry = b.read(path)
    assert entry["name"] == "smartPanel command palette"
    assert entry["binding"] == "<Ctrl><Alt>space"
    assert ks.is_palette_command(entry["command"])
    assert ks.find_existing(b)["path"] == path


def test_rewrites_existing_instead_of_duplicating():
    b = ks.MemoryBackend()
    p1 = ks.add_palette_shortcut(b, "<Ctrl><Alt>space")
    p2 = ks.add_palette_shortcut(b, "<Super>k")
    assert p1 == p2 and b.paths() == [p1]
    assert b.read(p1)["binding"] == "<Super>k"


def test_unavailable_backend():
    b = ks.MemoryBackend(available=False)
    assert ks.find_existing(b) is None
    with pytest.raises(RuntimeError):
        ks.add_palette_shortcut(b, "<Ctrl><Alt>space")


def test_find_existing_survives_a_broken_backend():
    class Broken(ks.MemoryBackend):
        def paths(self):
            raise OSError("dconf gone")
    assert ks.find_existing(Broken()) is None


def test_free_path_skips_taken():
    assert ks.free_path([]) == f"{BASE}custom0/"
    assert ks.free_path([f"{BASE}custom0/", f"{BASE}custom2/"]) == f"{BASE}custom1/"


def test_accel_label():
    assert ks.accel_label("<Ctrl><Alt>space") == "Ctrl+Alt+Space"
    assert ks.accel_label("<Primary><Shift>k") == "Ctrl+Shift+K"
    assert ks.accel_label("<Super>Return") == "Super+Return"
    assert ks.accel_label("") == ""


def test_written_command_is_absolute_and_runnable(monkeypatch):
    import os
    import shlex
    import sys
    monkeypatch.setattr(ks.shutil, "which", lambda name: None)
    b = ks.MemoryBackend()
    path = ks.add_palette_shortcut(b, "<Ctrl><Alt>space")
    argv = shlex.split(b.read(path)["command"])
    assert argv[0] == "env"
    assert argv[1].startswith("PYTHONPATH=") and os.path.isabs(argv[1].split("=", 1)[1])
    assert os.path.isdir(os.path.join(argv[1].split("=", 1)[1], "smartpanel"))
    assert argv[2] == os.path.abspath(sys.executable) and os.path.isabs(argv[2])
    assert argv[3:] == ["-m", "smartpanel", "--palette"]


def test_prefers_console_script_when_on_path():
    cmd = ks.palette_command(which=lambda name: "/opt/my apps/smartpanel")
    assert cmd == "'/opt/my apps/smartpanel' --palette"


def test_old_bare_command_is_found_and_upgraded(monkeypatch):
    monkeypatch.setattr(ks.shutil, "which", lambda name: None)
    b = ks.MemoryBackend({f"{BASE}custom0/": {"name": "x", "command": "smartpanel --palette",
                                               "binding": "<Super>p"}})
    assert ks.find_existing(b)["path"] == f"{BASE}custom0/"
    path = ks.add_palette_shortcut(b, "<Ctrl><Alt>space")
    assert path == f"{BASE}custom0/" and b.paths() == [path]
    assert b.read(path)["command"].startswith("env ")


def test_is_palette_command():
    assert ks.is_palette_command("smartpanel --palette")
    assert ks.is_palette_command("env PYTHONPATH=/x /usr/bin/python3 -m smartpanel --palette")
    assert not ks.is_palette_command("smartpanel --hud")
    assert not ks.is_palette_command("terminator --palette")
