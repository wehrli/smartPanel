"""Fixed-size metric history (SP-0006). Oldest first."""
from __future__ import annotations

from collections import deque
from typing import Iterable

DEFAULT_SIZE = 300  # 15 min at a 3 s poll


class RingBuffer:
    def __init__(self, size: int = DEFAULT_SIZE, values: Iterable[float] = ()):
        if size < 1:
            raise ValueError("size must be >= 1")
        self._d: deque[float] = deque(values, maxlen=size)

    def push(self, value: float | None) -> None:
        if value is not None:
            self._d.append(float(value))

    def last(self, n: int) -> list[float]:
        return list(self._d)[-n:] if n > 0 else []

    def values(self) -> list[float]:
        return list(self._d)

    def __len__(self) -> int:
        return len(self._d)
