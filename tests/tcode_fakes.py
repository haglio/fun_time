from __future__ import annotations

from player_core.funscript import Funscript


class FakeTCode:
    def __init__(self) -> None:
        self.updates: list[tuple[int, Funscript, float]] = []
        self.parks = 0
        self.resets = 0
        self.closed = False

    def update(self, position_ms: int, fs: Funscript, *, speed: float = 1.0) -> None:
        self.updates.append((position_ms, fs, speed))

    def park(self) -> None:
        self.parks += 1

    def reset(self) -> None:
        self.resets += 1

    def close(self) -> None:
        self.closed = True
