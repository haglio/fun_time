from __future__ import annotations

from player_core.funscript import Funscript

from main_player.loop_controller import LoopController, LoopState


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
        # No funscript (a plain clip): the marked range is used as-is, no snapping.
        lc = LoopController(None)

        lc.on_record_down(2500)
        lc.on_record_up(3500)

        assert lc.state == LoopState.LOOPING
        assert lc.in_ms == 2500
        assert lc.out_ms == 3500

    def test_release_before_start_floors_out_to_the_start(self):
        # The record-down point is a hard floor for the out point too: an out
        # before the start (the EOF-wrap race, since seeks are clamped) can't
        # flip the loop — it collapses to the minimum loop at the start.
        lc = LoopController(None)  # unscripted: exact, unsnapped bounds
        lc.on_record_down(5000)

        lc.on_record_up(2000)  # out landed 3s before the start

        assert lc.state == LoopState.LOOPING
        assert lc.in_ms == 5000  # start stays put, not dragged back to 2000
        assert lc.out_ms == 5500  # floored to the start, widened to the min loop

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
        """A loop that outlives the session it was marked in comes back whole,
        without a mark-and-release: the controller a new video builds is fresh,
        and the bounds it is handed are already the finished ones."""
        lc = LoopController(_make_fs())

        lc.restore(2000, 4000)

        assert lc.state == LoopState.LOOPING
        assert (lc.in_ms, lc.out_ms) == (2000, 4000)

    def test_restore_does_not_snap_the_bounds_it_is_given(self):
        """They were snapped when the loop was made.  Snapping again would move
        a loop every time it is resumed, and the funscript beside the video need
        not even be the one that shaped it."""
        lc = LoopController(_make_fs())

        lc.restore(2500, 3500)

        assert (lc.in_ms, lc.out_ms) == (2500, 3500)
