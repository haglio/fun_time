from __future__ import annotations

from pathlib import Path

import pytest
from funestra.testing import render_engine_radio_station, render_engine_without_mpv

from fun_time_vr.headset_engine import HeadsetEngine


@pytest.fixture
def mpv_calls():
    with render_engine_without_mpv(sound_routed_to="Example Headset") as calls:
        yield calls


def _an_engine() -> HeadsetEngine:
    return HeadsetEngine(lambda _name: None, loop_file=False, prefetch=True)


def test_it_is_built_muted_whatever_it_will_play(mpv_calls):
    built, _set_muted, _routed = mpv_calls

    _an_engine()

    assert built.call_args.kwargs["muted"] is True


def test_an_unmute_asked_before_the_headset_is_worn_waits(mpv_calls):
    _built, set_muted, _routed = mpv_calls
    engine = _an_engine()

    engine.set_muted(False)

    set_muted.assert_not_called()


def test_the_sound_goes_live_on_the_device_named_as_the_room_last_asked(mpv_calls):
    _built, set_muted, routed = mpv_calls
    engine = _an_engine()
    engine.set_muted(False)

    picked = engine.sound_goes_live("Example Headset")

    routed.assert_called_once_with("Example Headset")
    set_muted.assert_called_once_with(False)
    assert picked == "Example Headset"


def test_a_session_naming_no_device_keeps_the_default_sink(mpv_calls):
    _built, set_muted, routed = mpv_calls
    engine = _an_engine()

    engine.sound_goes_live("")

    routed.assert_not_called()
    set_muted.assert_called_once_with(True)


def test_once_live_a_mute_reaches_the_engine_at_once(mpv_calls):
    _built, set_muted, _routed = mpv_calls
    engine = _an_engine()
    engine.sound_goes_live("")

    engine.set_muted(False)
    engine.set_muted(True)

    assert [call.args[0] for call in set_muted.call_args_list] == [True, False, True]


def test_the_sound_goes_live_once(mpv_calls):
    _built, _set_muted, routed = mpv_calls
    engine = _an_engine()

    engine.sound_goes_live("Example Headset")
    engine.sound_goes_live("Example Headset")

    assert routed.call_count == 1


def test_no_radio_station_opens_before_the_headset_is_worn(mpv_calls):
    with render_engine_radio_station() as opened:
        engine = _an_engine()

        assert engine.radio_station([Path("alpha.mp4")]) is None
        opened.assert_not_called()


def test_once_worn_a_radio_station_opens_where_the_headsets_sound_goes(mpv_calls):
    with render_engine_radio_station(return_value="a station") as opened:
        engine = _an_engine()
        engine.sound_goes_live("Example Headset")

        assert engine.radio_station([Path("alpha.mp4")]) == "a station"
        opened.assert_called_once_with([Path("alpha.mp4")])
