"""Load the pane-capture fixtures in tests/fixtures/panes/."""
from __future__ import annotations

from pathlib import Path

PANES = Path(__file__).parent / "fixtures" / "panes"


def pane(name: str) -> str:
    return (PANES / f"{name}.txt").read_text(encoding="utf-8")


def all_panes() -> list[str]:
    return sorted(p.stem for p in PANES.glob("*.txt"))
