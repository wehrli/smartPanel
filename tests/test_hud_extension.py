"""The desktop HUD extension (extension/smartpanel-hud@omdev.dev, epic E23).

The extension is GJS, so its real tests are gjs scripts under tests/js/; this file runs
them (skipped when `gjs` is missing) and adds the Python-side checks that keep the HUD
honest against the contract:

- the JSON fixtures the gjs tests use are genuine `Store.to_dict()` output of
  `smartpanel.state.model` (every key exists in the model), so a renamed field breaks
  here instead of silently drawing "—" on the desktop;
- every state field hudmodel.js reads exists in the model;
- the extension's syntax/link check really fails on a broken file;
- metadata.json, the settings keys and install-hud.sh are sane.

No side effects: gjs runs with its temp files under tmp_path, nothing is installed.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from smartpanel.config import DEFAULTS
from smartpanel.state.model import (
    JournalProgress,
    Loading,
    PanelState,
    Prompt,
    Session,
)
from smartpanel.state.store import Store, to_dict

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension" / "smartpanel-hud@omdev.dev"
JS = ROOT / "tests" / "js"
FIXTURES = ROOT / "tests" / "fixtures" / "hud"
INSTALL = ROOT / "scripts" / "install-hud.sh"

GJS = shutil.which("gjs")
needs_gjs = pytest.mark.skipif(GJS is None, reason="gjs not installed")


def run_gjs(script: Path, tmp_path: Path, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [GJS, "-m", str(script)],
        cwd=ROOT,
        env={**os.environ, "SMARTPANEL_HUD_TEST_TMP": str(tmp_path), **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


# ───────────────────────── gjs suites ─────────────────────────

@needs_gjs
@pytest.mark.parametrize("script", ["test_hudmodel.js", "test_throttle.js", "test_extension.js"])
def test_gjs_suite(script, tmp_path):
    proc = run_gjs(JS / script, tmp_path)
    assert proc.returncode == 0, f"{script} failed:\n{proc.stdout}\n{proc.stderr}"
    assert re.search(r"^(\d+)/\1 passed$", proc.stdout, re.M), proc.stdout


@needs_gjs
@pytest.mark.parametrize("breakage", [
    ("extension.js", "_relayout() {", "_relayout() {{"),                 # syntax error
    ("extension.js", "    placeHud,\n", "    placeHudd,\n"),             # missing named export
    ("hudmodel.js", "// smartPanel HUD", "import St from 'gi://St';\n// smartPanel HUD"),  # impure
])
def test_extension_check_catches_breakage(breakage, tmp_path):
    """The shell-free check must actually fail on a broken extension."""
    name, old, new = breakage
    broken = tmp_path / "ext"
    shutil.copytree(EXT, broken)
    text = (broken / name).read_text()
    assert old in text, f"breakage anchor {old!r} vanished from {name}"
    (broken / name).write_text(text.replace(old, new, 1))
    proc = run_gjs(JS / "test_extension.js", tmp_path, SMARTPANEL_HUD_EXT_DIR=str(broken))
    assert proc.returncode != 0, proc.stdout


# ───────────────────────── fixtures vs. the model ─────────────────────────

def _reference() -> dict:
    """A to_dict() tree with one of every nested object populated, for key checks."""
    st = PanelState()
    st.sessions.items = [Session(name="claude-x", prompt=Prompt(question="q"),
                                 journal=JournalProgress())]
    st.app.loading = Loading()
    return Store(st).to_dict()


FREE_FORM = {("settings", "values"), ("settings", "overridden_by")}


def _unknown_keys(data, ref, path=()) -> list[str]:
    if path in FREE_FORM:
        return []
    out = []
    if isinstance(data, dict):
        if not isinstance(ref, dict):
            return ["/".join(path) + " is not an object in the model"]
        for key, value in data.items():
            if key not in ref:
                out.append("/".join(path + (key,)))
            elif ref[key] is not None:
                out += _unknown_keys(value, ref[key], path + (key,))
    elif isinstance(data, list) and isinstance(ref, list) and ref and isinstance(ref[0], dict):
        for item in data:
            out += _unknown_keys(item, ref[0], path + ("[]",))
    return out


FIXTURE_FILES = sorted(FIXTURES.glob("*.json"))


def test_fixtures_exist():
    names = {p.stem for p in FIXTURE_FILES}
    assert {"busy", "many", "loading", "first_run", "no_tmux", "no_sessions", "no_swap",
            "disabled", "fresh", "swap_warning"} <= names


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.stem)
def test_fixture_matches_model(path):
    data = json.loads(path.read_text())
    assert _unknown_keys(data, _reference()) == []


def test_fresh_fixture_is_a_default_panel_state():
    assert json.loads((FIXTURES / "fresh.json").read_text()) == Store().to_dict()


# Every state path hudmodel.js reads. `app.tmux_ok` is a forward-compatible read of the
# SP-2605 wording; the model's field is `sessions.tmux_ok`, which the HUD reads first.
HUD_READS = {
    "app.loading", "app.loading.done", "app.loading.total", "app.first_run",
    "spend.cost_today", "spend.tokens_today", "spend.tokens_today_human", "spend.burn_per_hour",
    "sessions.items", "sessions.tmux_ok",
    "sessions.items[].name", "sessions.items[].title", "sessions.items[].project",
    "sessions.items[].activity", "sessions.items[].phase",
    "system.cpu.percent", "system.memory.percent", "system.swap.percent", "system.swap.level",
    "system.net.down_bps", "settings.values",
}
OPTIONAL_READS = {"app.tmux_ok"}


@pytest.mark.parametrize("dotted", sorted(HUD_READS))
def test_hud_reads_real_fields(dotted):
    node = _reference()
    for part in dotted.split("."):
        if part.endswith("[]"):
            node = node[part[:-2]][0]
        else:
            assert part in node, f"{dotted}: {part!r} not in the model"
            node = node[part]


def test_hudmodel_reads_nothing_unexpected():
    """Field names hudmodel.js dereferences must be in HUD_READS (or deliberately optional)."""
    src = (EXT / "hudmodel.js").read_text()
    used = set(re.findall(r"\.(\w+)\b", src)) & {
        f.name for cls in _dataclasses() for f in dataclasses.fields(cls)}
    allowed = {part.removesuffix("[]") for d in HUD_READS | OPTIONAL_READS
               for part in d.split(".")}
    # generic JS members that happen to share a name with a model field
    allowed |= {"values", "items", "length"}
    assert used <= allowed, used - allowed


def _dataclasses():
    seen, todo = set(), [PanelState]
    while todo:
        cls = todo.pop()
        if cls in seen:
            continue
        seen.add(cls)
        for f in dataclasses.fields(cls):
            default = (f.default_factory() if f.default_factory is not dataclasses.MISSING
                       else None)
            if dataclasses.is_dataclass(default):
                todo.append(type(default))
    return seen | {Session, Prompt, JournalProgress, Loading}


# ───────────────────────── settings, metadata, installer ─────────────────────────

def test_hud_settings_keys_exist_with_contract_defaults():
    assert DEFAULTS["hud.enabled"] is False
    assert DEFAULTS["hud.corner"] == "top-right"
    src = (EXT / "hudmodel.js").read_text()
    for key in ("hud.enabled", "hud.corner", "hud.margin"):
        assert f"'{key}'" in src
    # the margin key is optional on the app side: the HUD falls back to its default
    assert "DEFAULT_MARGIN = 30" in src


def test_metadata():
    meta = json.loads((EXT / "metadata.json").read_text())
    assert meta["uuid"] == "smartpanel-hud@omdev.dev" == EXT.name
    assert meta["name"] == "smartPanel HUD"
    assert meta["shell-version"] == ["50"]
    assert meta["description"]


def test_extension_files_present():
    for name in ("extension.js", "hudmodel.js", "throttle.js", "stylesheet.css", "metadata.json"):
        assert (EXT / name).is_file(), name


def test_stylesheet_has_every_state_class():
    css = (EXT / "stylesheet.css").read_text()
    for state in ("waiting", "running", "journaling", "launching", "idle", "unknown",
                  "warn", "crit"):
        assert f".smartpanel-hud-{state}" in css, state


def test_extension_is_not_reactive_and_uses_background_group():
    src = (EXT / "extension.js").read_text()
    assert "_backgroundGroup" in src
    assert "reactive: true" not in src


def test_install_script_syntax():
    assert os.access(INSTALL, os.X_OK)
    subprocess.run(["bash", "-n", str(INSTALL)], check=True, timeout=10)


def _confined_install(tmp_path: Path, *args: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run install-hud.sh with HOME, PATH and the output dir confined to tmp_path.

    `gnome-extensions` and `gnome-shell` are shims that only log their argv, so a
    regression in the script's option parsing can never install the extension into
    the real user's session. Returns the process and the logged gnome-extensions calls.
    """
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    log = tmp_path / "calls.log"
    (fakebin / "gnome-extensions").write_text(
        f'#!/bin/sh\necho "$*" >> "{log}"\nexit 0\n')
    (fakebin / "gnome-shell").write_text("#!/bin/sh\necho 'GNOME Shell 50.0'\n")
    for shim in fakebin.iterdir():
        shim.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), "PATH": f"{fakebin}:/usr/bin:/bin",
           "SMARTPANEL_HUD_OUT": str(tmp_path / "dist")}
    proc = subprocess.run([str(INSTALL), *args], capture_output=True, text=True,
                          timeout=30, cwd=tmp_path, env=env)
    calls = log.read_text().splitlines() if log.exists() else []
    assert not (home / ".local" / "share" / "gnome-shell").exists()
    return proc, calls


def test_install_script_dry_run_changes_nothing(tmp_path):
    zip_path = ROOT / "dist" / "smartpanel-hud@omdev.dev.shell-extension.zip"
    before = zip_path.exists()
    proc, calls = _confined_install(tmp_path, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert calls == [], "a dry run must not call gnome-extensions at all"
    assert not (tmp_path / "dist").exists()
    out = proc.stdout
    assert "gnome-extensions pack" in out and "--extra-source=hudmodel.js" in out
    assert "--extra-source=throttle.js" in out
    assert "gnome-extensions install --force" in out
    assert "dry run" in out
    assert "Log out and back in" in out
    assert "gnome-extensions enable smartpanel-hud@omdev.dev" in out
    assert "X11" not in out and "Alt+F2" not in out, "GNOME Shell 50 has no X11 session"
    assert zip_path.exists() == before, "dry run must not build anything"


def test_install_script_never_enables_the_extension(tmp_path):
    # The shim answers `info` with success, i.e. a re-install the shell already knows:
    # exactly the case where the installer used to switch the HUD back on by itself.
    proc, calls = _confined_install(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert any(c.startswith("pack ") for c in calls)
    assert any(c.startswith("install --force ") for c in calls)
    assert not any(c.split()[0] in {"enable", "info"} for c in calls), calls
    assert "Enable it:" in proc.stdout


def test_install_script_help_is_only_the_header(tmp_path):
    proc, _ = _confined_install(tmp_path, "--help")
    assert proc.returncode == 0
    assert "set -euo" not in proc.stdout
    assert "--dry-run" in proc.stdout


def test_reference_tree_is_plain_json():
    json.dumps(to_dict(PanelState()))
