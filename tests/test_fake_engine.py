from __future__ import annotations

from pathlib import Path

import pytest

from tests.satellite_fakes import FakeSatelliteEngine

ONE, TWO, THREE = Path("one.mp4"), Path("two.mp4"), Path("three.mp4")


def test_a_new_engine_holds_nothing_and_opens_muted_at_full_volume():
    engine = FakeSatelliteEngine()

    assert (engine.opened, engine.playlist, engine.playlist_pos) == ([], [], 0)
    assert (engine.duration_ms, engine.position_ms, engine.frame_rate) == (5_000.0, 0.0, 25.0)
    assert (engine.volume, engine.muted, engine.speed) == (100, True, 1.0)
    assert (engine.paused, engine.loop_file, engine.closed, engine.idle) == (False,) * 4
    assert (engine.showing_image, engine.showing_video) == (False, True)


def test_its_length_is_its_first_argument():
    assert FakeSatelliteEngine(1_000.0).duration_ms == 1_000.0


def test_a_load_goes_back_to_the_start():
    engine = FakeSatelliteEngine()
    engine.seek_ms(1_000.0)

    engine.load(ONE)

    assert (engine.opened, engine.playlist, engine.position_ms) == ([ONE], [ONE], 0.0)


def test_staging_replaces_what_was_staged():
    engine = FakeSatelliteEngine()
    engine.load(ONE)
    engine.stage_next(TWO)

    engine.stage_next(THREE)

    assert (engine.playlist, engine.staged_next) == ([ONE, THREE], THREE)


def test_clearing_drops_what_was_staged():
    engine = FakeSatelliteEngine()
    engine.load(ONE)
    engine.stage_next(TWO)

    engine.clear_next()

    assert (engine.playlist, engine.staged_next) == ([ONE], None)


def test_running_out_rolls_onto_what_was_staged():
    engine = FakeSatelliteEngine()
    engine.load(ONE)
    engine.stage_next(TWO)
    assert engine.advanced_to_next is False

    engine.simulate_eof_advance()

    assert (engine.advanced_to_next, engine.staged_next) == (True, None)


def test_running_out_with_nothing_staged_stays_put():
    engine = FakeSatelliteEngine()
    engine.load(ONE)

    engine.simulate_eof_advance()

    assert engine.advanced_to_next is False


def test_settling_forgets_the_item_it_rolled_off_and_keeps_what_was_staged_after():
    engine = FakeSatelliteEngine()
    engine.load(ONE)
    engine.stage_next(TWO)
    engine.simulate_eof_advance()
    engine.stage_next(THREE)

    engine.settle_on_the_next()

    assert (engine.playlist, engine.playlist_pos) == ([TWO, THREE], 0)


def test_staging_after_a_roll_keeps_the_item_it_rolled_off():
    engine = FakeSatelliteEngine()
    engine.load(ONE)
    engine.stage_next(TWO)
    engine.simulate_eof_advance()

    engine.stage_next(THREE)

    assert (engine.playlist, engine.playlist_pos) == ([ONE, TWO, THREE], 1)


def test_a_swap_replaces_the_item_on_screen():
    engine = FakeSatelliteEngine()
    engine.load(ONE)

    engine.swap_still(TWO)

    assert (engine.swapped, engine.playlist) == ([TWO], [TWO])


def test_a_seek_is_recorded_as_asked_and_lands_within_the_length():
    engine = FakeSatelliteEngine(1_000.0)

    engine.seek_ms(-5.0)
    assert engine.position_ms == 0.0
    engine.seek_ms(9_000.0)

    assert (engine.seeks, engine.position_ms) == ([-5.0, 9_000.0], 1_000.0)


def test_a_refused_seek_raises_as_mpv_does_and_moves_nothing():
    engine = FakeSatelliteEngine()
    engine.refuse_seeks(1)

    with pytest.raises(SystemError):
        engine.seek_ms(1_000.0)
    engine.seek_ms(2_000.0)

    assert (engine.refused, engine.seeks, engine.position_ms) == (1, [2_000.0], 2_000.0)


def test_each_setting_is_held_as_given():
    engine = FakeSatelliteEngine()

    engine.set_paused(True)
    engine.set_loop_file(True)
    engine.set_pace(3.0)
    engine.set_volume(40)
    engine.set_muted(False)
    engine.set_speed(2.0)
    engine.set_ab_loop(1.0, 2.0)
    engine.tile_to_fill(640, 480)

    assert (engine.paused, engine.loop_file, engine.pace_s) == (True, True, 3.0)
    assert (engine.volume, engine.muted, engine.speed, engine.ab_loop) == (40, False, 2.0, (1.0, 2.0))
    assert engine.tiled_to == [(640, 480)]
    engine.clear_ab_loop()
    assert engine.ab_loop is None


def test_a_still_counts_what_it_is_asked():
    engine = FakeSatelliteEngine()

    engine.push_still()

    assert engine.pushes == 1


def test_there_is_no_screenshot_and_no_radio_station():
    engine = FakeSatelliteEngine()

    assert engine.screenshot_bgra() is None
    assert engine.radio_station([ONE]) is None


def test_overlays_are_held_by_their_id_and_removing_a_missing_one_is_nothing():
    engine = FakeSatelliteEngine()

    engine.overlay(1, 2, 3, b"drawn")
    assert engine.overlays == {1: (2, 3, b"drawn")}
    engine.remove_overlay(1)
    engine.remove_overlay(1)

    assert engine.overlays == {}


def test_closing_is_remembered():
    engine = FakeSatelliteEngine()

    engine.close()

    assert engine.closed is True
