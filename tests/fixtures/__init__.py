"""Shared test fixtures. `copy_spend_tree` stages the realistic transcript tree in
tests/fixtures/spend/ under tmp_path with deterministic mtimes."""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SPEND_DIR = Path(__file__).parent / "spend"
PARIS = ZoneInfo("Europe/Paris")
# 2026-11-17 14:32 in Paris (CET, UTC+1).
SPEND_NOW = datetime(2026, 11, 17, 13, 32, tzinfo=timezone.utc)

ORIGINAL = "-home-user-dev-omdev-ledgerly/aaaa1111-0000-4000-8000-000000000001.jsonl"
RESUMED = "-home-user-dev-omdev-ledgerly/bbbb2222-0000-4000-8000-000000000002.jsonl"
OTHER = "-home-user-dev-omdev-smartPanel/cccc3333-0000-4000-8000-000000000003.jsonl"

# mtime = last line written. The resumed copy is newer than the original.
_MTIMES = {
    OTHER: datetime(2026, 11, 12, 10, 0, 5, tzinfo=timezone.utc),
    ORIGINAL: datetime(2026, 11, 16, 23, 31, tzinfo=timezone.utc),
    RESUMED: datetime(2026, 11, 17, 13, 10, 5, tzinfo=timezone.utc),
}


def copy_spend_tree(dest: Path) -> Path:
    root = dest / "projects"
    shutil.copytree(SPEND_DIR, root)
    for rel, when in _MTIMES.items():
        ns = int(when.timestamp() * 1e9)
        os.utime(root / rel, ns=(ns, ns))
    return root
