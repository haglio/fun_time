from __future__ import annotations

REWIND_MS = 50


class _Unconnected:
    def update(self, position_ms: int, script, *, speed: float = 1.0) -> None:
        pass

    def park(self) -> None:
        pass

    def reset(self) -> None:
        pass

    def close(self) -> None:
        pass


class ScriptedDevice:
    def __init__(self, tcode=None, *, enabled: bool = True) -> None:
        self._tcode = _Unconnected() if tcode is None else tcode
        self._enabled = enabled

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
