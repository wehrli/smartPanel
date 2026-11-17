"""Test doubles for the session services: a fake tmux server and a fake loop.

No real tmux, terminals or timers: the fake runner answers the tmux commands the
code issues from an in-memory table and records every keystroke.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tests.panes import pane


@dataclass
class FakeSession:
    created: int = 1000
    attached: bool = False
    title: str = ""
    cwd: str | None = "/home/u/dev/webapp"
    pane: str = field(default_factory=lambda: pane("idle_prose_question"))


class FakeTmux:
    def __init__(self, sessions: dict[str, FakeSession] | None = None, available=True):
        self.sessions = dict(sessions or {})
        self.available = available
        self.calls: list[list[str]] = []
        self.keys: list[list[str]] = []
        self.killed: list[str] = []
        self.titles: dict[str, str] = {}

    @staticmethod
    def _target(args, pane_target=True):
        t = args[args.index("-t") + 1]
        t = t[1:] if t.startswith("=") else t
        return t[:-1] if pane_target and t.endswith(":") else t

    def __call__(self, args):
        self.calls.append(list(args))
        if not self.available:
            return 127, "", "tmux not found"
        cmd = args[0]
        if cmd == "-V":
            return 0, "tmux 3.4\n", ""
        if cmd == "list-sessions":
            if not self.sessions:
                return 1, "", "no server running on /tmp/tmux-1000/default"
            out = "".join(f"{n}\t{s.created}\t{int(s.attached)}\t1\t{s.title}\n"
                          for n, s in self.sessions.items())
            return 0, out, ""
        name = self._target(args)
        s = self.sessions.get(name)
        if cmd == "capture-pane":
            return (0, s.pane, "") if s else (1, "", "can't find session")
        if cmd == "display-message":
            return (0, (s.cwd or "") + "\n", "") if s else (1, "", "can't find session")
        if cmd == "set-option":
            if s and args[-2] == "set-titles-string":
                self.titles[name] = args[-1]
            return (0, "", "") if s else (1, "", "no such session")
        if cmd == "send-keys":
            self.keys.append(list(args))
            return (0, "", "") if s else (1, "", "can't find pane")
        if cmd in ("kill-session", "has-session"):
            if s is None:
                return 1, "", "can't find session"
            if cmd == "kill-session":
                del self.sessions[name]
                self.killed.append(name)
            return 0, "", ""
        raise AssertionError(f"unexpected tmux call {args}")


class FakeLoop:
    """Runs idle callbacks and threads inline; records timers."""

    def __init__(self):
        self.timers: list[tuple[float, object]] = []
        self.cancelled: list[int] = []
        self.threads: list[str] = []

    def timeout(self, seconds, fn):
        self.timers.append((seconds, fn))
        return len(self.timers)

    def cancel(self, handle):
        self.cancelled.append(handle)

    def idle(self, fn):
        fn()

    def thread(self, fn, name="worker"):
        self.threads.append(name)
        fn()


class Clock:
    def __init__(self, now=10_000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeSpend:
    def __init__(self, costs: dict[str, float] | None = None):
        self.costs = costs or {}
        self.asked: list[str] = []

    def session_cost(self, path):
        self.asked.append(str(path))
        return self.costs.get(str(path))
