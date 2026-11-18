"""SP-1601/1602/1801: journal steps, outcome and the receipt's front matter."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import yaml

from smartpanel.sessions import journal
from smartpanel.state.model import JOURNAL_STEPS


def _transcript(tmp_path) -> Path:
    t = tmp_path / "sid-9.jsonl"
    t.write_text("\n".join(json.dumps(o) for o in [
        {"type": "user", "message": {"content": "fix it"}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/p/a.py"}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "make test\nmore"}}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}},
    ]) + "\n")
    return t


def _setup(monkeypatch, tmp_path, live, resume=None):
    t = _transcript(tmp_path)
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path / "journals")
    monkeypatch.setattr(journal, "find_transcript", lambda _c: t)

    def fake_live(name, transcript, project, progress=None, **kw):
        for step in live[1]:
            progress(step)
        return live[0]

    monkeypatch.setattr(journal, "journal_in_session", fake_live)
    monkeypatch.setattr(journal, "run_journal_skill", lambda *a: resume)


def _run(**kw):
    steps, order = [], []
    r = journal.run_journal(
        "webapp", "/p", duration_s=3725, session_name="claude-webapp",
        close_session=lambda: order.append("close"), title="Fix login",
        on_progress=lambda step, **k: steps.append((step, k.get("headless", False),
                                                    k.get("failed", False))), **kw)
    return r, steps, order


def test_step_names_match_the_contract():
    assert len(JOURNAL_STEPS) == 5
    assert (journal.STEP_WAIT_IDLE, journal.STEP_RECEIPT) == (0, 4)


def test_live_route_reports_every_step_in_order(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path,
           (journal.LiveResult(journal.LIVE_OK, "Appended § webapp."), [0, 1, 2]))
    r, steps, order = _run()
    assert [s for s, _, _ in steps] == [0, 0, 1, 2, 3, 4]
    assert not any(h or f for _, h, f in steps)
    assert r.outcome == "journaled" and r.confirmation == "Appended § webapp."
    assert not r.headless and order == ["close"]


def test_headless_route_reports_the_same_steps_flagged(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, (journal.LiveResult(journal.LIVE_UNAVAILABLE), []),
           resume="Appended via resume.")
    r, steps, _ = _run()
    assert steps == [(0, False, False), (1, True, False), (2, True, False),
                     (3, True, False), (4, True, False)]
    assert r.outcome == "journaled" and r.headless
    assert r.confirmation == "Appended via resume."


def test_failed_skill_stops_on_the_skill_step(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, (journal.LiveResult(journal.LIVE_NOT_SUBMITTED), [0, 1]),
           resume=None)
    r, steps, order = _run()
    assert steps[-1] == (2, True, True)
    assert all(s <= 2 for s, _, _ in steps)
    assert r.outcome == "skill_failed"
    assert r.confirmation == journal.FAILED_CONFIRMATION
    assert order == ["close"]  # closed anyway
    assert r.path and r.path.exists()


def test_timeout_outcome(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, (journal.LiveResult(journal.LIVE_TIMEOUT), [0, 1, 2]))
    r, steps, _ = _run()
    assert r.outcome == "timeout"
    assert steps[-1] == (2, False, True)
    assert "had not finished" in r.confirmation


def test_no_transcript_is_skill_failed_with_a_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path / "journals")
    monkeypatch.setattr(journal, "find_transcript", lambda _c: None)
    r, _, order = _run()
    assert r.outcome == "skill_failed"
    assert r.confirmation == journal.NO_TRANSCRIPT_CONFIRMATION
    assert order == ["close"]


def test_no_transcript_fails_on_the_first_step_not_the_skill_step(monkeypatch, tmp_path):
    """Nothing was typed, so the checklist must not tick 'Type /journal'."""
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path / "journals")
    monkeypatch.setattr(journal, "find_transcript", lambda _c: None)
    _, steps, _ = _run()
    assert steps == [(0, False, False), (0, False, True)]


def test_session_gone_before_typing_fails_where_the_headless_run_stopped(monkeypatch, tmp_path):
    """Live never got to typing; the headless retry reached the skill and failed there."""
    _setup(monkeypatch, tmp_path, (journal.LiveResult(journal.LIVE_UNAVAILABLE), [0]),
           resume=None)
    _, steps, _ = _run()
    assert steps[-1] == (2, True, True)


def test_never_journaling_fails_on_the_first_step(monkeypatch, tmp_path):
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path / "journals")
    _, steps, _ = _run(run_skill=False)
    assert steps[-1] == (0, False, True)


def test_receipt_front_matter(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path,
           (journal.LiveResult(journal.LIVE_OK, "Appended § webapp."), [0, 1, 2]))
    r, _, _ = _run()
    text = r.path.read_text()
    assert text.startswith("---\n")
    meta = yaml.safe_load(text.split("---\n")[1])
    assert meta["session"] == "sid-9"
    assert meta["tmux"] == "claude-webapp"
    assert meta["title"] == "Fix login"
    assert meta["project"] == "webapp"
    assert meta["outcome"] == "journaled"
    assert meta["duration_s"] == 3725
    assert meta["files"] == ["/p/a.py"]
    assert meta["commands"] == ["make test"]
    assert meta["tools"] == {"Edit": 1, "Bash": 1}
    assert meta["confirmation"] == "Appended § webapp."
    # The Markdown body is unchanged.
    assert "# Session journal — webapp" in text and "## Activity" in text


def test_progress_callback_errors_do_not_stop_the_close(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path,
           (journal.LiveResult(journal.LIVE_OK, "ok"), [0, 1, 2]))
    order = []

    def bad(*_a, **_k):
        raise RuntimeError("ui gone")

    r = journal.run_journal("webapp", "/p", session_name="claude-webapp",
                            close_session=lambda: order.append("close"), on_progress=bad)
    assert order == ["close"] and r.outcome == "journaled"


def test_async_reports_a_result_even_when_journaling_blows_up(monkeypatch, tmp_path):
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path / "journals")
    monkeypatch.setattr(journal, "find_transcript", lambda _c: Path("/t/sid.jsonl"))

    def boom(*_a, **_k):
        raise RuntimeError("tmux exploded")

    monkeypatch.setattr(journal, "journal_in_session", boom)
    closed, results, paths = [], [], []
    done = threading.Event()
    journal.write_journal_async(
        "webapp", "/p", session_name="claude-webapp",
        close_session=lambda: closed.append(1),
        on_done=paths.append,
        on_result=lambda r: (results.append(r), done.set()))
    assert done.wait(5)
    assert closed == [1]
    assert results[0].outcome == "skill_failed" and results[0].path is None
    assert paths == [None]
