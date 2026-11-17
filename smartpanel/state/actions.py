"""The verbs every surface can invoke. Implemented once by `services.controller`
(real) and `demo.controller` (simulated); called by the tray menu, the dashboard,
the palette, and the D-Bus service.

Contract:
- Every method returns immediately (never blocks the GLib loop). Slow work runs on a
  worker thread and reports through `Store` (state changes + Events).
- Results the user should see are Events (`toast`, ...) emitted on the store, not
  return values — so the D-Bus caller and the GTK caller get identical feedback.
- Unknown session names are ignored with a toast, never an exception.
"""
from __future__ import annotations

from typing import Any, Protocol


class Actions(Protocol):
    # navigation / surfaces
    def open_dashboard(self, page: str = "") -> None: ...
    def open_palette(self) -> None: ...
    def set_hud(self, enabled: bool) -> None: ...
    def pin_ticker(self, pinned: bool) -> None: ...

    # sessions
    def focus_session(self, name: str) -> None: ...
    def retitle_session(self, name: str) -> None: ...
    def request_close(self, name: str) -> None:
        """Ask the UI to confirm (emits `confirm_close`), or close at once if the
        session is too young / journaling is off."""
    def close_session(self, name: str, journal: bool) -> None: ...
    def request_close_all(self) -> None: ...
    def close_all(self) -> None: ...
    def request_new_session(self) -> None: ...
    def new_session(self, directory: str) -> None: ...
    def answer_prompt(self, name: str, option: int, fingerprint: str) -> None: ...

    # files
    def open_path(self, path: str) -> None:
        """Open a file (journal log, receipt) with the desktop's default app.
        Asynchronous; a missing file or a failed launch toasts
        'Couldn’t open <name>' instead of raising."""

    # settings
    def set_setting(self, key: str, value: Any) -> None: ...
    def list_start_folders(self) -> list[dict]:
        """[{name, path, has_session}] for the folder picker (fast, local)."""
    def recheck_environment(self) -> None:
        """Re-test tmux / terminal availability ("Check again")."""
    def quit(self) -> None: ...
