from __future__ import annotations

from funestra.funscript import Funscript

from main_funestra.loop_controller import LoopController, LoopState


def _make_fs():
    return Funscript(actions=[
        (0, 100), (1000, 0), (2000, 100), (3000, 0),
        (4000, 100), (5000, 0), (6000, 100),
    ])


class TestLoopController:
    def test_initial_state_is_normal(self):
        lc = LoopController(_make_fs())

        assert lc.state == LoopState.NORMAL

    def test_record_down_normal_transitions_to_marking(self):
        lc = LoopController(_make_fs())

        lc.on_record_down(2500)

        assert lc.state == LoopState.RECORDING
        assert lc.in_ms == 2500

    def test_record_up_marking_transitions_to_looping_with_snapped_bounds(self):
        lc = LoopController(_make_fs())
        lc.on_record_down(2500)

        lc.on_record_up(3500)

        assert lc.state == LoopState.LOOPING
        assert lc.in_ms == 2000
        assert lc.out_ms == 4000

    def test_record_down_looping_transitions_to_normal(self):
        lc = LoopController(_make_fs())
        lc.on_record_down(2500)
        lc.on_record_up(3500)

        lc.on_record_down(3000)

        assert lc.state == LoopState.NORMAL
        assert lc.in_ms is None
        assert lc.out_ms is None

    def test_unscripted_loop_uses_raw_bounds(self):
        lc = LoopController(None)

        lc.on_record_down(2500)
        lc.on_record_up(3500)

        assert lc.state == LoopState.LOOPING
        assert lc.in_ms == 2500
        assert lc.out_ms == 3500

    def test_a_release_behind_the_start_floors_to_it_rather_than_flipping_the_loop(self):
        lc = LoopController(None)
        lc.on_record_down(5000)

        lc.on_record_up(2000)

        assert lc.state == LoopState.LOOPING
        assert lc.in_ms == 5000
        assert lc.out_ms == 5500

    def test_record_up_in_normal_is_noop(self):
        lc = LoopController(_make_fs())

        lc.on_record_up(1000)

        assert lc.state == LoopState.NORMAL

    def test_cancel_from_looping_returns_to_normal(self):
        lc = LoopController(_make_fs())
        lc.on_record_down(2500)
        lc.on_record_up(3500)

        lc.cancel()

        assert lc.state == LoopState.NORMAL
        assert lc.in_ms is None
        assert lc.out_ms is None

    def test_cancel_while_marking_abandons_mark(self):
        lc = LoopController(_make_fs())
        lc.on_record_down(2500)

        lc.cancel()

        assert lc.state == LoopState.NORMAL
        assert lc.in_ms is None

    def test_cancel_in_normal_is_noop(self):
        lc = LoopController(_make_fs())

        lc.cancel()

        assert lc.state == LoopState.NORMAL

    def test_restore_lands_straight_in_a_running_loop(self):
        lc = LoopController(_make_fs())

        lc.restore(2000, 4000)

        assert lc.state == LoopState.LOOPING
        assert (lc.in_ms, lc.out_ms) == (2000, 4000)

    def test_restore_does_not_snap_the_bounds_it_is_given(self):
        lc = LoopController(_make_fs())

        lc.restore(2500, 3500)

        assert (lc.in_ms, lc.out_ms) == (2500, 3500)
