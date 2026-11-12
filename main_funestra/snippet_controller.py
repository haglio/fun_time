from __future__ import annotations

from enum import StrEnum

from funestra.funscript import Funscript, snap_loop


class SnippetState(StrEnum):
    NORMAL = "normal"
    RECORDING = "recording"
    REPEATING = "repeating"


class SnippetController:
    def __init__(self, funscript: Funscript | None) -> None:
        self._funscript = funscript
        self._state = SnippetState.NORMAL
        self._in_ms: int | None = None
        self._out_ms: int | None = None

    @property
    def state(self) -> SnippetState:
        return self._state

    @property
    def in_ms(self) -> int | None:
        return self._in_ms

    @property
    def out_ms(self) -> int | None:
        return self._out_ms

    def on_record_down(self, position_ms: int) -> None:
        if self._state == SnippetState.NORMAL:
            self._in_ms = position_ms
            self._state = SnippetState.RECORDING
        elif self._state == SnippetState.REPEATING:
            self.cancel()

    def on_record_up(self, position_ms: int) -> None:
        if self._state != SnippetState.RECORDING:
            return
        out_ms = max(position_ms, self._in_ms)
        self._in_ms, self._out_ms = snap_loop(
            self._funscript, self._in_ms, out_ms,
        )
        self._state = SnippetState.REPEATING

    def restore(self, in_ms: int, out_ms: int) -> None:
        self._in_ms = in_ms
        self._out_ms = out_ms
        self._state = SnippetState.REPEATING

    def cancel(self) -> None:
        self._in_ms = None
        self._out_ms = None
        self._state = SnippetState.NORMAL
