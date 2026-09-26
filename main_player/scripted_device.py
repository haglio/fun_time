from __future__ import annotations

REWIND_MS = 50


class ScriptedDevice:
    def __init__(self, tcode) -> None:
        self._tcode = tcode
        self._enabled = True

    def set_enabled(self, enabled: bool) -> None:
        if enabled and not self._enabled:
            self.take_over()
        self._enabled = enabled

    def take_over(self) -> None:
        self._tcode.reset()

    def drive(self, position_ms: float, script, *, speed: float) -> None:
        if not self._enabled:
            return
        if script is not None:
            self._tcode.update(int(position_ms), script, speed=speed)
        else:
            self._tcode.park()

    def close(self) -> None:
        self._tcode.close()
