from __future__ import annotations

import time
from collections.abc import Callable

PRESS_FLASH_S = 0.2


class PressFlash:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._pressed_at: dict[str, float] = {}

    def press(self, action: str) -> None:
        self._pressed_at[action] = self._clock()

    def lit(self) -> frozenset[str]:
        now = self._clock()
        self._pressed_at = {action: at for action, at in self._pressed_at.items()
                            if now - at < PRESS_FLASH_S}
        return frozenset(self._pressed_at)

    def seconds_until_one_goes_out(self) -> float | None:
        if not self.lit():
            return None
        return min(self._pressed_at.values()) + PRESS_FLASH_S - self._clock()
