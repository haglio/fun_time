from __future__ import annotations

import time
from collections.abc import Callable

PRESS_FLASH_S = 0.2


def _still_lit(pressed_at: dict[str, float], now: float) -> dict[str, float]:
    return {action: at for action, at in pressed_at.items() if now - at < PRESS_FLASH_S}


class PressFlash:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._pressed_at: dict[str, float] = {}

    def press(self, action: str) -> None:
        self._pressed_at = {**self._pressed_at, action: self._clock()}

    def lit(self) -> frozenset[str]:
        self._pressed_at = _still_lit(self._pressed_at, self._clock())
        return frozenset(self._pressed_at)

    def seconds_until_one_goes_out(self) -> float | None:
        lit = self.lit()
        if not lit:
            return None
        return min(self._pressed_at[action] for action in lit) + PRESS_FLASH_S - self._clock()
