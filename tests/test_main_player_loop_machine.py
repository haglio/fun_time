"""What a loop asks of the player, once the state machine has settled it.

`main_player.loop_controller` decides where the bounds go; its own tests cover that and
are not repeated here.  These are about the half that has a player: the range
mpv is handed, the playhead landing on its start, the range cleared again, the
device taken back when a loop is left, the mark a player is told to keep, and
what each tick of the playhead does to a mark that is still open.
"""
from __future__ import annotations

from player_core.funscript import Funscript
from player_core.modes import LoopState

from main_player.loop_machine import LoopMachine


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
        self.duration_ms = 60_000.0

    def set_ab_loop(self, in_ms: float, out_ms: float) -> None:
        self.ab_loop = (in_ms, out_ms)

    def clear_ab_loop(self) -> None:
        self.ab_loop = None
        self.clears += 1


def _loops(*, scripted: bool = True):
    """A `LoopMachine` over a fake player, plus what it asked of its owner."""
    player = FakePlayer()
    seeks: list[float] = []
    takeovers: list[int] = []
    marks: list[int | None] = []
    loops = LoopMachine(
        player,
        seek_to=seeks.append,
        take_the_device_over=lambda: takeovers.append(1),
        mark=marks.append,
    )
    loops.open(_funscript() if scripted else None)
    marks.clear()
    return loops, player, seeks, takeovers, marks


class TestOpeningAVideo:
    def test_it_starts_idle_with_no_range_on_the_player(self):
        loops, player, _seeks, _takeovers, _marks = _loops()

        assert (loops.idle, loops.state) == (True, LoopState.NORMAL)
        assert player.ab_loop is None

    def test_it_clears_a_range_the_last_video_left_running(self):
        loops, player, _seeks, _takeovers, _marks = _loops()
        loops.restore(2000, 4000)

        loops.open(None)

        assert player.ab_loop is None
        assert loops.idle

    def test_it_drops_a_mark_the_last_video_left_open(self):
        loops, _player, _seeks, _takeovers, marks = _loops()
        loops.record_down(2500)

        loops.open(None)

        assert marks == [2500, None]


class TestTheRecordGesture:
    def test_a_settled_loop_is_handed_to_mpv_snapped_to_the_bases(self):
        loops, player, seeks, _takeovers, _marks = _loops()

        loops.record_down(2500)
        assert loops.state is LoopState.RECORDING

        loops.record_up(3500)

        assert loops.state is LoopState.LOOPING
        assert player.ab_loop == (2000, 4000)
        assert seeks[-1] == 2000, "the playhead lands on the loop's start"

    def test_an_unscripted_video_loops_the_raw_range(self):
        """Clips can be recorded without a funscript; only the snapping is
        funscript-gated."""
        loops, player, _seeks, _takeovers, _marks = _loops(scripted=False)

        loops.record_down(2500)
        loops.record_up(3500)

        assert player.ab_loop == (2500, 3500)

    def test_an_out_point_before_the_start_floors_to_the_start(self):
        loops, player, seeks, _takeovers, _marks = _loops(scripted=False)
        loops.record_down(5000)

        loops.record_up(2000)

        assert player.ab_loop == (5000, 5500)
        assert seeks[-1] == 5000

    def test_letting_go_without_having_pressed_asks_for_nothing(self):
        loops, player, seeks, _takeovers, marks = _loops()

        loops.record_up(3500)

        assert (loops.idle, player.ab_loop, seeks, marks) == (True, None, [], [])

    def test_the_in_point_shows_only_while_the_mark_is_open(self):
        loops, _player, _seeks, _takeovers, _marks = _loops()
        assert loops.marked_in_ms is None

        loops.record_down(2500)
        assert loops.marked_in_ms == 2500

        loops.record_up(3500)
        assert loops.marked_in_ms is None, "looping now, not marking"

    def test_the_player_is_told_where_the_mark_starts_and_when_it_closes(self):
        """A Playback floors its seeks at the mark, so it hears of the mark the
        moment it opens and again the moment it closes."""
        loops, _player, _seeks, _takeovers, marks = _loops()

        loops.record_down(2500)
        loops.record_up(3500)

        assert marks == [2500, None]


class TestTheOneButtonGesture:
    def test_the_first_tap_opens_a_mark(self):
        loops, _player, _seeks, _takeovers, marks = _loops()

        loops.record_tap(2500)

        assert (loops.marking, loops.marked_in_ms, marks) == (True, 2500, [2500])

    def test_the_second_closes_the_loop_and_starts_it(self):
        loops, player, seeks, _takeovers, _marks = _loops()
        loops.record_tap(2500)

        loops.record_tap(3500)

        assert (loops.running, player.ab_loop, seeks[-1]) == (True, (2000, 4000), 2000)

    def test_the_third_drops_it(self):
        loops, player, _seeks, takeovers, _marks = _loops()
        loops.record_tap(2500)
        loops.record_tap(3500)
        takeovers.clear()

        loops.record_tap(3000)

        assert (loops.idle, player.ab_loop, takeovers) == (True, None, [1])


class TestTheBoundsItPublishes:
    def test_nothing_while_no_loop_is_running(self):
        loops, _player, _seeks, _takeovers, _marks = _loops()
        assert loops.bounds is None

        loops.record_down(2500)
        assert loops.bounds is None, "a mark in progress is not a loop yet"

    def test_the_snapped_pair_while_one_is(self):
        loops, _player, _seeks, _takeovers, _marks = _loops()
        loops.record_down(2500)
        loops.record_up(3500)

        assert loops.bounds == (2000, 4000)


class TestPuttingALoopBack:
    def test_the_finished_bounds_go_straight_to_mpv_and_the_playhead(self):
        loops, player, seeks, _takeovers, _marks = _loops()

        loops.restore(2000, 4000)

        assert loops.state is LoopState.LOOPING
        assert (loops.bounds, player.ab_loop, seeks[-1]) == (
            (2000, 4000), (2000, 4000), 2000)

    def test_an_empty_range_is_no_loop_to_put_back(self):
        loops, player, seeks, _takeovers, _marks = _loops()

        loops.restore(0, 0)
        loops.restore(4000, 2000)

        assert (loops.idle, player.ab_loop, seeks) == (True, None, [])


class TestLeavingALoop:
    def test_cancelling_clears_the_range_and_takes_the_device_back(self):
        loops, player, _seeks, takeovers, _marks = _loops()
        loops.restore(2000, 4000)
        takeovers.clear()

        loops.cancel()

        assert (loops.idle, player.ab_loop, takeovers) == (True, None, [1])

    def test_cancelling_an_open_mark_drops_it_and_takes_nothing_back(self):
        loops, player, _seeks, takeovers, marks = _loops()
        loops.record_down(2500)

        loops.cancel()

        assert (loops.idle, player.ab_loop, takeovers, marks) == (True, None, [], [2500, None])

    def test_pressing_record_again_leaves_it_the_same_way(self):
        loops, player, _seeks, takeovers, _marks = _loops()
        loops.restore(2000, 4000)
        takeovers.clear()

        loops.record_down(2500)

        assert (loops.idle, player.ab_loop, takeovers) == (True, None, [1])

    def test_cancelling_when_nothing_is_running_asks_for_nothing(self):
        loops, player, _seeks, takeovers, _marks = _loops()
        clears_before = player.clears

        loops.cancel()

        assert (loops.idle, takeovers) == (True, [])
        assert player.clears == clears_before, "no range to clear"


class TestWhatEachTickOfThePlayheadDoes:
    """`observe` is the half of a loop no gesture can say: a mark that ran to the
    end of the file.  A clock that jumped is the player's own to notice."""

    def test_a_tick_while_nothing_is_marked_or_running_asks_for_nothing(self):
        loops, _player, _seeks, takeovers, _marks = _loops()

        assert loops.observe(1000) is False
        assert loops.observe(2000) is False
        assert takeovers == []

    def test_a_mark_that_reaches_the_end_of_the_file_closes_and_starts_there(self):
        loops, player, seeks, _takeovers, _marks = _loops(scripted=False)
        player.duration_ms = 10_000.0
        loops.record_down(9_000)

        assert loops.observe(9_950) is True
        assert (loops.running, player.ab_loop, seeks[-1]) == (True, (9_000, 9_950), 9_000)

    def test_a_mark_the_wrap_beat_closes_where_the_playhead_last_was(self):
        loops, player, seeks, _takeovers, _marks = _loops(scripted=False)
        player.duration_ms = 10_000.0
        loops.record_down(9_000)
        loops.observe(9_800)

        assert loops.observe(20) is True
        assert (player.ab_loop, seeks[-1]) == ((9_000, 9_800), 9_000)

    def test_a_backward_seek_mid_mark_goes_on_marking(self):
        loops, player, _seeks, _takeovers, _marks = _loops(scripted=False)
        loops.record_down(30_000)
        loops.observe(40_000)

        assert loops.observe(20_000) is False
        assert (loops.marking, player.ab_loop) == (True, None)

    def test_a_rewound_clock_is_not_the_loops_to_answer(self):
        """Every player here takes the device back on a rewind itself; a second
        reset from the loop would re-arm the glide twice for one wrap."""
        loops, _player, _seeks, takeovers, _marks = _loops()
        loops.restore(2000, 4000)
        loops.observe(3900)
        takeovers.clear()

        assert loops.observe(2000) is False
        assert takeovers == []
