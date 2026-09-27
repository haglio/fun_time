"""The loop a video is being marked into, or is running in.

:class:`main_player.loop_controller.LoopController` is the pure half — where the bounds
go, and which of the three states a gesture leaves the machine in.  This is the
half that has a player: it hands a settled range to mpv, which loops it natively
rather than by seeking, drops the playhead on its start, clears the range again
when the loop is left, and reads the clock each tick to catch the two things a
gesture cannot say — a mark that ran to the end of the file, and a wrap.

Both main players own one: :class:`main_player.session.PlayerSession` on the
desktop and :class:`fun_time_vr.roles.MainRole` in the headset, which is why it
lives here rather than inside either of them.  On the desktop it is also what
kept ten of ``PlayerSession``'s methods from being this and nothing else, while
that class's other three subjects — where the playlist is, how fast and how loud
it plays, and who has the device — never read a loop bound.
"""
from __future__ import annotations

from collections.abc import Callable

from player_core.modes import LoopState

from .loop_controller import LoopController
from .scripted_device import REWIND_MS

# A rewind that lands within this of zero is the file wrapping at EOF (mpv
# loop-file=inf restarts at 0), as opposed to a seek back to some interior point.
_EOF_WRAP_START_MS = 250

# While marking a loop, close it once the playhead comes within this of the
# file end — proactively, so mpv's A/B loop takes over before loop-file wraps
# the whole video to the start and flashes the opening frames.  Wide enough that
# a tick reliably lands inside it at 60 fps, small enough to still feel instant.
_EOF_MARGIN_MS = 100


class LoopMachine:
    def __init__(
        self,
        player,
        *,
        seek_to: Callable[[float], object],
        take_the_device_over: Callable[[], None],
    ) -> None:
        self._player = player
        self._seek_to = seek_to
        self._take_the_device_over = take_the_device_over
        # Replaced by :meth:`open` before a video is on screen.  Every video has
        # one -- clips can be recorded without a funscript, and only the snapping
        # is funscript-gated -- so this is never None again.
        self._ctrl = LoopController(None)
        self._last_pos_ms = 0.0

    def open(self, funscript) -> None:
        """A new video: nothing marked, nothing running, mpv's range cleared."""
        self._ctrl = LoopController(funscript)
        self._last_pos_ms = 0.0
        self._player.clear_ab_loop()

    @property
    def marking(self) -> bool:
        return self._ctrl.state == LoopState.RECORDING

    @property
    def running(self) -> bool:
        return self._ctrl.state == LoopState.LOOPING

    @property
    def idle(self) -> bool:
        return self._ctrl.state == LoopState.NORMAL

    @property
    def state(self) -> LoopState:
        return self._ctrl.state

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

    def observe(self, position_ms: float) -> bool:
        """What this tick's playhead does to the loop; True when the tick is over.

        A mark that reached the end of the file closes there and starts, which
        moves the playhead — so nothing else in the tick is owed the old position
        and the caller stops.  Otherwise a rewound clock is a jump the device
        knows nothing about, whichever of the three wraps it: mpv's A/B loop
        going B->A, loop-file restarting a held video, or a backward seek.
        """
        previous_ms, self._last_pos_ms = self._last_pos_ms, position_ms
        rewound = position_ms + REWIND_MS < previous_ms
        if self.marking:
            duration_ms = self._player.duration_ms
            near_end = duration_ms > 0 and position_ms >= duration_ms - _EOF_MARGIN_MS
            # near_end fires just before loop-file (inf) wraps the whole video to
            # the start, so the A/B loop takes over without the opening frames
            # flashing; the wrap below is the fallback if a tick only lands after
            # it.  Either way the out point stays just short of the file end,
            # which mpv loops cleanly.
            if near_end or (rewound and position_ms < _EOF_WRAP_START_MS):
                self.finish_at(int(position_ms if near_end else previous_ms))
                return True
        elif rewound:
            self._take_the_device_over()
        return False

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
        goes through the player's seek so it survives a file that is still
        opening, which is the case for a loop restored the moment a session
        launches.
        """
        self._player.set_ab_loop(self._ctrl.in_ms, self._ctrl.out_ms)
        self._seek_to(self._ctrl.in_ms)

    def _leave(self) -> None:
        self._player.clear_ab_loop()
        self._take_the_device_over()
