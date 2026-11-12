from __future__ import annotations

from funestra.funscript import Funscript

from main_funestra.snippet_controller import SnippetState
from main_funestra.snippet_machine import SnippetMachine


def _script_whose_bases_are_at_0_2000_and_4000() -> Funscript:
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


def _snippet(*, scripted: bool = True):
    playback = FakePlayback()
    seeks: list[float] = []
    takeovers: list[int] = []
    floors: list[int | None] = []
    snippet = SnippetMachine(
        playback,
        seek_to=seeks.append,
        take_the_device_over=lambda: takeovers.append(1),
        floor_seeks_at=floors.append,
    )
    snippet.open(_script_whose_bases_are_at_0_2000_and_4000() if scripted else None)
    floors.clear()
    return snippet, playback, seeks, takeovers, floors


class TestOpeningAVideo:
    def test_it_starts_idle_with_no_range_on_the_funestra(self):
        snippet, playback, _seeks, _takeovers, _floors = _snippet()

        assert snippet.state is SnippetState.NORMAL
        assert playback.ab_loop is None

    def test_it_clears_a_range_the_last_video_left_running(self):
        snippet, playback, _seeks, _takeovers, _floors = _snippet()
        snippet.restore(2000, 4000)

        snippet.open(None)

        assert playback.ab_loop is None
        assert snippet.state is SnippetState.NORMAL

    def test_it_drops_a_mark_the_last_video_left_open(self):
        snippet, _playback, _seeks, _takeovers, floors = _snippet()
        snippet.record_down(2500)

        snippet.open(None)

        assert floors == [2500, None]


class TestTheRecordGesture:
    def test_a_settled_snippet_is_handed_to_mpv_snapped_to_the_bases(self):
        snippet, playback, seeks, _takeovers, _floors = _snippet()

        snippet.record_down(2500)
        assert snippet.state is SnippetState.RECORDING

        snippet.record_up(3500)

        assert snippet.state is SnippetState.REPEATING
        assert playback.ab_loop == (2000, 4000)
        assert seeks[-1] == 2000, "the playhead lands on the snippet's start"

    def test_an_unscripted_video_repeats_the_raw_range(self):
        snippet, playback, _seeks, _takeovers, _floors = _snippet(scripted=False)

        snippet.record_down(2500)
        snippet.record_up(3500)

        assert playback.ab_loop == (2500, 3500)

    def test_a_release_just_after_the_file_wrapped_floors_to_the_mark(self):
        snippet, playback, seeks, _takeovers, _floors = _snippet(scripted=False)
        playback.duration_ms = 10_000.0
        snippet.record_down(9_000)
        snippet.observe(9_800)

        snippet.record_up(20)

        assert playback.ab_loop == (9_000, 9_500)
        assert seeks[-1] == 9_000

    def test_letting_go_without_having_pressed_asks_for_nothing(self):
        snippet, playback, seeks, _takeovers, floors = _snippet()

        snippet.record_up(3500)

        assert (snippet.state, playback.ab_loop, seeks, floors) == (SnippetState.NORMAL, None, [], [])

    def test_the_in_point_is_kept_only_while_the_mark_is_open(self):
        snippet, _playback, _seeks, _takeovers, floors = _snippet()
        assert floors == []

        snippet.record_down(2500)
        assert floors == [2500]

        snippet.record_up(3500)
        assert floors == [2500, None], "repeating now, not marking"

    def test_seeks_are_floored_at_the_mark_from_its_opening_until_it_closes(self):
        snippet, _playback, _seeks, _takeovers, floors = _snippet()

        snippet.record_down(2500)
        snippet.record_up(3500)

        assert floors == [2500, None]


class TestTheOneButtonGesture:
    def test_the_first_tap_opens_a_mark(self):
        snippet, _playback, _seeks, _takeovers, floors = _snippet()

        snippet.record_tap(2500)

        assert (snippet.marking, floors) == (True, [2500])

    def test_the_second_closes_the_snippet_and_starts_it(self):
        snippet, playback, seeks, _takeovers, _floors = _snippet()
        snippet.record_tap(2500)

        snippet.record_tap(3500)

        assert (snippet.running, playback.ab_loop, seeks[-1]) == (True, (2000, 4000), 2000)

    def test_the_third_drops_it(self):
        snippet, playback, _seeks, takeovers, _floors = _snippet()
        snippet.record_tap(2500)
        snippet.record_tap(3500)
        takeovers.clear()

        snippet.record_tap(3000)

        assert (snippet.state, playback.ab_loop, takeovers) == (SnippetState.NORMAL, None, [1])


class TestTheBoundsItPublishes:
    def test_nothing_while_no_loop_is_running(self):
        snippet, _playback, _seeks, _takeovers, _floors = _snippet()
        assert snippet.bounds is None

        snippet.record_down(2500)
        assert snippet.bounds is None, "a mark in progress is not a snippet yet"

    def test_the_snapped_pair_while_one_is(self):
        snippet, _playback, _seeks, _takeovers, _floors = _snippet()
        snippet.record_down(2500)
        snippet.record_up(3500)

        assert snippet.bounds == (2000, 4000)


class TestPuttingASnippetBack:
    def test_the_finished_bounds_go_straight_to_mpv_and_the_playhead(self):
        snippet, playback, seeks, _takeovers, _floors = _snippet()

        snippet.restore(2000, 4000)

        assert snippet.state is SnippetState.REPEATING
        assert (snippet.bounds, playback.ab_loop, seeks[-1]) == (
            (2000, 4000), (2000, 4000), 2000)

    def test_an_empty_range_is_no_loop_to_put_back(self):
        snippet, playback, seeks, _takeovers, _floors = _snippet()

        snippet.restore(0, 0)
        snippet.restore(4000, 2000)

        assert (snippet.state, playback.ab_loop, seeks) == (SnippetState.NORMAL, None, [])


class TestLeavingASnippet:
    def test_cancelling_clears_the_range_and_takes_the_device_back(self):
        snippet, playback, _seeks, takeovers, _floors = _snippet()
        snippet.restore(2000, 4000)
        takeovers.clear()

        snippet.cancel()

        assert (snippet.state, playback.ab_loop, takeovers) == (SnippetState.NORMAL, None, [1])

    def test_cancelling_an_open_mark_drops_it_and_takes_nothing_back(self):
        snippet, playback, _seeks, takeovers, floors = _snippet()
        snippet.record_down(2500)

        snippet.cancel()

        assert (snippet.state, playback.ab_loop, takeovers, floors) == (SnippetState.NORMAL, None, [], [2500, None])

    def test_pressing_record_again_leaves_it_the_same_way(self):
        snippet, playback, _seeks, takeovers, _floors = _snippet()
        snippet.restore(2000, 4000)
        takeovers.clear()

        snippet.record_down(2500)

        assert (snippet.state, playback.ab_loop, takeovers) == (SnippetState.NORMAL, None, [1])

    def test_cancelling_when_nothing_is_running_asks_for_nothing(self):
        snippet, playback, _seeks, takeovers, _floors = _snippet()
        clears_before = playback.clears

        snippet.cancel()

        assert (snippet.state, takeovers) == (SnippetState.NORMAL, [])
        assert playback.clears == clears_before, "no range to clear"


class TestWhatEachTickOfThePlayheadDoes:
    def test_a_tick_while_nothing_is_marked_or_running_asks_for_nothing(self):
        snippet, _playback, _seeks, takeovers, _floors = _snippet()

        assert snippet.observe(1000) is False
        assert snippet.observe(2000) is False
        assert takeovers == []

    def test_a_mark_that_reaches_the_end_of_the_file_closes_and_starts_there(self):
        snippet, playback, seeks, _takeovers, _floors = _snippet(scripted=False)
        playback.duration_ms = 10_000.0
        snippet.record_down(9_000)

        assert snippet.observe(9_950) is True
        assert (snippet.running, playback.ab_loop, seeks[-1]) == (True, (9_000, 9_950), 9_000)

    def test_a_mark_the_wrap_beat_closes_where_the_playhead_last_was(self):
        snippet, playback, seeks, _takeovers, _floors = _snippet(scripted=False)
        playback.duration_ms = 10_000.0
        snippet.record_down(9_000)
        snippet.observe(9_800)

        assert snippet.observe(20) is True
        assert (playback.ab_loop, seeks[-1]) == ((9_000, 9_800), 9_000)

    def test_a_backward_seek_mid_mark_goes_on_marking(self):
        snippet, playback, _seeks, _takeovers, _floors = _snippet(scripted=False)
        snippet.record_down(30_000)
        snippet.observe(40_000)

        assert snippet.observe(20_000) is False
        assert (snippet.marking, playback.ab_loop) == (True, None)

    def test_a_running_snippet_wraps_in_mpv_with_no_seek_from_here(self):
        snippet, _playback, seeks, _takeovers, _floors = _snippet()
        snippet.restore(2000, 4000)
        seeks.clear()

        assert snippet.observe(3900) is False
        assert snippet.observe(4100) is False
        assert seeks == []

    def test_a_wrap_asks_no_second_takeover_beside_the_playbacks_own(self):
        snippet, _playback, _seeks, takeovers, _floors = _snippet()
        snippet.restore(2000, 4000)
        snippet.observe(3900)
        takeovers.clear()

        assert snippet.observe(2000) is False
        assert takeovers == []
