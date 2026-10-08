"""Sensor surfacing live Claude tmux sessions for the top bar.

Exposes cheap scalars (COUNT, NAMES) plus the activity tally (WAITING, RUNNING
and the ready-made STATUS badge) — the interactive session list and its close /
focus actions live in the dashboard's Sessions section, which reads tmux
directly. `tmux list-sessions` is a sub-10ms local call and the per-session
`capture-pane` behind the activity states is ~2.4ms each, so the 3s cache keeps
us well under the main-loop budget even at fast poll intervals.
"""
from __future__ import annotations

import time

from smartpanel.sessions import activity, tmux
from smartpanel.uiformat import sessions_status

_CACHE: dict[str, object] = {"sessions": None, "ts": 0.0}
_REFRESH_INTERVAL_S = 3.0


def invalidate() -> None:
    """Force the next sensor read to re-query tmux (call after a kill)."""
    _CACHE["ts"] = 0.0


def _live() -> list[tmux.TmuxSession]:
    now = time.monotonic()
    if _CACHE["sessions"] is None or now - float(_CACHE["ts"]) >= _REFRESH_INTERVAL_S:
        _CACHE["sessions"] = activity.annotate(tmux.list_sessions())
        _CACHE["ts"] = now
    return _CACHE["sessions"]  # type: ignore[return-value]


def sessions() -> dict[str, float | str | None]:
    live = _live()
    names = ", ".join(s.project for s in live) if live else "—"
    tally = activity.counts(live)
    return {
        "COUNT": float(len(live)),
        "NAMES": names,
        # Ready-made top-bar badge: "🧩4 ❓1 ▶2", collapsing to "🧩4" when
        # nothing is happening. Prefer this over COUNT in a top_bar_format.
        "STATUS": sessions_status(live),
        "WAITING": float(tally[activity.WAITING]),
        "RUNNING": float(tally[activity.RUNNING]),
    }
