"""scripts/screenshots.sh and scripts/bar_preview.py (SP-2701), without rendering
the whole suite: --list for the matrix, one bar preview for the cairo path."""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "screenshots.sh"


def _list(*args: str) -> list[str]:
    out = subprocess.run(["bash", str(SCRIPT), "--list", "--out", "/x", *args],
                         capture_output=True, text=True, timeout=30, check=True)
    return out.stdout.splitlines()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_full_matrix_is_scenarios_by_pages_by_styles():
    from smartpanel import demo
    rows = _list()
    assert len(rows) == len(demo.SCENARIOS) * 9 * 2
    assert rows[0] == "/x/busy-overview-light.png  (busy overview light 900x680)"
    assert "/x/many-sessions-board-dark.png  (many sessions:board dark 900x680)" in rows


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_filters_and_size():
    rows = _list("--scenarios", "alert", "--pages", "system journal:ok",
                 "--styles", "dark", "--size", "1100x760")
    assert rows == ["/x/alert-system-dark.png  (alert system dark 1100x760)",
                    "/x/alert-journal-ok-dark.png  (alert journal:ok dark 1100x760)"]


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_readme_set():
    rows = _list("--readme")
    assert len(rows) == 6
    assert all(r.startswith("/x/") for r in rows)


def test_bar_preview_renders_a_png(tmp_path):
    out = tmp_path / "bar.png"
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    subprocess.run([sys.executable, str(ROOT / "scripts" / "bar_preview.py"),
                    "--out", str(out), "--scenarios", "busy", "cold", "--ticker"],
                   env=env, check=True, capture_output=True, timeout=60)
    head = out.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", head[16:24])
    assert h == 4 * 32 * 2                      # 4 rows, 32 px panel, 2x
    assert w > 400
