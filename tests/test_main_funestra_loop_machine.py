"""What a loop asks of the Funestra, once the state machine has settled it.

`main_funestra.loop_controller` decides where the bounds go; its own tests cover that and
are not repeated here.  These are about the half that has a Funestra: the range
mpv is handed, the playhead landing on its start, the range cleared again, the
device taken back when a loop is left, the mark a Funestra is told to keep, and
what each tick of the playhead does to a mark that is still open.
"""
from __future__ import annotations

from funestra.funscript import Funscript
from funestra.modes import LoopState

from main_funestra.loop_machine import LoopMachine


def _funscript() -> Funscript:
    """Base actions (pos >= 95) at 0, 2000 and 4000: what a mark of 2500..3500
    snaps out to."""
    return Funscript(actions=[
        (0, 100), (1000, 0), (2000, 100), (3000, 0), (4000, 100),
    ])


class FakePlayback:
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
    """A `LoopMachine` over a fake Funestra, plus what it asked of its owner."""
    playback = FakePlayback()
    seeks: list[float] = []
    takeovers: list[int] = []
    marks: list[int | None] = []
    loops = LoopMachine(
        playback,
        seek_to=seeks.append,
        take_the_device_over=lambda: takeovers.append(1),
        mark=marks.append,
    )
    loops.open(_funscript() if scripted else None)
    marks.clear()
    return loops, playback, seeks, takeovers, marks


class TestOpeningAVideo:
    def test_it_starts_idle_with_no_range_on_the_funestra(self):
        loops, playback, _seeks, _takeovers, _marks = _loops()

        assert loops.state is LoopState.NORMAL
        assert playback.ab_loop is None

    def test_it_clears_a_range_the_last_video_left_running(self):
        loops, playback, _seeks, _takeovers, _marks = _loops()
        loops.restore(2000, 4000)

        loops.open(None)

        assert playback.ab_loop is None
        assert loops.state is LoopState.NORMAL

    def test_it_drops_a_mark_the_last_video_left_open(self):
        loops, _playback, _seeks, _takeovers, marks = _loops()
        loops.record_down(2500)

        loops.open(None)

        assert marks == [2500, None]


class TestTheRecordGesture:
    def test_a_settled_loop_is_handed_to_mpv_snapped_to_the_bases(self):
        loops, playback, seeks, _takeovers, _marks = _loops()

        loops.record_down(2500)
        assert loops.state is LoopState.RECORDING

        loops.record_up(3500)

        assert loops.state is LoopState.LOOPING
        assert playback.ab_loop == (2000, 4000)
        assert seeks[-1] == 2000, "the playhead lands on the loop's start"

    def test_an_unscripted_video_loops_the_raw_range(self):
        """Clips can be recorded without a funscript; only the snapping is
        funscript-gated."""
        loops, playback, _seeks, _takeovers, _marks = _loops(scripted=False)

        loops.record_down(2500)
        loops.record_up(3500)

        assert playback.ab_loop == (2500, 3500)

    def test_an_out_point_before_the_start_floors_to_the_start(self):
        loops, playback, seeks, _takeovers, _marks = _loops(scripted=False)
        loops.record_down(5000)

        loops.record_up(2000)

        assert playback.ab_loop == (5000, 5500)
        assert seeks[-1] == 5000

    def test_letting_go_without_having_pressed_asks_for_nothing(self):
        loops, playback, seeks, _takeovers, marks = _loops()

        loops.record_up(3500)

        assert (loops.state, playback.ab_loop, seeks, marks) == (LoopState.NORMAL, None, [], [])

    def test_the_in_point_is_kept_only_while_the_mark_is_open(self):
        loops, _playback, _seeks, _takeovers, marks = _loops()
        assert marks == []

        loops.record_down(2500)
        assert marks == [2500]

        loops.record_up(3500)
        assert marks == [2500, None], "looping now, not marking"

    def test_the_funestra_is_told_where_the_mark_starts_and_when_it_closes(self):
        """A Playback floors its seeks at the mark, so it hears of the mark the
        moment it opens and again the moment it closes."""
        loops, _playback, _seeks, _takeovers, marks = _loops()

        loops.record_down(2500)
        loops.record_up(3500)

        assert marks == [2500, None]


class TestTheOneButtonGesture:
    def test_the_first_tap_opens_a_mark(self):
        loops, _playback, _seeks, _takeovers, marks = _loops()

        loops.record_tap(2500)

        assert (loops.marking, marks) == (True, [2500])

    def test_the_second_closes_the_loop_and_starts_it(self):
        loops, playback, seeks, _takeovers, _marks = _loops()
        loops.record_tap(2500)

        loops.record_tap(3500)

        assert (loops.running, playback.ab_loop, seeks[-1]) == (True, (2000, 4000), 2000)

    def test_the_third_drops_it(self):
        loops, playback, _seeks, takeovers, _marks = _loops()
        loops.record_tap(2500)
        loops.record_tap(3500)
        takeovers.clear()

        loops.record_tap(3000)

        assert (loops.state, playback.ab_loop, takeovers) == (LoopState.NORMAL, None, [1])


class TestTheBoundsItPublishes:
    def test_nothing_while_no_loop_is_running(self):
        loops, _playback, _seeks, _takeovers, _marks = _loops()
        assert loops.bounds is None

        loops.record_down(2500)
        assert loops.bounds is None, "a mark in progress is not a loop yet"

    def test_the_snapped_pair_while_one_is(self):
        loops, _playback, _seeks, _takeovers, _marks = _loops()
        loops.record_down(2500)
        loops.record_up(3500)

        assert loops.bounds == (2000, 4000)


class TestPuttingALoopBack:
    def test_the_finished_bounds_go_straight_to_mpv_and_the_playhead(self):
        loops, playback, seeks, _takeovers, _marks = _loops()

        loops.restore(2000, 4000)

        assert loops.state is LoopState.LOOPING
        assert (loops.bounds, playback.ab_loop, seeks[-1]) == (
            (2000, 4000), (2000, 4000), 2000)

    def test_an_empty_range_is_no_loop_to_put_back(self):
        loops, playback, seeks, _takeovers, _marks = _loops()

        loops.restore(0, 0)
        loops.restore(4000, 2000)

        assert (loops.state, playback.ab_loop, seeks) == (LoopState.NORMAL, None, [])


class TestLeavingALoop:
    def test_cancelling_clears_the_range_and_takes_the_device_back(self):
        loops, playback, _seeks, takeovers, _marks = _loops()
        loops.restore(2000, 4000)
        takeovers.clear()

        loops.cancel()

        assert (loops.state, playback.ab_loop, takeovers) == (LoopState.NORMAL, None, [1])

    def test_cancelling_an_open_mark_drops_it_and_takes_nothing_back(self):
        loops, playback, _seeks, takeovers, marks = _loops()
        loops.record_down(2500)

        loops.cancel()

        assert (loops.state, playback.ab_loop, takeovers, marks) == (LoopState.NORMAL, None, [], [2500, None])

    def test_pressing_record_again_leaves_it_the_same_way(self):
        loops, playback, _seeks, takeovers, _marks = _loops()
        loops.restore(2000, 4000)
        takeovers.clear()

        loops.record_down(2500)

        assert (loops.state, playback.ab_loop, takeovers) == (LoopState.NORMAL, None, [1])

    def test_cancelling_when_nothing_is_running_asks_for_nothing(self):
        loops, playback, _seeks, takeovers, _marks = _loops()
        clears_before = playback.clears

        loops.cancel()

        assert (loops.state, takeovers) == (LoopState.NORMAL, [])
        assert playback.clears == clears_before, "no range to clear"


class TestWhatEachTickOfThePlayheadDoes:
    """`observe` is the half of a loop no gesture can say: a mark that ran to the
    end of the file.  A clock that jumped is the Funestra's own to notice."""

    def test_a_tick_while_nothing_is_marked_or_running_asks_for_nothing(self):
        loops, _playback, _seeks, takeovers, _marks = _loops()

        assert loops.observe(1000) is False
        assert loops.observe(2000) is False
        assert takeovers == []

    def test_a_mark_that_reaches_the_end_of_the_file_closes_and_starts_there(self):
        loops, playback, seeks, _takeovers, _marks = _loops(scripted=False)
        playback.duration_ms = 10_000.0
        loops.record_down(9_000)

        assert loops.observe(9_950) is True
        assert (loops.running, playback.ab_loop, seeks[-1]) == (True, (9_000, 9_950), 9_000)

    def test_a_mark_the_wrap_beat_closes_where_the_playhead_last_was(self):
        loops, playback, seeks, _takeovers, _marks = _loops(scripted=False)
        playback.duration_ms = 10_000.0
        loops.record_down(9_000)
        loops.observe(9_800)

        assert loops.observe(20) is True
        assert (playback.ab_loop, seeks[-1]) == ((9_000, 9_800), 9_000)

    def test_a_backward_seek_mid_mark_goes_on_marking(self):
        loops, playback, _seeks, _takeovers, _marks = _loops(scripted=False)
        loops.record_down(30_000)
        loops.observe(40_000)

        assert loops.observe(20_000) is False
        assert (loops.marking, playback.ab_loop) == (True, None)

    def test_a_rewound_clock_is_not_the_loops_to_answer(self):
        """Every Funestra here takes the device back on a rewind itself; a second
        reset from the loop would re-arm the glide twice for one wrap."""
        loops, _playback, _seeks, takeovers, _marks = _loops()
        loops.restore(2000, 4000)
        loops.observe(3900)
        takeovers.clear()

        assert loops.observe(2000) is False
        assert takeovers == []
