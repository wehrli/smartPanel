"""Suite-wide display policy for the GTK tests.

The GTK test files skip with "needs GTK 4 and a display" when `Gtk.init_check()`
fails. Left alone that is silent: over SSH or in CI ~115 UI tests skip and the
run still reads green. Two knobs make the requirement explicit:

* ``SMARTPANEL_TEST_DISPLAY=broadway`` runs every GTK test against a private
  ``gtk4-broadwayd`` (own ``XDG_RUNTIME_DIR``, own session bus), so nothing
  touches the developer's compositor or session bus. ``auto`` (the default) does
  the same when neither ``DISPLAY`` nor ``WAYLAND_DISPLAY`` is set and
  ``gtk4-broadwayd`` is installed; ``host`` keeps the inherited display.
* ``SMARTPANEL_REQUIRE_GTK_TESTS=1`` turns those skips into a usage error.

The display has to exist before the test modules import Gtk, which happens at
collection, so it is started in ``pytest_configure``, not in a fixture.

Timezone: an autouse fixture re-runs `time.tzset()` after every test, so a zone
one test set never leaks into the next.

Environment (added when the sessions fixes landed): the README invites users to
set `SMARTPANEL_*` knobs, and several modules read them once at import time
(`journal.SKILL_COMMAND`, `focus.REATTACH_ENABLED`, ...). They are stripped when
pytest loads this file, before any test module imports smartpanel, and again per
test; the XDG config/state dirs point at a temp dir. The suite's own knobs
(`_TEST_KNOBS`) are kept.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time

import pytest

GTK_SKIP_REASON = "needs GTK 4 and a display"

_procs: list[subprocess.Popen] = []
_tmpdirs: list[tempfile.TemporaryDirectory] = []
_saved_env: dict[str, str | None] = {}


def _want_broadway() -> bool:
    mode = os.environ.get("SMARTPANEL_TEST_DISPLAY", "auto").strip().lower()
    if mode == "host" or not shutil.which("gtk4-broadwayd"):
        return False
    if mode == "broadway":
        return True
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _setenv(key: str, value: str | None) -> None:
    _saved_env.setdefault(key, os.environ.get(key))
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


def _start_broadway() -> bool:
    tmp = tempfile.TemporaryDirectory(prefix="sp-broadway-")
    _tmpdirs.append(tmp)
    os.chmod(tmp.name, 0o700)
    display = f":{40 + os.getpid() % 50}"
    env = dict(os.environ, XDG_RUNTIME_DIR=tmp.name)
    try:
        broadway = subprocess.Popen(["gtk4-broadwayd", display], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    _procs.append(broadway)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if broadway.poll() is not None:
            return False
        if any(n.startswith("broadway") for n in os.listdir(tmp.name)):
            break
        time.sleep(0.05)
    else:
        return False
    # A private session bus too: GTK otherwise reaches the user's real one.
    bus_addr = None
    if shutil.which("dbus-daemon"):
        try:
            bus = subprocess.Popen(["dbus-daemon", "--session", "--nofork",
                                    "--print-address=1"], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            _procs.append(bus)
            bus_addr = (bus.stdout.readline() or "").strip() or None
        except OSError:
            bus_addr = None
    _setenv("XDG_RUNTIME_DIR", tmp.name)
    _setenv("GDK_BACKEND", "broadway")
    _setenv("BROADWAY_DISPLAY", display)
    _setenv("DISPLAY", None)
    _setenv("WAYLAND_DISPLAY", None)
    if bus_addr:
        _setenv("DBUS_SESSION_BUS_ADDRESS", bus_addr)
    return True


def pytest_configure(config):
    if _want_broadway() and not _start_broadway():
        _stop()


def _stop() -> None:
    for p in reversed(_procs):
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        if p.stdout:
            p.stdout.close()
    _procs.clear()
    for key, value in _saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    _saved_env.clear()
    for t in _tmpdirs:
        t.cleanup()
    _tmpdirs.clear()


def pytest_unconfigure(config):
    _stop()


def _gtk_skipped(item) -> bool:
    for mark in item.iter_markers("skipif"):
        if GTK_SKIP_REASON in str(mark.kwargs.get("reason", "")) and \
                any(bool(c) for c in mark.args):
            return True
    return False


def pytest_collection_modifyitems(config, items):
    if os.environ.get("SMARTPANEL_REQUIRE_GTK_TESTS", "").strip() not in ("1", "true", "yes"):
        return
    skipped = [i.nodeid for i in items if _gtk_skipped(i)]
    if skipped:
        raise pytest.UsageError(
            f"SMARTPANEL_REQUIRE_GTK_TESTS is set but {len(skipped)} GTK tests would skip "
            f"({GTK_SKIP_REASON}); first: {skipped[0]}. Install gtk4-broadwayd or run "
            f"with a display.")


_PREFIX = "SMARTPANEL_"
# Knobs of the test suite itself, not of the app: kept.
_TEST_KNOBS = frozenset({"SMARTPANEL_REGEN_FIXTURES", "SMARTPANEL_TEST_DISPLAY",
                         "SMARTPANEL_REQUIRE_GTK_TESTS", "SMARTPANEL_HUD_TEST_TMP"})


def _app_knobs() -> list[str]:
    return [k for k in os.environ if k.startswith(_PREFIX) and k not in _TEST_KNOBS]


for _k in _app_knobs():
    del os.environ[_k]


@pytest.fixture(autouse=True)
def _reload_timezone_after_each_test():
    """Re-read TZ after every test (monkeypatch restores TZ but can't call tzset).

    Set up before monkeypatch, so it tears down after it and sees the restored value.
    """
    yield
    time.tzset()


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path_factory):
    for k in _app_knobs():
        monkeypatch.delenv(k)
    xdg = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg / "state"))
    yield

