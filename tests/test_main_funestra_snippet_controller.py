from __future__ import annotations

from funestra.funscript import Funscript

from main_funestra.snippet_controller import SnippetController, SnippetState


def _make_fs():
    return Funscript(actions=[
        (0, 100), (1000, 0), (2000, 100), (3000, 0),
        (4000, 100), (5000, 0), (6000, 100),
    ])


class TestSnippetController:
    def test_initial_state_is_normal(self):
        lc = SnippetController(_make_fs())

        assert lc.state == SnippetState.NORMAL

    def test_record_down_normal_transitions_to_marking(self):
        lc = SnippetController(_make_fs())

        lc.on_record_down(2500)

        assert lc.state == SnippetState.RECORDING
        assert lc.in_ms == 2500

    def test_record_up_marking_transitions_to_repeating_with_snapped_bounds(self):
        lc = SnippetController(_make_fs())
        lc.on_record_down(2500)

        lc.on_record_up(3500)

        assert lc.state == SnippetState.REPEATING
        assert lc.in_ms == 2000
        assert lc.out_ms == 4000

    def test_record_down_repeating_transitions_to_normal(self):
        lc = SnippetController(_make_fs())
        lc.on_record_down(2500)
        lc.on_record_up(3500)

        lc.on_record_down(3000)

        assert lc.state == SnippetState.NORMAL
        assert lc.in_ms is None
        assert lc.out_ms is None

    def test_unscripted_snippet_uses_raw_bounds(self):
        lc = SnippetController(None)

        lc.on_record_down(2500)
        lc.on_record_up(3500)

        assert lc.state == SnippetState.REPEATING
        assert lc.in_ms == 2500
        assert lc.out_ms == 3500

    def test_a_release_behind_the_start_floors_to_it_rather_than_flipping_the_snippet(self):
        lc = SnippetController(None)
        lc.on_record_down(5000)

        lc.on_record_up(2000)

        assert lc.state == SnippetState.REPEATING
        assert lc.in_ms == 5000
        assert lc.out_ms == 5500

    def test_record_up_in_normal_is_noop(self):
        lc = SnippetController(_make_fs())

        lc.on_record_up(1000)

        assert lc.state == SnippetState.NORMAL

    def test_cancel_from_repeating_returns_to_normal(self):
        lc = SnippetController(_make_fs())
        lc.on_record_down(2500)
        lc.on_record_up(3500)

        lc.cancel()

        assert lc.state == SnippetState.NORMAL
        assert lc.in_ms is None
        assert lc.out_ms is None

    def test_cancel_while_marking_abandons_mark(self):
        lc = SnippetController(_make_fs())
        lc.on_record_down(2500)

        lc.cancel()

        assert lc.state == SnippetState.NORMAL
        assert lc.in_ms is None

    def test_cancel_in_normal_is_noop(self):
        lc = SnippetController(_make_fs())

        lc.cancel()

        assert lc.state == SnippetState.NORMAL

    def test_restore_lands_straight_in_a_running_snippet(self):
        lc = SnippetController(_make_fs())

        lc.restore(2000, 4000)

        assert lc.state == SnippetState.REPEATING
        assert (lc.in_ms, lc.out_ms) == (2000, 4000)

    def test_a_restored_snippet_is_not_snapped_again_to_the_script_beside_the_video_now(self):
        lc = SnippetController(_make_fs())

        lc.restore(2500, 3500)

        assert (lc.in_ms, lc.out_ms) == (2500, 3500)
