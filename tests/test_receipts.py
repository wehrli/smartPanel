from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from smartpanel.sessions import journal, receipts
from smartpanel.sessions.receipts import ReceiptIndex, load_receipt, parse_receipt

FIXTURES = Path(__file__).parent / "fixtures" / "receipts"


@pytest.fixture
def jdir(tmp_path):
    d = tmp_path / "journals"
    shutil.copytree(FIXTURES, d)
    return d


def test_index_lists_newest_first_including_unreadable(jdir):
    entries = ReceiptIndex(jdir).scan()
    assert [e.id for e in entries] == [
        "20261117-160000-broken",
        "20261117-150210-tacomap",
        "20261117-113846-giftswap-2",
        "20261012-225216-ledgerly",
        "20260701-172909-tacomap-2",
    ]


def test_front_matter_receipt(jdir):
    e = load_receipt(jdir / "20261117-150210-tacomap.md")
    assert e.readable
    assert e.title == "Menu import from CSV" and e.project == "tacomap"
    assert e.outcome == "journaled"
    assert e.duration_s == 9000
    assert e.files == ["/home/user/dev/omdev/tacomap/api/menu/import.go"]
    assert e.commands == ["go test ./menu/..."]
    assert e.tools == {"Read": 12, "Edit": 5, "Bash": 4}
    assert e.confirmation.startswith("Journaled tacomap")
    assert e.time == datetime(2026, 11, 17, 15, 2, 10).timestamp()


def test_old_receipt_parsed_from_headings(jdir):
    e = load_receipt(jdir / "20261012-225216-ledgerly.md")
    assert e.readable
    assert e.project == "ledgerly" and e.title == "ledgerly"
    assert e.outcome == "journaled"
    assert e.confirmation.startswith("Appended 2026-10-12 § ledgerly")
    assert e.duration_s == 3720
    assert e.tools == {"Read": 9, "Edit": 4, "Bash": 3}
    assert e.files[1] == "/home/user/dev/omdev/ledgerly/README.md"
    assert e.commands == ["go test ./...", "make lint"]
    assert e.time == datetime(2026, 10, 12, 22, 52, 16).timestamp()


def test_old_receipt_where_the_skill_did_not_run(jdir):
    e = load_receipt(jdir / "20261117-113846-giftswap-2.md")
    assert e.outcome == "skill_failed"
    assert "did not run" in e.confirmation
    assert e.duration_s == 504
    assert e.commands == ["ls; ls santasecretapi santasecretweb",
                          "cat internal/core/services/assignment_engine.go"]


def test_legacy_summary_receipt_is_receipt_only(jdir):
    # The oldest receipts used "## Summary" for the slot the skill fills now.
    e = load_receipt(jdir / "20260701-172909-tacomap-2.md")
    assert e.readable
    assert e.outcome == "skill_failed"
    assert "LLM summary unavailable" in e.confirmation
    assert e.duration_s == 5 * 3600 + 34 * 60


def test_broken_front_matter_is_listed_as_unreadable(jdir):
    e = load_receipt(jdir / "20261117-160000-broken.md")
    assert not e.readable
    assert e.confirmation == "Couldn't read this receipt"
    assert e.time == datetime(2026, 11, 17, 16, 0, 0).timestamp()


def test_binary_garbage_is_unreadable(tmp_path):
    p = tmp_path / "x.md"
    p.write_bytes(b"\xff\xfe\x00garbage")
    assert not load_receipt(p).readable
    assert parse_receipt("just some notes", "x", "x") is None


def test_a_receipt_written_by_run_journal_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path / "j")
    monkeypatch.setattr(journal, "find_transcript", lambda _c: None)
    r = journal.run_journal("webapp", "/p", duration_s=61, title="Fix login",
                            session_name="claude-webapp")
    [e] = ReceiptIndex().scan()  # default dir follows journal.JOURNAL_DIR
    assert e.id == r.path.stem
    assert e.title == "Fix login" and e.outcome == "skill_failed"
    assert e.confirmation == journal.NO_TRANSCRIPT_CONFIRMATION
    assert e.duration_s == 61


def test_cache_reparses_only_changed_files(jdir, monkeypatch):
    idx = ReceiptIndex(jdir)
    idx.scan()
    calls = []
    real = receipts.load_receipt
    monkeypatch.setattr(receipts, "load_receipt",
                        lambda p, mtime=None: calls.append(p.name) or real(p, mtime))
    idx.scan()
    assert calls == []
    p = jdir / "20261012-225216-ledgerly.md"
    p.write_text(p.read_text().replace("ledgerly", "ledgerly2", 1))
    os.utime(p, (1, 1))
    entries = idx.scan()
    assert calls == ["20261012-225216-ledgerly.md"]
    (jdir / "20261117-160000-broken.md").unlink()
    assert len(idx.scan()) == len(entries) - 1


def test_missing_dir_is_empty(tmp_path):
    assert ReceiptIndex(tmp_path / "nope").scan() == []


def test_monthly_log_path(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTPANEL_JOURNAL_LOG_DIR", str(tmp_path))
    now = datetime(2026, 11, 17)
    assert receipts.monthly_log_path(now) is None
    (tmp_path / "2026-11.md").write_text("x")
    assert receipts.monthly_log_path(now) == str(tmp_path / "2026-11.md")
