"""Isolate the suite from the developer's shell.

The README and CLAUDE.md invite users to set `SMARTPANEL_*` knobs (journal
skill, focus re-attach, ...). Several modules read them once, at import time
(`journal.SKILL_COMMAND`, `focus.REATTACH_ENABLED`, ...), so they are stripped
here, when pytest loads this file and before any test module imports
smartpanel. The autouse fixture repeats it per test for code that reads the
environment lazily, and points the XDG dirs at a temp dir so no test can touch
the real ~/.config/smartpanel. A test that needs a knob sets it explicitly.
"""
from __future__ import annotations

import os

import pytest

_PREFIX = "SMARTPANEL_"
# Knobs of the test suite itself, not of the app: kept.
_TEST_KNOBS = frozenset({"SMARTPANEL_REGEN_FIXTURES"})


def _app_knobs() -> list[str]:
    return [k for k in os.environ if k.startswith(_PREFIX) and k not in _TEST_KNOBS]


for _k in _app_knobs():
    del os.environ[_k]


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path_factory):
    for k in _app_knobs():
        monkeypatch.delenv(k)
    xdg = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg / "state"))
    yield
