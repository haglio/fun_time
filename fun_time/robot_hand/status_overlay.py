from __future__ import annotations

import time


class StatusOverlayController:
    def __init__(self, *, hide_delay_ms: int, can_hide, now_source=time.monotonic):
        self.hide_delay_ms = hide_delay_ms
        self.can_hide = can_hide
        self._now = now_source
        self._visible = False
        self._hide_at: float | None = None

    @property
    def visible(self) -> bool:
        if self._hide_at is not None and self._now() >= self._hide_at:
            self._hide_at = None
            if self.can_hide():
                self._visible = False
        return self._visible

    def show(self) -> None:
        self._visible = True

    def hide(self) -> None:
        if self.can_hide():
            self._visible = False
            self._hide_at = None

    def schedule_hide(self) -> None:
        self._hide_at = self._now() + self.hide_delay_ms / 1000.0

    def on_mouse_motion(self, _event=None) -> None:
        self.show()
        self.schedule_hide()

    def on_mouse_leave(self, _event=None) -> None:
        self.hide()
