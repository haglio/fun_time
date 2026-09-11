"""The loop a video is being marked into, or is running in.

:class:`main_player.loop_controller.LoopController` is the pure half — where the bounds
go, and which of the three states a gesture leaves the machine in.  This is the
half that has a player: it hands a settled range to mpv, which loops it natively
rather than by seeking, drops the playhead on its start, and clears the range
again when the loop is left.

It lives apart from :class:`main_player.session.PlayerSession` because ten of that
class's methods were this and nothing else, while the class's other three
subjects — where the playlist is, how fast and how loud it plays, and who has
the device — never read a loop bound.
"""
from __future__ import annotations

from collections.abc import Callable

from .loop_controller import LoopController, LoopState

# The word each state goes out as.  The main player publishes it in its status file and Fun
# Time reads it there, so these three strings are an orchestrator contract and
# the enum is this repo's own business.
_PUBLISHED = {
    LoopState.NORMAL: "normal",
    LoopState.MARKING: "recording",
    LoopState.LOOPING: "looping",
}


class SessionLoops:
    def __init__(
        self,
        player,
        *,
        seek_to: Callable[[float], None],
        take_the_device_over: Callable[[], None],
    ) -> None:
        self._player = player
        self._seek_to = seek_to
        self._take_the_device_over = take_the_device_over
        # Replaced by :meth:`open` before a video is on screen.  Every video has
        # one -- clips can be recorded without a funscript, and only the snapping
        # is funscript-gated -- so this is never None again.
        self._ctrl = LoopController(None)

    def open(self, funscript) -> None:
        """A new video: nothing marked, nothing running, mpv's range cleared."""
        self._ctrl = LoopController(funscript)
        self._player.clear_ab_loop()

    @property
    def marking(self) -> bool:
        return self._ctrl.state == LoopState.MARKING

    @property
    def running(self) -> bool:
        return self._ctrl.state == LoopState.LOOPING

    @property
    def idle(self) -> bool:
        return self._ctrl.state == LoopState.NORMAL

    @property
    def published_state(self) -> str:
        """The state as the shared vocabulary: normal/recording/looping."""
        return _PUBLISHED[self._ctrl.state]

    @property
    def bounds(self) -> tuple[int, int] | None:
        """Active loop (in_ms, out_ms) — None unless a loop is running."""
        if not self.running:
            return None
        return self._ctrl.in_ms, self._ctrl.out_ms

    @property
    def marked_in_ms(self) -> int | None:
        """In point of the loop being marked — None unless recording."""
        if not self.marking:
            return None
        return self._ctrl.in_ms

    def record_down(self, position_ms: int) -> None:
        was_running = self.running
        self._ctrl.on_record_down(position_ms)
        if was_running:
            self._leave()

    def record_up(self, position_ms: int) -> None:
        if not self.marking:
            return
        self.finish_at(position_ms)

    def finish_at(self, out_ms: int) -> None:
        """Close the marked loop at *out_ms* and start mpv's native A/B loop."""
        self._ctrl.on_record_up(out_ms)
        if self.running:
            self._enter()

    def restore(self, in_ms: int, out_ms: int) -> None:
        """Put the video back into a loop it was left running in.

        The loop outlives the session that marked it: an orchestrator reads the
        bounds off the status file this session publishes and hands them back on
        the command channel next launch, over the video the playlist was resumed
        onto.  The bounds are already finished ones, so no gesture is replayed
        and nothing is snapped again.

        An empty range is no loop — that is what the status file says when
        nothing is looping — and is left alone rather than turned into a loop
        with nothing in it.
        """
        if out_ms <= in_ms:
            return
        self._ctrl.restore(in_ms, out_ms)
        self._enter()

    def cancel(self) -> None:
        was_running = self.running
        self._ctrl.cancel()
        if was_running:
            self._leave()

    def _enter(self) -> None:
        """Hand the settled loop to mpv and drop the playhead on its start.

        mpv loops the A/B range natively (smooth, no seek stutter).  The jump
        goes through the session's seek so it survives a file that is still
        opening, which is the case for a loop restored the moment a session
        launches.
        """
        self._player.set_ab_loop(self._ctrl.in_ms, self._ctrl.out_ms)
        self._seek_to(self._ctrl.in_ms)

    def _leave(self) -> None:
        self._player.clear_ab_loop()
        self._take_the_device_over()
