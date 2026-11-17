from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from smartpanel.sessions import journal


# ---- transcript discovery ----

def test_project_dir_dash_encodes_the_cwd():
    d = journal.project_dir_for_cwd("/home/user/projects/smartPanel")
    assert d.name == "-home-user-projects-smartPanel"
    assert d.parent == Path.home() / ".claude" / "projects"


def test_session_id_is_the_transcript_stem():
    assert journal.session_id_of(Path("/x/914611b9-8924-412d.jsonl")) == "914611b9-8924-412d"


def test_find_transcript_picks_the_newest(tmp_path, monkeypatch):
    cwd = tmp_path / "proj"
    cwd.mkdir()
    pdir = tmp_path / "projects" / str(cwd).replace("/", "-")
    pdir.mkdir(parents=True)
    old, new = pdir / "old.jsonl", pdir / "new.jsonl"
    old.write_text("{}\n")
    new.write_text("{}\n")
    import os
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    monkeypatch.setattr(journal, "project_dir_for_cwd", lambda _c: pdir)

    assert journal.find_transcript(cwd) == new


def test_find_transcript_returns_none_when_no_project_dir(tmp_path):
    assert journal.find_transcript(tmp_path / "never-used") is None


# ---- the skill invocation ----

def test_skill_prompt_scopes_to_the_project():
    assert journal.skill_prompt("webapp") == "/journal webapp"
    assert journal.skill_prompt(None) == "/journal"


def test_build_skill_command_resumes_the_session_unattended():
    cmd = journal.build_skill_command("abc-123", "webapp", model="", tools=["Bash", "Write"])
    assert cmd[:4] == ["claude", "-p", "--resume", "abc-123"]
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    assert cmd[cmd.index("--allowedTools") + 1:] == ["Bash", "Write"]


def test_build_skill_command_keeps_the_variadic_flag_last():
    """The prompt goes on stdin: a trailing positional would be eaten by --allowedTools."""
    cmd = journal.build_skill_command("abc-123", "webapp")
    assert "/journal" not in " ".join(cmd)
    assert cmd.index("--allowedTools") == len(cmd) - 1 - len(journal.SKILL_TOOLS)


def test_build_skill_command_omits_model_when_unset():
    assert "--model" not in journal.build_skill_command("abc", None, model="")
    assert "--model" in journal.build_skill_command("abc", None, model="claude-haiku-4-5")


def test_run_journal_skill_returns_stdout_and_runs_in_the_session_cwd(monkeypatch, tmp_path):
    seen = {}

    class Result:
        returncode = 0
        stdout = "Appended 2026-07-31 § webapp — 3 done, 2 decided.\n"
        stderr = ""

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return Result()

    monkeypatch.setattr(journal.subprocess, "run", fake_run)
    out = journal.run_journal_skill("abc-123", tmp_path, "webapp")

    assert out == "Appended 2026-07-31 § webapp — 3 done, 2 decided."
    assert seen["input"] == "/journal webapp"
    assert seen["cwd"] == str(tmp_path)


def test_run_journal_skill_swallows_a_missing_claude(monkeypatch):
    def boom(*_a, **_kw):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(journal.subprocess, "run", boom)
    assert journal.run_journal_skill("abc-123", None, "webapp") is None


def test_run_journal_skill_swallows_a_nonzero_exit(monkeypatch):
    class Result:
        returncode = 1
        stdout = ""
        stderr = "no such session"

    monkeypatch.setattr(journal.subprocess, "run", lambda *_a, **_kw: Result())
    assert journal.run_journal_skill("abc-123", None, None) is None


# ---- condensing ----

def _transcript(path: Path, lines: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(o) for o in lines) + "\n")
    return path


def test_condense_extracts_prompts_tools_files_and_commands(tmp_path):
    t = _transcript(tmp_path / "s.jsonl", [
        {"type": "user", "message": {"content": "fix the parser"}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "on it"},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/a/parser.py"}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}},
        ]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/a/parser.py"}},
        ]}},
        {"type": "summary", "summary": "ignored"},
        {"not json at all": True},
    ])
    d = journal.condense(t)

    assert d.user_prompts == ["fix the parser"]
    assert d.assistant_texts == ["on it"]
    assert d.tool_counts == {"Edit": 2, "Bash": 1}
    assert d.files == ["/a/parser.py"]  # de-duped
    assert d.commands == ["pytest -q"]
    assert "USER: fix the parser" in d.condensed


def test_condense_skips_malformed_lines(tmp_path):
    t = tmp_path / "s.jsonl"
    t.write_text('{"type":"user","message":{"content":"ok"}}\nnot-json\n\n')
    assert journal.condense(t).user_prompts == ["ok"]


def test_condense_truncates_from_the_front(tmp_path):
    t = _transcript(tmp_path / "s.jsonl", [
        {"type": "user", "message": {"content": "x" * 200}} for _ in range(20)
    ])
    d = journal.condense(t, max_chars=500)
    assert d.condensed.startswith("…(truncated)…")
    assert len(d.condensed) <= 500 + len("…(truncated)…\n")


# ---- rendering ----

WHEN = datetime(2026, 7, 31, 9, 30, 0)


def test_render_leads_with_the_skill_result():
    md = journal._render_markdown(
        "webapp", "/home/user/projects/webapp", Path("/t/abc.jsonl"),
        journal.Digest(user_prompts=["a"], tool_counts={"Bash": 2},
                       files=["/a/x.py"], commands=["go test ./..."]),
        duration_s=3720, when=WHEN,
        skill_result="Appended 2026-07-31 § webapp — 3 done.",
    )
    assert "# Session journal — webapp" in md
    assert "- **Duration:** 1h 2m" in md
    assert "- **Session:** `abc`" in md
    assert "Appended 2026-07-31 § webapp — 3 done." in md
    assert "Bash×2" in md
    assert "`/a/x.py`" in md
    assert "`go test ./...`" in md


def test_render_says_so_when_the_skill_did_not_run():
    md = journal._render_markdown("webapp", "/p", None, None, None, WHEN, None)
    assert "did not run" in md
    assert "_(No transcript found for this folder.)_" in md


def test_write_journal_writes_a_receipt_even_without_a_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path / "journals")
    p = journal.write_journal("webapp", None, duration_s=60, run_skill=False)
    assert p is not None and p.exists()
    text = p.read_text()
    assert text.startswith("---\n")
    header, body = text[4:].split("\n---\n\n", 1)
    assert "project: webapp" in header
    assert body.startswith("# Session journal — webapp")


def test_write_journal_runs_the_skill_with_the_sessions_id(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path / "journals")
    monkeypatch.setattr(journal, "find_transcript", lambda _c: Path("/t/sid-1.jsonl"))
    monkeypatch.setattr(journal, "condense", lambda _t: journal.Digest())
    monkeypatch.setattr(journal, "run_journal_skill",
                        lambda sid, cwd, proj: calls.append((sid, cwd, proj)) or "done.")

    p = journal.write_journal("webapp", "/home/user/projects/webapp")

    assert calls == [("sid-1", "/home/user/projects/webapp", "webapp")]
    assert "done." in p.read_text()


def test_safe_filename_strips_path_separators():
    assert journal._safe("a/b c") == "a-b-c"
    assert journal._safe("") == "session"


# ---- driving the skill inside the live session ----

def _idle(text="ready"):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _working():
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Task", "input": {}}]}}


def test_classify_last_event_reads_turn_boundaries():
    assert journal.classify_last_event(_idle()) == "idle"
    assert journal.classify_last_event(_working()) == "working"
    # A user line mid-conversation is normally a tool_result feeding a tool back.
    assert journal.classify_last_event({"type": "user", "message": {"content": "hi"}}) == "working"
    assert journal.classify_last_event({"type": "summary"}) == "unknown"
    assert journal.classify_last_event(None) == "unknown"


def test_tail_state_counts_lines_and_returns_the_last_assistant_text(tmp_path):
    t = _transcript(tmp_path / "s.jsonl", [_idle("first"), _working(), _idle("last")])
    count, state, text = journal.tail_state(t)
    assert (count, state, text) == (3, "idle", "last")


def test_tail_state_reports_working_mid_turn(tmp_path):
    t = _transcript(tmp_path / "s.jsonl", [_idle("first"), _working()])
    assert journal.tail_state(t)[1] == "working"


def _fake_tmux(monkeypatch, sent, alive=True, send_ok=True):
    monkeypatch.setattr(journal.tmux, "has_session", lambda _n: alive)
    monkeypatch.setattr(journal.tmux, "send_text",
                        lambda n, txt: sent.append(("text", n, txt)) or send_ok)
    monkeypatch.setattr(journal.tmux, "send_enter",
                        lambda n: sent.append(("enter", n)) or send_ok)


def _stepper(transcript: Path, steps: list[dict]):
    """A sleep stub that advances the simulated session one event per call."""
    it = iter(steps)

    def fake_sleep(_s: float) -> None:
        nxt = next(it, None)
        if nxt is not None:
            with open(transcript, "a") as f:
                f.write(json.dumps(nxt) + "\n")

    return fake_sleep


def test_journal_in_session_types_the_command_and_waits_for_the_answer(tmp_path, monkeypatch):
    t = _transcript(tmp_path / "sid.jsonl", [_idle()])
    sent: list = []
    _fake_tmux(monkeypatch, sent)
    step = _stepper(t, [
        {"type": "user", "message": {"content": "/journal webapp"}},
        _working(),
        _idle("Appended 2026-07-31 § webapp — 3 done."),
    ])

    r = journal.journal_in_session("claude-webapp", t, "webapp", sleep=step)

    assert r.ok and r.text == "Appended 2026-07-31 § webapp — 3 done."
    assert ("text", "claude-webapp", "/journal webapp") in sent
    assert ("enter", "claude-webapp") in sent


def test_journal_in_session_will_not_type_into_a_busy_session(tmp_path, monkeypatch):
    """Keys sent mid-turn land in the input box and corrupt the next prompt."""
    t = _transcript(tmp_path / "sid.jsonl", [_working()])
    sent: list = []
    _fake_tmux(monkeypatch, sent)

    r = journal.journal_in_session("claude-x", t, "x", idle_wait_s=0, sleep=lambda _s: None)

    assert r.status == journal.LIVE_UNAVAILABLE
    assert sent == []  # nothing was typed


def test_journal_in_session_is_unavailable_when_the_session_is_gone(tmp_path, monkeypatch):
    t = _transcript(tmp_path / "sid.jsonl", [_idle()])
    sent: list = []
    _fake_tmux(monkeypatch, sent, alive=False)
    r = journal.journal_in_session("claude-gone", t, "x", sleep=lambda _s: None)
    assert r.status == journal.LIVE_UNAVAILABLE and r.retryable


def test_journal_in_session_nudges_enter_then_reports_not_submitted(tmp_path, monkeypatch):
    """The slash-command autocomplete can swallow the first Enter."""
    t = _transcript(tmp_path / "sid.jsonl", [_idle()])
    sent: list = []
    _fake_tmux(monkeypatch, sent)

    r = journal.journal_in_session("claude-x", t, "x", submit_wait_s=0,
                                   sleep=lambda _s: None)

    assert r.status == journal.LIVE_NOT_SUBMITTED and r.retryable
    assert [s for s in sent if s[0] == "enter"] == [("enter", "claude-x")] * 2


def test_journal_in_session_timeout_is_not_retryable(tmp_path, monkeypatch):
    """Once the prompt is in, re-running headlessly would duplicate the entry."""
    t = _transcript(tmp_path / "sid.jsonl", [_idle()])
    sent: list = []
    _fake_tmux(monkeypatch, sent)
    # The prompt lands (transcript grows) but the turn never completes.
    step = _stepper(t, [{"type": "user", "message": {"content": "/journal x"}}])

    r = journal.journal_in_session("claude-x", t, "x", timeout_s=0, sleep=step)

    assert r.status == journal.LIVE_TIMEOUT
    assert not r.retryable


# ---- write_journal: ordering of journal vs. close ----

def test_write_journal_closes_the_session_only_after_the_live_run(tmp_path, monkeypatch):
    """The whole point of the live path: the session stays up until it answers."""
    order: list[str] = []
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path / "journals")
    monkeypatch.setattr(journal, "find_transcript", lambda _c: Path("/t/sid-1.jsonl"))
    monkeypatch.setattr(journal, "condense", lambda _t: journal.Digest())
    monkeypatch.setattr(journal, "journal_in_session",
                        lambda *a, **kw: order.append("journal")
                        or journal.LiveResult(journal.LIVE_OK, "Appended § webapp."))
    monkeypatch.setattr(journal, "run_journal_skill",
                        lambda *a: order.append("resume") or "should not happen")

    p = journal.write_journal("webapp", "/p", session_name="claude-webapp",
                              close_session=lambda: order.append("close"))

    assert order == ["journal", "close"]  # journal first, close second
    assert "Appended § webapp." in p.read_text()


def test_write_journal_falls_back_to_resume_and_closes_first(tmp_path, monkeypatch):
    """--resume reuses the session id, so the session must be dead before it runs."""
    order: list[str] = []
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path / "journals")
    monkeypatch.setattr(journal, "find_transcript", lambda _c: Path("/t/sid-1.jsonl"))
    monkeypatch.setattr(journal, "condense", lambda _t: journal.Digest())
    monkeypatch.setattr(journal, "journal_in_session",
                        lambda *a, **kw: journal.LiveResult(journal.LIVE_NOT_SUBMITTED))
    monkeypatch.setattr(journal, "run_journal_skill",
                        lambda *a: order.append("resume") or "Appended via resume.")

    p = journal.write_journal("webapp", "/p", session_name="claude-webapp",
                              close_session=lambda: order.append("close"))

    assert order == ["close", "resume"]  # close first, then resume
    assert "Appended via resume." in p.read_text()


def test_write_journal_does_not_double_run_after_a_live_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path / "journals")
    monkeypatch.setattr(journal, "find_transcript", lambda _c: Path("/t/sid-1.jsonl"))
    monkeypatch.setattr(journal, "condense", lambda _t: journal.Digest())
    monkeypatch.setattr(journal, "journal_in_session",
                        lambda *a, **kw: journal.LiveResult(journal.LIVE_TIMEOUT))
    calls: list = []
    monkeypatch.setattr(journal, "run_journal_skill", lambda *a: calls.append(a) or "x")

    p = journal.write_journal("webapp", "/p", session_name="claude-webapp",
                              close_session=lambda: None)

    assert calls == []  # never retried
    assert "had not finished" in p.read_text()


def test_write_journal_closes_the_session_even_if_journaling_blows_up(tmp_path, monkeypatch):
    """A wedged journal must never leave a session that cannot be closed."""
    closed: list = []
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path / "journals")
    monkeypatch.setattr(journal, "find_transcript", lambda _c: Path("/t/sid-1.jsonl"))
    monkeypatch.setattr(journal, "condense", lambda _t: journal.Digest())

    def boom(*_a, **_kw):
        raise RuntimeError("tmux exploded")

    monkeypatch.setattr(journal, "journal_in_session", boom)

    done = threading.Event()
    journal.write_journal_async("webapp", "/p", session_name="claude-webapp",
                                close_session=lambda: closed.append(True),
                                on_done=lambda _p: done.set())
    assert done.wait(5)
    assert closed == [True]  # exactly once


def test_once_runs_the_callback_a_single_time():
    calls = []
    f = journal._once(lambda: calls.append(1))
    f(); f(); f()
    assert calls == [1]
    journal._once(None)()  # tolerates no callback
