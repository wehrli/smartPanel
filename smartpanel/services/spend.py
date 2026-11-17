"""Claude spend service: aggregator on a worker thread -> Store `spend` + app.loading/first_run.

CONTRACT STUB (wave 0). WP-spend implements; API fixed:
    SpendService(store, config, claude_root=None).start() / .stop() / .refresh_now()
    SpendService.session_cost(transcript_path) -> float | None   # cached per-file cost (E14)
"""
from __future__ import annotations


class SpendService:
    def __init__(self, store, config, claude_root=None):
        self.store, self.config, self.claude_root = store, config, claude_root

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def refresh_now(self) -> None:
        pass

    def session_cost(self, transcript_path) -> float | None:
        return None
