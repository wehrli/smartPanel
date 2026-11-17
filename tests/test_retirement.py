"""The GTK 3 code is gone and stays gone (architecture v2, "Retired at the end of wave 2").

PyGObject can't load GTK 3 and GTK 4 in one process, so a single stray
`gi.require_version("Gtk", "3.0")` or AyatanaAppIndicator3 import anywhere in the
package would break the app the moment that module is imported.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "smartpanel"

RETIRED = ("indicator", "dashboard", "daemon", "sensors/claude", "sensors/sessions")

GTK3 = re.compile(
    r"""require_version\(\s*['"]Gtk['"]\s*,\s*['"]3\.0['"]"""
    r"""|AyatanaAppIndicator3|AppIndicator3|gir1\.2-gtk-3\.0""")


def _py_files():
    return [p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts]


@pytest.mark.parametrize("name", RETIRED)
def test_retired_module_is_deleted(name):
    assert not (PKG / f"{name}.py").exists()


def test_nothing_imports_a_retired_module():
    pattern = re.compile(
        r"(from\s+smartpanel(\.sensors)?\s+import\s+[^\n]*\b(indicator|dashboard|daemon)\b"
        r"|smartpanel\.(indicator|dashboard|daemon)\b"
        r"|smartpanel\.sensors\.(claude|sessions)\b"
        r"|from\s+smartpanel\.sensors\s+import\s+[^\n]*\b(claude|sessions)\b)")
    hits = []
    for p in [*_py_files(), *(ROOT / "tests").glob("*.py")]:
        if p.name == Path(__file__).name:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not hits, "\n".join(hits)


def test_no_gtk3_anywhere_in_the_package():
    hits = []
    for p in _py_files():
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if GTK3.search(line):
                hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not hits, "\n".join(hits)


def test_installer_and_service_target_gtk4():
    install = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert not GTK3.search(install)
    for pkg in ("python3-gi", "gir1.2-gtk-4.0", "gir1.2-adw-1", "python3-cairo",
                "gir1.2-notify-0.7", "tmux", "terminator"):
        assert pkg in install, pkg
    unit = (ROOT / "scripts" / "smartpanel.service.in").read_text(encoding="utf-8")
    assert "-m smartpanel" in unit


def test_praise_report_needs_no_display_and_no_gtk(tmp_path):
    """`--praise-report` over SSH: no display, no `gi`, an empty transcript dir."""
    probe = (
        "import sys\n"
        "from smartpanel.cli import main\n"
        "rc = main(['--praise-report'])\n"
        "assert rc == 0, rc\n"
        "assert 'gi' not in sys.modules, 'praise report imported gi'\n"
    )
    env = {k: v for k, v in os.environ.items()
           if k not in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS")}
    env["HOME"] = str(tmp_path)          # no ~/.claude: the report must still print
    env["PYTHONPATH"] = str(ROOT)
    out = subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip(), "report printed nothing"
