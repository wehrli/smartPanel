"""The v2 spike ADRs (SP-0001..0003) exist, and every image they link is in the repo."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ADR = ROOT / "docs" / "adr"
NAMES = ["0001-dashboard-toolkit.md", "0002-top-bar-graphics.md", "0003-desktop-hud.md"]


@pytest.mark.parametrize("name", NAMES)
def test_adr_has_the_sections(name):
    text = (ADR / name).read_text()
    for heading in ("## Context", "## Options", "## Decision", "## Consequences"):
        assert heading in text, f"{name}: {heading}"


@pytest.mark.parametrize("name", NAMES + ["README.md"])
def test_adr_links_resolve(name):
    text = (ADR / name).read_text()
    for target in re.findall(r"\]\(([^)#]+)\)", text):
        assert (ADR / target).exists(), f"{name} links {target}"


def test_top_bar_adr_shows_all_three_options():
    text = (ADR / "0002-top-bar-graphics.md").read_text()
    for option in ("wide", "pixmap", "unicode"):
        png = ADR / "img" / f"0002-{option}.png"
        assert f"img/0002-{option}.png" in text
        assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_capture_script_is_executable_bash():
    script = ROOT / "scripts" / "capture_bar_options.sh"
    assert os.access(script, os.X_OK)
    subprocess.run(["bash", "-n", str(script)], check=True, timeout=10)
