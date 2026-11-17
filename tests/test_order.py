from __future__ import annotations

from smartpanel.sessions.order import group_of, sort_sessions
from smartpanel.state.model import (
    IDLE, PHASE_JOURNALING, PHASE_LAUNCHING, RUNNING, UNKNOWN, WAITING, Session)


def S(name, activity=IDLE, created=0.0, phase="none"):
    return Session(name=name, activity=activity, created=created, phase=phase)


def test_groups_in_order_waiting_running_journaling_launching_idle():
    items = [
        S("idle", IDLE, 1),
        S("launch", UNKNOWN, 0, PHASE_LAUNCHING),
        S("journal", IDLE, 2, PHASE_JOURNALING),
        S("run", RUNNING, 3),
        S("wait", WAITING, 4),
        S("unknown", UNKNOWN, 5),
    ]
    assert [s.name for s in sort_sessions(items)] == [
        "wait", "run", "journal", "launch", "idle", "unknown"]


def test_stable_by_creation_within_a_group():
    items = [S("c", RUNNING, 30), S("a", RUNNING, 10), S("b", RUNNING, 20)]
    assert [s.name for s in sort_sessions(items)] == ["a", "b", "c"]


def test_phase_wins_over_activity():
    assert group_of(S("x", WAITING, phase=PHASE_JOURNALING)) == PHASE_JOURNALING
    assert group_of(S("x", "nonsense")) == UNKNOWN
