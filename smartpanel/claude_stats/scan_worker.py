"""Run the transcript scan in a child process, off the app's GIL (SP-2702).

Why a process and not the worker thread alone: a cold scan is pure-Python JSON
parsing for many seconds. On a thread it holds the GIL, and every time the main
loop does I/O (psutil reads dozens of sysfs files per CPU tick) it releases the
GIL and then waits up to the switch interval to get it back. Measured with 400
synthetic transcripts: `sensors.system.cpu()` went from 2 ms to 3-5 s per call
while the scan ran. In a child process the scan has its own interpreter, and the
app's worker thread only sits in a blocking pipe read (GIL released).

`ScanWorker` is a drop-in for `Aggregator` as `SpendService` uses it: a `root`
attribute and `refresh(progress=None) -> Snapshot`, plus `close()`. The child is
long-lived and keeps its own `Aggregator`, so the per-file mtime cache (and its
memory) lives there; a warm refresh re-parses only changed files, as before.

Protocol, pickled over the child's stdin/stdout:
    parent -> child   {"root": str}                       one refresh request
    child  -> parent  ("progress", done, total)            0..n times, throttled
                      ("result", Snapshot) | ("error", str)  exactly once
The child exits on EOF on stdin, so it never outlives the app (a parent that
dies closes the pipe). If the child cannot be started, the scan falls back to
an in-process `Aggregator` rather than showing no spend at all.
"""
from __future__ import annotations

import logging
import os
import pickle
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from smartpanel.claude_stats.aggregator import Aggregator, ProgressFn, Snapshot

log = logging.getLogger(__name__)

# Child-side progress throttle; the service throttles again before touching the UI.
CHILD_PROGRESS_INTERVAL_S = 0.1
# Nice increment for the child: the scan is background work and must not compete
# with the desktop for CPU.
CHILD_NICE = 10


class ScanWorkerError(RuntimeError):
    """The child died or reported a failure; the next refresh starts a new one."""


def _child_env() -> dict[str, str]:
    """The app's environment, plus the directory holding the `smartpanel`
    package on PYTHONPATH, so the child imports the very same code even when
    the app runs from a source checkout rather than an installed package."""
    env = dict(os.environ)
    pkg_parent = str(Path(__file__).resolve().parent.parent.parent)
    current = env.get("PYTHONPATH")
    env["PYTHONPATH"] = pkg_parent + (os.pathsep + current if current else "")
    return env


class ScanWorker:
    def __init__(self, claude_root: Path | None = None, *,
                 command: list[str] | None = None):
        self.root = Path(claude_root) if claude_root else Path.home() / ".claude" / "projects"
        self._command = command or [sys.executable, "-m", "smartpanel.claude_stats.scan_worker"]
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._closed = False
        self._fallback: Aggregator | None = None

    # ── parent side ──
    def _spawn(self) -> subprocess.Popen | None:
        try:
            return subprocess.Popen(
                self._command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                env=_child_env(), close_fds=True)
        except OSError:
            log.warning("cannot start the scan process; scanning in-process", exc_info=True)
            return None

    def _child(self) -> subprocess.Popen | None:
        with self._lock:
            if self._closed:
                raise ScanWorkerError("scan worker closed")
            proc = self._proc
            if proc is None or proc.poll() is not None:
                proc = self._proc = self._spawn()
            return proc

    def refresh(self, progress: ProgressFn | None = None) -> Snapshot:
        proc = self._child()
        if proc is None:
            if self._fallback is None:
                self._fallback = Aggregator(claude_root=self.root)
            return self._fallback.refresh(progress=progress)
        try:
            pickle.dump({"root": str(self.root)}, proc.stdin)
            proc.stdin.flush()
            while True:
                msg = pickle.load(proc.stdout)      # blocks with the GIL released
                kind = msg[0]
                if kind == "progress":
                    if progress is not None:
                        try:
                            progress(msg[1], msg[2])
                        except Exception:  # noqa: BLE001
                            log.debug("progress callback failed", exc_info=True)
                elif kind == "result":
                    return msg[1]
                else:
                    raise ScanWorkerError(f"scan failed in the child: {msg[1]}")
        except (OSError, EOFError, pickle.UnpicklingError, ValueError) as e:
            self._discard(proc)
            raise ScanWorkerError(f"scan process died: {e}") from e

    def _discard(self, proc: subprocess.Popen) -> None:
        with self._lock:
            if self._proc is proc:
                self._proc = None
        _reap(proc)

    def open(self) -> None:
        """Allow refreshes again after `close()` (the child starts on demand)."""
        with self._lock:
            self._closed = False

    def close(self) -> None:
        """Stop the child. Safe from any thread; a refresh in flight fails, and
        none starts a new child until `open()`."""
        with self._lock:
            self._closed = True
            proc, self._proc = self._proc, None
        if proc is not None:
            _reap(proc)


def _reap(proc: subprocess.Popen) -> None:
    """Kill the child, then close its pipes.

    Kill first: the child only reads transcripts, so nothing is lost, and a
    worker thread may be blocked reading its stdout — closing that stream
    first would wait on the reader's lock until the scan finished.
    """
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        log.warning("scan process %s did not exit", proc.pid)
    for stream in (proc.stdin, proc.stdout):
        try:
            if stream is not None:
                stream.close()
        except (OSError, ValueError):
            pass


# ── child side ──
def serve(stdin, stdout, aggregator_factory=Aggregator) -> None:
    """Answer refresh requests until EOF. Pure I/O over two binary streams."""
    aggs: dict[str, Aggregator] = {}

    def send(msg: Any) -> None:
        pickle.dump(msg, stdout, protocol=pickle.HIGHEST_PROTOCOL)
        stdout.flush()

    while True:
        try:
            req = pickle.load(stdin)
        except EOFError:
            return
        root = req["root"]
        agg = aggs.get(root)
        if agg is None:
            agg = aggs[root] = aggregator_factory(claude_root=Path(root))
        last = [0.0]

        def progress(done: int, total: int) -> None:
            now = time.monotonic()
            if done < total and now - last[0] < CHILD_PROGRESS_INTERVAL_S:
                return
            last[0] = now
            send(("progress", done, total))

        try:
            snap = agg.refresh(progress=progress)
        except Exception as e:  # noqa: BLE001 — report, keep serving
            log.exception("scan failed")
            send(("error", f"{type(e).__name__}: {e}"))
            continue
        send(("result", snap))


def main() -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    try:
        os.nice(CHILD_NICE)
    except (AttributeError, OSError):
        pass
    # Nothing else may write to stdout: it carries the protocol.
    out = sys.stdout.buffer
    sys.stdout = sys.stderr
    try:
        serve(sys.stdin.buffer, out)
    except (BrokenPipeError, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    main()
