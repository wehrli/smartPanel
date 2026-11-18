"""ScanWorker (SP-2702): the transcript scan runs in a child process, so a cold
scan cannot hold the app's GIL. The protocol is tested in-process through
`serve()`; one test drives a real child end to end against the fixture tree."""
from __future__ import annotations

import io
import os
import pickle
import subprocess
import sys

import pytest
from pytest import approx

from smartpanel.claude_stats import scan_worker
from smartpanel.claude_stats.aggregator import Aggregator, Snapshot
from smartpanel.claude_stats.scan_worker import ScanWorker, ScanWorkerError, serve
from tests.fixtures import PARIS, SPEND_NOW, copy_spend_tree


def _requests(*roots) -> io.BytesIO:
    buf = io.BytesIO()
    for root in roots:
        pickle.dump({"root": str(root)}, buf)
    buf.seek(0)
    return buf


def _messages(out: io.BytesIO) -> list:
    out.seek(0)
    msgs = []
    while True:
        try:
            msgs.append(pickle.load(out))
        except EOFError:
            return msgs


def _pinned(**kw):
    return Aggregator(now_fn=lambda: SPEND_NOW, tz=PARIS, **kw)


def test_serve_answers_each_request_with_progress_then_a_result(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_worker, "CHILD_PROGRESS_INTERVAL_S", 0.0)
    root = copy_spend_tree(tmp_path)
    out = io.BytesIO()
    serve(_requests(root, root), out, aggregator_factory=_pinned)
    msgs = _messages(out)
    kinds = [m[0] for m in msgs]
    assert kinds == ["progress"] * 3 + ["result"] + ["progress"] * 3 + ["result"]
    assert [m[1:] for m in msgs[:3]] == [(1, 3), (2, 3), (3, 3)]
    want = _pinned(claude_root=root).refresh()
    for snap in (msgs[3][1], msgs[7][1]):
        assert isinstance(snap, Snapshot)
        assert snap.files_total == 3
        assert snap.today.cost_usd == approx(want.today.cost_usd)
        assert snap.per_file_cost == approx(want.per_file_cost)


def test_serve_keeps_one_aggregator_per_root_so_warm_scans_hit_the_cache(tmp_path):
    root = copy_spend_tree(tmp_path)
    made = []

    def factory(**kw):
        made.append(kw)
        return _pinned(**kw)
    serve(_requests(root, root, root), io.BytesIO(), aggregator_factory=factory)
    assert len(made) == 1


def test_serve_always_sends_the_last_progress_even_when_throttled(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_worker, "CHILD_PROGRESS_INTERVAL_S", 3600.0)
    root = copy_spend_tree(tmp_path)
    out = io.BytesIO()
    serve(_requests(root), out, aggregator_factory=_pinned)
    progress = [m[1:] for m in _messages(out) if m[0] == "progress"]
    assert progress[-1] == (3, 3)
    assert len(progress) < 3


def test_serve_reports_a_failed_scan_and_keeps_serving(tmp_path):
    class Broken:
        def __init__(self, **kw):
            pass

        def refresh(self, progress=None):
            raise RuntimeError("disk on fire")
    out = io.BytesIO()
    serve(_requests(tmp_path, tmp_path), out, aggregator_factory=Broken)
    msgs = _messages(out)
    assert [m[0] for m in msgs] == ["error", "error"]
    assert "disk on fire" in msgs[0][1]


# ── parent side, against a real child ──

def _children(worker: ScanWorker) -> int | None:
    return worker._proc.pid if worker._proc is not None else None


def test_real_child_scans_and_matches_the_in_process_aggregator(tmp_path):
    root = copy_spend_tree(tmp_path)
    worker = ScanWorker(claude_root=root)
    seen = []
    try:
        snap = worker.refresh(progress=lambda d, t: seen.append((d, t)))
        pid = _children(worker)
        again = worker.refresh()
        assert _children(worker) == pid          # one long-lived child
    finally:
        worker.close()
    want = Aggregator(claude_root=root).refresh()
    assert snap.files_total == again.files_total == 3
    assert snap.lifetime.cost_usd == approx(want.lifetime.cost_usd)
    assert snap.per_file_cost == approx(want.per_file_cost)
    assert seen and seen[-1] == (3, 3)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)                          # close() reaped it


def test_a_dead_child_fails_that_refresh_and_the_next_one_respawns(tmp_path):
    root = copy_spend_tree(tmp_path)
    worker = ScanWorker(claude_root=root)
    try:
        worker.refresh()
        first = worker._proc
        first.kill()
        first.wait()
        # poll() sees it dead, so this refresh simply starts a new child.
        assert worker.refresh().files_total == 3
        assert worker._proc is not first
    finally:
        worker.close()


def test_a_child_that_exits_mid_request_raises_scan_worker_error(tmp_path):
    cmd = [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"]
    worker = ScanWorker(claude_root=tmp_path, command=cmd)
    try:
        with pytest.raises(ScanWorkerError):
            worker.refresh()
        assert worker._proc is None
    finally:
        worker.close()


def test_falls_back_to_scanning_in_process_when_the_child_cannot_start(tmp_path):
    root = copy_spend_tree(tmp_path)
    worker = ScanWorker(claude_root=root, command=[str(tmp_path / "no-such-python")])
    snap = worker.refresh()
    assert snap.files_total == 3
    worker.close()


def test_closed_worker_refuses_until_reopened(tmp_path):
    root = copy_spend_tree(tmp_path)
    worker = ScanWorker(claude_root=root)
    worker.close()
    with pytest.raises(ScanWorkerError):
        worker.refresh()
    worker.open()
    try:
        assert worker.refresh().files_total == 3
    finally:
        worker.close()


def test_child_exits_when_its_stdin_closes(tmp_path):
    proc = subprocess.Popen([sys.executable, "-m", "smartpanel.claude_stats.scan_worker"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            env=scan_worker._child_env())
    proc.stdin.close()
    assert proc.wait(timeout=10) == 0
    proc.stdout.close()


def test_close_during_a_refresh_returns_promptly_and_fails_that_refresh(tmp_path):
    import threading
    import time
    # A child that accepts the request and never answers: a scan in progress.
    cmd = [sys.executable, "-c",
           "import sys, time, pickle; pickle.load(sys.stdin.buffer); time.sleep(60)"]
    worker = ScanWorker(claude_root=tmp_path, command=cmd)
    errors = []

    def run():
        try:
            worker.refresh()
        except ScanWorkerError as e:
            errors.append(e)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.3)
    started = time.monotonic()
    worker.close()
    t.join(timeout=5)
    assert time.monotonic() - started < 3
    assert not t.is_alive() and len(errors) == 1
