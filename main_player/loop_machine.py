"""The loop a video is being marked into, or is running in.

:class:`main_player.loop_controller.LoopController` is the pure half -- where the bounds
go, and which of the three states a gesture leaves the machine in.  This is the
half that has a player: it hands a settled range to mpv, which loops it natively
rather than by seeking, drops the playhead on its start, clears the range again
when the loop is left, and reads the clock each tick to catch the one thing a
gesture cannot say -- a mark that ran to the end of the file.

Kino owns one, on the Main Funestra of the desktop and of the headset alike,
whose Playback is the player here.  *mark* is told where a stretch starts to
be marked out, and None when the mark closes or drops, so a player that keeps
the mark (a Playback floors its seeks at it) hears of it.
"""
from __future__ import annotations

from collections.abc import Callable

from player_core.modes import LoopState
from player_core.scripted_device import REWIND_MS

from .loop_controller import LoopController

_EOF_WRAP_START_MS = 250
_EOF_MARGIN_MS = 100


def _nobody_keeps_the_mark(_in_ms: int | None) -> None:
    pass


class LoopMachine:
    def __init__(
        self,
        player,
        *,
        seek_to: Callable[[float], object],
        take_the_device_over: Callable[[], None],
        mark: Callable[[int | None], None] = _nobody_keeps_the_mark,
    ) -> None:
        self._player = player
        self._seek_to = seek_to
        self._take_the_device_over = take_the_device_over
        self._mark = mark
        self._ctrl = LoopController(None)
        self._last_pos_ms = 0.0

    def open(self, funscript) -> None:
        self._ctrl = LoopController(funscript)
        self._last_pos_ms = 0.0
        self._mark(None)
        self._player.clear_ab_loop()

    @property
    def marking(self) -> bool:
        return self._ctrl.state == LoopState.RECORDING

    @property
    def running(self) -> bool:
        return self._ctrl.state == LoopState.LOOPING

    @property
    def state(self) -> LoopState:
        return self._ctrl.state

    @property
    def bounds(self) -> tuple[int, int] | None:
        if not self.running:
            return None
        return self._ctrl.in_ms, self._ctrl.out_ms

    def observe(self, position_ms: float) -> bool:
        previous_ms, self._last_pos_ms = self._last_pos_ms, position_ms
        if not self.marking:
            return False
        duration_ms = self._player.duration_ms
        near_end = duration_ms > 0 and position_ms >= duration_ms - _EOF_MARGIN_MS
        rewound = position_ms + REWIND_MS < previous_ms
        if near_end or (rewound and position_ms < _EOF_WRAP_START_MS):
            self.finish_at(int(position_ms if near_end else previous_ms))
            return True
        return False

    def record_down(self, position_ms: int) -> None:
        was_running = self.running
        self._ctrl.on_record_down(position_ms)
        if was_running:
            self._leave()
        if self.marking:
            self._mark(position_ms)

    def record_up(self, position_ms: int) -> None:
        if not self.marking:
            return
        self.finish_at(position_ms)

    def record_tap(self, position_ms: int) -> None:
        if self.marking:
            self.record_up(position_ms)
        elif self.running:
            self.cancel()
        else:
            self.record_down(position_ms)

    def finish_at(self, out_ms: int) -> None:
        self._ctrl.on_record_up(out_ms)
        self._mark(None)
        if self.running:
            self._enter()

    def restore(self, in_ms: int, out_ms: int) -> None:
        if out_ms <= in_ms:
            return
        self._ctrl.restore(in_ms, out_ms)
        self._enter()

    def cancel(self) -> None:
        was_running = self.running
        self._ctrl.cancel()
        self._mark(None)
        if was_running:
            self._leave()

    def _enter(self) -> None:
        self._player.set_ab_loop(self._ctrl.in_ms, self._ctrl.out_ms)
        self._seek_to(self._ctrl.in_ms)

    def _leave(self) -> None:
        self._player.clear_ab_loop()
        self._take_the_device_over()
