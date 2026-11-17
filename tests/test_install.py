"""scripts/install.sh (SP-2704), exercised with --dry-run against a throwaway HOME.

The dry run prints every command and every file it would write and changes nothing,
so this never touches the real systemd user instance or the session bus.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "scripts" / "install.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture(scope="module")
def dry_run(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "SMARTPANEL_PYTHON": sys.executable,
           "LANG": os.environ.get("LANG", "C.UTF-8")}
    out = subprocess.run(["bash", str(INSTALL), "--dry-run"], env=env, cwd=home,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return home, out.stdout


def test_dry_run_changes_nothing(dry_run):
    home, _ = dry_run
    # (.cache may appear: the dependency probes import gi, which caches fonts.)
    assert not (home / ".config").exists()
    assert not (home / ".local").exists()


def test_creates_the_settings_dir(dry_run):
    home, out = dry_run
    assert f"+ mkdir -p {home}/.config/smartpanel" in out


def test_unit_runs_the_gtk4_app(dry_run):
    home, out = dry_run
    unit = out.split(f"+ write {home}/.config/systemd/user/smartpanel.service:")[1]
    unit = unit.split("+ ")[0]
    assert f"ExecStart={sys.executable} -m smartpanel\n" in unit
    assert f"WorkingDirectory={ROOT}" in unit
    assert "BusName=dev.omdev.SmartPanel" in unit
    assert "--theme" not in unit.split("ExecStart=")[1].splitlines()[0]
    assert "@" not in "".join(l for l in unit.splitlines() if not l.lstrip().startswith("#"))


def test_dbus_service_file(dry_run):
    home, out = dry_run
    svc = out.split(f"+ write {home}/.local/share/dbus-1/services/dev.omdev.SmartPanel.service:")[1]
    svc = svc.split("+ ")[0]
    assert "Name=dev.omdev.SmartPanel\n" in svc
    assert "SystemdService=smartpanel.service" in svc
    assert "-m smartpanel" in svc


def test_launcher_and_service_commands(dry_run):
    home, out = dry_run
    assert f"+ write {home}/.local/bin/smartpanel:" in out
    assert "+ systemctl --user enable smartpanel.service" in out
    assert "install-hud.sh" in out


def test_launcher_is_written_fresh_not_through_a_symlink(dry_run):
    home, out = dry_run
    rm = out.index(f"+ rm -f {home}/.local/bin/smartpanel")
    assert rm < out.index(f"+ write {home}/.local/bin/smartpanel:")


@pytest.fixture
def launcher(tmp_path):
    """The filled launcher, with a fake gdbus / python that log their argv."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "log"
    for name in ("gdbus", "fakepy"):
        f = bindir / name
        f.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n', encoding="utf-8")
        f.chmod(0o755)
    text = (ROOT / "scripts" / "smartpanel.in").read_text(encoding="utf-8")
    text = text.replace("@REPO@", str(ROOT)).replace("@PYTHON@", str(bindir / "fakepy"))
    script = tmp_path / "smartpanel"
    script.write_text(text, encoding="utf-8")
    script.chmod(0o755)

    def call(*args, env_extra=None):
        if log.exists():
            log.unlink()
        env = {"PATH": f"{bindir}:/usr/bin:/bin", **(env_extra or {})}
        subprocess.run([str(script), *args], env=env, check=True, timeout=30)
        return log.read_text(encoding="utf-8").splitlines()
    return call


@pytest.mark.parametrize("args", [(), ("--dashboard",), ("--dashboard=spend",),
                                  ("--palette",), ("--hud", "toggle"), ("-v",)])
def test_launcher_activates_through_systemd_for_app_calls(launcher, args):
    lines = launcher(*args)
    assert lines[0].startswith("gdbus call --session --dest dev.omdev.SmartPanel")
    assert "org.freedesktop.DBus.Peer.Ping" in lines[0]
    assert lines[1] == " ".join(["fakepy -m smartpanel", *args])


@pytest.mark.parametrize("args", [("--demo", "busy"), ("--snapshot", "overview"),
                                  ("--praise-report",), ("-h",), ("--help",),
                                  ("--theme", "claude-focus"), ("--gallery",)])
def test_launcher_runs_local_only_calls_without_activation(launcher, args):
    assert launcher(*args) == [" ".join(["fakepy -m smartpanel", *args])]


def test_launcher_skips_activation_for_a_dev_app_id(launcher):
    assert launcher(env_extra={"SMARTPANEL_APP_ID": "dev.omdev.SmartPanel.Dev"}) == \
        ["fakepy -m smartpanel"]


def test_launcher_debug_timing_runs_the_timing_entry_point(launcher):
    assert launcher("--debug-timing", "-v") == ["fakepy -m smartpanel.timing --debug-timing -v"]
