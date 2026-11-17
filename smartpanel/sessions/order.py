"""Display order for sessions (SP-1204), shared by every surface.

Needs you, running, journaling, starting, idle — the things that want your
attention first. Within a group, oldest first (stable by creation time), so a
row never jumps past its neighbours just because a tick re-read tmux.
"""
from __future__ import annotations

from typing import Iterable

from smartpanel.state.model import (
    IDLE,
    PHASE_JOURNALING,
    PHASE_LAUNCHING,
    RUNNING,
    UNKNOWN,
    WAITING,
    Session,
)

GROUPS = (WAITING, RUNNING, PHASE_JOURNALING, PHASE_LAUNCHING, IDLE, UNKNOWN)
_RANK = {g: i for i, g in enumerate(GROUPS)}


def group_of(s: Session) -> str:
    """The display group: a panel-owned phase wins over the pane's activity."""
    if s.phase in (PHASE_JOURNALING, PHASE_LAUNCHING):
        return s.phase
    return s.activity if s.activity in _RANK else UNKNOWN


def sort_key(s: Session) -> tuple:
    return (_RANK[group_of(s)], s.created or 0.0, s.name)


def sort_sessions(items: Iterable[Session]) -> list[Session]:
    return sorted(items, key=sort_key)
