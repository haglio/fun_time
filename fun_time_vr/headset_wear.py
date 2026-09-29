from __future__ import annotations

from fun_time.dashboard_actions import HEADSET_OFF, HEADSET_ON

SAID_AGAIN_EVERY_S = 2.0


class HeadsetWear:
    def __init__(self) -> None:
        self._worn: bool | None = None
        self._said_at = 0.0

    def said(self, worn: bool, *, now: float) -> list[str]:
        if worn == self._worn and now - self._said_at < SAID_AGAIN_EVERY_S:
            return []
        self._worn = worn
        self._said_at = now
        return [HEADSET_ON if worn else HEADSET_OFF]
