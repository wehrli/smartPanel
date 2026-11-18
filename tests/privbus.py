"""A private dbus-daemon for tests, without Gio.TestDBus.

``Gio.TestDBus.down()`` (and its GObject finalizer, which calls ``down()`` again)
waits up to 30 s for the process-wide session-bus singleton to be finalized. GTK test
modules call ``Gtk.init_check()`` at import, during collection, and GTK holds that
singleton for the life of the process, so every TestDBus teardown in a full run paid
the whole 30 s. A plain ``dbus-daemon`` subprocess has no such wait: the tests connect
by address and never touch the session-bus singleton.
"""
from __future__ import annotations

import contextlib
import shutil
import subprocess
from typing import Iterator

AVAILABLE = shutil.which("dbus-daemon") is not None


@contextlib.contextmanager
def private_bus(timeout: float = 5.0) -> Iterator[str]:
    """Start a private session-type dbus-daemon; yield its address; always stop it."""
    proc = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--print-address"],
        stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True)
    try:
        address = (proc.stdout.readline() if proc.stdout else "").strip()
        if not address:
            raise RuntimeError("dbus-daemon printed no address")
        yield address
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            proc.kill()
            proc.wait(timeout=timeout)
        if proc.stdout:
            proc.stdout.close()
