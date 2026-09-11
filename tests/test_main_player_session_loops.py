"""What a loop asks of the player, once the state machine has settled it.

`main_player.loop_controller` decides where the bounds go; its own tests cover that and
are not repeated here.  These are about the half that has a player: the range
mpv is handed, the playhead landing on its start, the range cleared again, and
the device taken back whenever a loop moves the clock.
"""
from __future__ import annotations

from player_core.funscript import Funscript

from main_player.session_loops import SessionLoops


def _funscript() -> Funscript:
    """Base actions (pos >= 95) at 0, 2000 and 4000: what a mark of 2500..3500
    snaps out to."""
    return Funscript(actions=[
        (0, 100), (1000, 0), (2000, 100), (3000, 0), (4000, 100),
    ])


class FakePlayer:
    def __init__(self) -> None:
        self.ab_loop: tuple[float, float] | None = None
        self.clears = 0

    def set_ab_loop(self, in_ms: float, out_ms: float) -> None:
        self.ab_loop = (in_ms, out_ms)

    def clear_ab_loop(self) -> None:
        self.ab_loop = None
        self.clears += 1


def _loops(*, scripted: bool = True):
    """A `SessionLoops` over a fake player, plus what it asked of the session."""
    player = FakePlayer()
    seeks: list[float] = []
    takeovers: list[int] = []
    loops = SessionLoops(
        player,
        seek_to=seeks.append,
        take_the_device_over=lambda: takeovers.append(1),
    )
    loops.open(_funscript() if scripted else None)
    return loops, player, seeks, takeovers


class TestOpeningAVideo:
    def test_it_starts_idle_with_no_range_on_the_player(self):
        loops, player, _seeks, _takeovers = _loops()

        assert (loops.idle, loops.published_state) == (True, "normal")
        assert player.ab_loop is None

    def test_it_clears_a_range_the_last_video_left_running(self):
        loops, player, _seeks, _takeovers = _loops()
        loops.restore(2000, 4000)

        loops.open(None)

        assert player.ab_loop is None
        assert loops.idle


class TestTheRecordGesture:
    def test_a_settled_loop_is_handed_to_mpv_snapped_to_the_bases(self):
        loops, player, seeks, _takeovers = _loops()

        loops.record_down(2500)
        assert loops.published_state == "recording"

        loops.record_up(3500)

        assert loops.published_state == "looping"
        assert player.ab_loop == (2000, 4000)
        assert seeks[-1] == 2000, "the playhead lands on the loop's start"

    def test_an_unscripted_video_loops_the_raw_range(self):
        """Clips can be recorded without a funscript; only the snapping is
        funscript-gated."""
        loops, player, _seeks, _takeovers = _loops(scripted=False)

        loops.record_down(2500)
        loops.record_up(3500)

        assert player.ab_loop == (2500, 3500)

    def test_an_out_point_before_the_start_floors_to_the_start(self):
        """The EOF-wrap race: the loop floors to its start and is handed over as
        a minimum loop there, never flipped to [out, start]."""
        loops, player, seeks, _takeovers = _loops(scripted=False)
        loops.record_down(5000)

        loops.record_up(2000)

        assert player.ab_loop == (5000, 5500)
        assert seeks[-1] == 5000

    def test_letting_go_without_having_pressed_asks_for_nothing(self):
        loops, player, seeks, _takeovers = _loops()

        loops.record_up(3500)

        assert (loops.idle, player.ab_loop, seeks) == (True, None, [])

    def test_the_in_point_shows_only_while_the_mark_is_open(self):
        loops, _player, _seeks, _takeovers = _loops()
        assert loops.marked_in_ms is None

        loops.record_down(2500)
        assert loops.marked_in_ms == 2500

        loops.record_up(3500)
        assert loops.marked_in_ms is None, "looping now, not marking"


class TestTheBoundsItPublishes:
    def test_nothing_while_no_loop_is_running(self):
        loops, _player, _seeks, _takeovers = _loops()
        assert loops.bounds is None

        loops.record_down(2500)
        assert loops.bounds is None, "a mark in progress is not a loop yet"

    def test_the_snapped_pair_while_one_is(self):
        loops, _player, _seeks, _takeovers = _loops()
        loops.record_down(2500)
        loops.record_up(3500)

        assert loops.bounds == (2000, 4000)


class TestPuttingALoopBack:
    def test_the_finished_bounds_go_straight_to_mpv_and_the_playhead(self):
        """Reopening on the video a loop was left running over: no gesture is
        replayed, the finished bounds are simply put back and the playhead goes
        to the top of them, exactly as a record-up leaves it."""
        loops, player, seeks, _takeovers = _loops()

        loops.restore(2000, 4000)

        assert loops.published_state == "looping"
        assert (loops.bounds, player.ab_loop, seeks[-1]) == (
            (2000, 4000), (2000, 4000), 2000)

    def test_an_empty_range_is_no_loop_to_put_back(self):
        """The status file names a zero range when nothing is looping, and a
        video is never resumed into a loop it cannot play."""
        loops, player, seeks, _takeovers = _loops()

        loops.restore(0, 0)
        loops.restore(4000, 2000)

        assert (loops.idle, player.ab_loop, seeks) == (True, None, [])


class TestLeavingALoop:
    def test_cancelling_clears_the_range_and_takes_the_device_back(self):
        """The playhead is about to carry on past the out point it was being
        held inside, which the device knows nothing about."""
        loops, player, _seeks, takeovers = _loops()
        loops.restore(2000, 4000)
        takeovers.clear()

        loops.cancel()

        assert (loops.idle, player.ab_loop, takeovers) == (True, None, [1])

    def test_pressing_record_again_leaves_it_the_same_way(self):
        loops, player, _seeks, takeovers = _loops()
        loops.restore(2000, 4000)
        takeovers.clear()

        loops.record_down(2500)

        assert (loops.idle, player.ab_loop, takeovers) == (True, None, [1])

    def test_cancelling_when_nothing_is_running_asks_for_nothing(self):
        loops, player, _seeks, takeovers = _loops()
        clears_before = player.clears

        loops.cancel()

        assert (loops.idle, takeovers) == (True, [])
        assert player.clears == clears_before, "no range to clear"
