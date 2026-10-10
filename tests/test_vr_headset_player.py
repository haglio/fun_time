"""The engine a headset's Funestra plays through: silent until the headset is
worn, whatever the room or a chip asks before then."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from player_core.render_player import MpvRenderPlayer

from fun_time_vr.headset_player import HeadsetPlayer


@pytest.fixture
def engine():
    """The engine's own calls recorded rather than made: the player under test
    is what sits between the Funestra and them."""
    with patch.object(MpvRenderPlayer, "__init__", return_value=None) as built, \
            patch.object(MpvRenderPlayer, "set_muted") as set_muted, \
            patch.object(MpvRenderPlayer, "set_audio_device_matching",
                         return_value="Example Headset") as routed:
        yield built, set_muted, routed


def _a_player() -> HeadsetPlayer:
    return HeadsetPlayer(lambda _name: None, loop_file=False, prefetch=True)


def test_it_is_built_muted_whatever_it_will_play(engine):
    built, _set_muted, _routed = engine

    _a_player()

    assert built.call_args.kwargs["muted"] is True


def test_an_unmute_asked_before_the_headset_is_worn_waits(engine):
    """A sink parked on the stand takes the stream without draining it, and
    mpv's clock follows audio: unmuted then, the picture freezes on frame 1."""
    _built, set_muted, _routed = engine
    player = _a_player()

    player.set_muted(False)

    set_muted.assert_not_called()


def test_the_sound_goes_live_on_the_device_named_as_the_room_last_asked(engine):
    _built, set_muted, routed = engine
    player = _a_player()
    player.set_muted(False)

    picked = player.sound_goes_live("Example Headset")

    routed.assert_called_once_with("Example Headset")
    set_muted.assert_called_once_with(False)
    assert picked == "Example Headset"


def test_a_session_naming_no_device_keeps_the_default_sink(engine):
    _built, set_muted, routed = engine
    player = _a_player()

    player.sound_goes_live("")

    routed.assert_not_called()
    set_muted.assert_called_once_with(True)


def test_once_live_a_mute_reaches_the_engine_at_once(engine):
    _built, set_muted, _routed = engine
    player = _a_player()
    player.sound_goes_live("")

    player.set_muted(False)
    player.set_muted(True)

    assert [call.args[0] for call in set_muted.call_args_list] == [True, False, True]


def test_the_sound_goes_live_once(engine):
    _built, _set_muted, routed = engine
    player = _a_player()

    player.sound_goes_live("Example Headset")
    player.sound_goes_live("Example Headset")

    assert routed.call_count == 1
