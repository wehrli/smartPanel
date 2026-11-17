"""CLI parsing and forwarding helpers (SP-0011). No GTK, no running instance."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from smartpanel import cli

ROOT = Path(__file__).resolve().parent.parent


def test_bare_invocation():
    ns = cli.parse_args([])
    assert ns.dashboard is None and not ns.palette and ns.hud is None
    assert cli.requests_from(ns, remote=False) == []
    # Launching again while running shows the dashboard.
    assert cli.requests_from(ns, remote=True) == [cli.Request("dashboard", "")]


@pytest.mark.parametrize("argv,expected", [
    (["--dashboard"], [cli.Request("dashboard", "")]),
    (["--dashboard", "sessions:board"], [cli.Request("dashboard", "sessions:board")]),
    (["--dashboard", "journal:2026-11-17-1202-x"], [cli.Request("dashboard", "journal:2026-11-17-1202-x")]),
    (["--palette"], [cli.Request("palette")]),
    (["--hud", "toggle"], [cli.Request("hud", "toggle")]),
    (["--dashboard", "spend", "--hud", "on"], [cli.Request("dashboard", "spend"), cli.Request("hud", "on")]),
])
def test_verbs_become_requests(argv, expected):
    ns = cli.parse_args(argv)
    assert cli.requests_from(ns, remote=True) == expected
    assert cli.requests_from(ns, remote=False) == expected


def test_dashboard_without_page_followed_by_flag():
    ns = cli.parse_args(["--dashboard", "--palette"])
    assert ns.dashboard == "" and ns.palette


@pytest.mark.parametrize("arg,current,expected", [
    ("on", False, True), ("off", True, False), ("toggle", True, False), ("toggle", False, True),
])
def test_hud_target(arg, current, expected):
    assert cli.hud_target(arg, current) is expected


@pytest.mark.parametrize("argv", [
    ["--hud", "maybe"], ["--demo", "nope"], ["--bogus"], ["--snapshot", "overview"],
    ["--out", "x.png"], ["--snapshot", "overview", "--out", "x.png", "--size", "big"],
    ["--snapshot", "overview", "--out", "x.png", "--size", "10x10"],
])
def test_bad_arguments_raise_instead_of_exiting(argv):
    with pytest.raises(cli.CliError):
        cli.parse_args(argv)


def test_demo_scenarios_accepted():
    for name in cli.SCENARIOS:
        assert cli.parse_args(["--demo", name]).demo == name


def test_startup_options_are_flagged_when_remote():
    assert cli.ignored_when_remote(cli.parse_args(["--demo", "busy", "--theme", "x"])) == [
        "--demo", "--theme"]
    assert cli.ignored_when_remote(cli.parse_args(["--palette"])) == []


def test_snapshot_args_and_size():
    ns = cli.parse_args(["--demo", "busy", "--snapshot", "sessions:board", "--out", "b.png",
                         "--size", "1000x700", "--style", "light"])
    assert (ns.snapshot, ns.out, ns.style) == ("sessions:board", "b.png", "light")
    assert cli.parse_size(ns.size) == (1000, 700)


def test_log_levels():
    import logging
    assert cli.log_level(cli.parse_args([])) == logging.WARNING
    assert cli.log_level(cli.parse_args(["-v"])) == logging.INFO
    assert cli.log_level(cli.parse_args(["--verbose", "--debug"])) == logging.DEBUG


def test_resolve_theme(tmp_path):
    assert cli.resolve_theme(None).name == "default.yaml"
    assert cli.resolve_theme("claude-focus").name == "claude-focus.yaml"
    f = tmp_path / "mine.yaml"
    f.write_text("STATS: {}\n")
    assert cli.resolve_theme(str(f)) == f
    with pytest.raises(cli.CliError):
        cli.resolve_theme("no-such-theme")


def test_main_rejects_bad_args_with_status_2(capsys):
    assert cli.main(["--hud", "maybe"]) == 2
    assert "invalid choice" in capsys.readouterr().err
    assert cli.main(["--theme", "no-such-theme"]) == 2


def test_demo_gallery_is_the_gallery():
    ns = cli.parse_args(["--demo", "gallery"])
    assert ns.gallery and ns.demo is None


def test_garbled_theme_is_a_usage_error(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("STATS: {CPU: [unclosed\n  - : :\n")
    with pytest.raises(cli.CliError):
        cli.check_theme(str(bad))
    assert cli.main(["--theme", str(bad)]) == 2
    assert "bad theme" in capsys.readouterr().err
    assert cli.check_theme("default").name == "default.yaml"


def test_main_help(capsys):
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    for flag in ("--dashboard", "--palette", "--hud", "--demo", "--snapshot", "--praise-report"):
        assert flag in out


def _run(args, tmp_path, code=None):
    env = dict(os.environ, HOME=str(tmp_path), PYTHONPATH=str(ROOT))
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    return subprocess.run([sys.executable, "-c", code] if code else
                          [sys.executable, "-m", "smartpanel", *args],
                          capture_output=True, text=True, env=env, timeout=60, cwd=tmp_path)


def test_praise_report_runs_headless_without_gtk(tmp_path):
    code = ("import sys; from smartpanel import cli; rc = cli.main(['--praise-report']); "
            "assert 'gi.repository.Gtk' not in sys.modules, 'GTK imported'; sys.exit(rc)")
    r = _run([], tmp_path, code=code)
    assert r.returncode == 0, r.stderr
    assert "Gratification report" in r.stdout


def test_cli_module_imports_without_gtk(tmp_path):
    r = _run([], tmp_path, code="import sys, smartpanel.cli, smartpanel.ui.shellmodel, "
                                "smartpanel.ui.charts, smartpanel.ui.tokens; "
                                "sys.exit('gi.repository.Gtk' in sys.modules)")
    assert r.returncode == 0, r.stderr
