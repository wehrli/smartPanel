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
    assert b.read(path) == {"name": "smartPanel command palette",
                            "command": "smartpanel --palette", "binding": "<Ctrl><Alt>space"}
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
