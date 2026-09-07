"""When the main player is judged to have stopped advancing."""
from __future__ import annotations

from fun_time_vr.playback_watch import STALLED, STILL_STALLED, PlaybackWatch


def _run(watch, *ticks):
    """Feed ``(position_ms, playing, now)`` triples; collect the verdicts."""
    return [watch.note(position_ms=p, playing=q, now=t) for p, q, t in ticks]


class TestWhileItIsMoving:
    def test_a_player_that_advances_says_nothing(self):
        watch = PlaybackWatch(seconds=5.0)

        assert _run(watch, (0, True, 0.0), (500, True, 5.0), (1000, True, 10.0)) == [
            None, None, None]

    def test_a_paused_player_is_not_stalled(self):
        """Genau mode is the main player holding one frame on purpose, and so is
        omnipause; neither is the failure this watches for."""
        watch = PlaybackWatch(seconds=5.0)

        assert _run(watch, (900, False, 0.0), (900, False, 30.0)) == [None, None]

    def test_a_player_with_nothing_open_is_not_stalled(self):
        """The caller passes playing=False until a duration is known, so an open
        that takes a while is not a freeze."""
        watch = PlaybackWatch(seconds=5.0)

        assert _run(watch, (0, False, 0.0), (0, False, 60.0), (0, True, 61.0)) == [
            None, None, None]

    def test_a_short_hitch_is_not_a_stall(self):
        watch = PlaybackWatch(seconds=5.0)

        assert _run(watch, (900, True, 0.0), (900, True, 4.9)) == [None, None]


class TestWhenItStops:
    def test_a_frozen_position_is_called_once(self):
        watch = PlaybackWatch(seconds=5.0)

        assert _run(watch, (900, True, 0.0), (900, True, 5.0), (900, True, 5.5)) == [
            None, STALLED, None]

    def test_the_second_verdict_waits_a_full_window_for_the_reopen(self):
        """The caller reopens the file on STALLED; saying it again before that
        has had a chance to take hold would report a recovery that never ran."""
        watch = PlaybackWatch(seconds=5.0)

        assert _run(
            watch, (900, True, 0.0), (900, True, 5.0), (900, True, 9.0), (900, True, 10.0),
        ) == [None, STALLED, None, STILL_STALLED]

    def test_it_gives_up_after_saying_so_twice(self):
        watch = PlaybackWatch(seconds=5.0)
        _run(watch, (900, True, 0.0), (900, True, 5.0), (900, True, 10.0))

        assert _run(watch, (900, True, 15.0), (900, True, 100.0)) == [None, None]

    def test_moving_again_clears_it(self):
        watch = PlaybackWatch(seconds=5.0)
        _run(watch, (900, True, 0.0), (900, True, 5.0))

        assert _run(watch, (1400, True, 6.0), (1400, True, 11.0)) == [None, STALLED]

    def test_a_pause_clears_it(self):
        watch = PlaybackWatch(seconds=5.0)
        _run(watch, (900, True, 0.0), (900, True, 5.0))

        assert _run(watch, (900, False, 6.0), (900, True, 7.0), (900, True, 12.0)) == [
            None, None, STALLED]
