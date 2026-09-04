"""A satellite's own volume chip: what it opens showing, and what a press sets.

Driven against the shared fake player, whose ``volume``/``muted`` pair models
mpv's two independent audio properties — so a test can tell "turned all the way
down" from "muted", which is the distinction the chip exists to draw.
"""
from __future__ import annotations

from player_core.volume import CHIP_H, VolumeHud, chip_xy

from satellite.volume import SatelliteVolume
from tests.satellite_fakes import FakeSatellitePlayer

WIN_W, WIN_H = 640, 480
ROW_H = 24

# Window points on the three parts of the chip, from its own placement.
_VX, _VY = chip_xy(win_w=WIN_W, win_h=WIN_H, timeline_h=ROW_H)
SPEAKER = (_VX + 7, _VY + CHIP_H // 2)      # the left end, which mutes
HALFWAY = (_VX + 66, _VY + CHIP_H // 2)     # the slider's midpoint: level 50
OFF_CHIP = (_VX - 40, _VY + CHIP_H // 2)    # just left of it, on the scrubber


def _volume(*, live: bool = True):
    player = FakeSatellitePlayer()
    return SatelliteVolume(player, live=live), player


def _press(volume, point) -> bool:
    return volume.press_at(*point, win_w=WIN_W, win_h=WIN_H, timeline_h=ROW_H)


def _drag(volume, point) -> None:
    volume.drag_at(*point, win_w=WIN_W, win_h=WIN_H, timeline_h=ROW_H)


class TestWhatItOpensAt:
    def test_a_live_chip_opens_muted_at_full(self):
        # The room's sound is the main player's, so a satellite is quiet until
        # asked — but the fill under the mute is what unmuting returns to, and a
        # chip opening at zero would unmute to silence.
        volume, _player = _volume()

        assert volume.hud.muted is True
        assert volume.hud.volume == 100

    def test_a_silent_chip_opens_empty(self):
        # --no-audio: there is no level to return to, so the chip reads as the
        # fixed indicator it was before a satellite could be heard at all.
        volume, _player = _volume(live=False)

        assert volume.hud.muted is True
        assert volume.hud.volume == 0


class TestPresses:
    def test_the_speaker_unmutes_the_player_and_mutes_it_again(self):
        volume, player = _volume()

        assert _press(volume, SPEAKER)
        assert volume.hud.muted is False
        assert player.muted is False

        assert _press(volume, SPEAKER)
        assert volume.hud.muted is True
        assert player.muted is True

    def test_the_slider_sets_the_level_and_lifts_the_mute(self):
        # Reaching for the volume is asking to hear something — the same
        # convention Fun Time's spoken "louder" follows on the main player.
        volume, player = _volume()

        assert _press(volume, HALFWAY)

        assert volume.hud == VolumeHud(volume=50, muted=False)
        assert (player.volume, player.muted) == (50, False)

    def test_a_press_beside_the_chip_is_not_taken(self):
        volume, player = _volume()

        assert not _press(volume, OFF_CHIP)
        assert (player.volume, player.muted) == (100, True)

    def test_a_silent_chip_swallows_its_press_without_acting_on_it(self):
        # It is drawn over the scrubber's row, so a press on it must not seek
        # the video behind it — even where there is no sound to set.
        volume, player = _volume(live=False)

        assert _press(volume, SPEAKER)
        assert volume.hud.muted is True
        assert player.muted is True


class TestDrags:
    def test_a_drag_along_the_track_keeps_setting_the_level(self):
        volume, player = _volume()

        _drag(volume, HALFWAY)
        assert player.volume == 50

        _drag(volume, (_VX + 26, HALFWAY[1]))     # the track's silent end
        assert player.volume == 0

    def test_a_drag_past_the_track_s_end_saturates_instead_of_stopping(self):
        # The last few pixels of the chip are past the drawn track; a pointer
        # dragged onto them is asking for full, not for wherever it last was.
        volume, player = _volume()

        _drag(volume, (_VX + 110, HALFWAY[1]))

        assert player.volume == 100

    def test_a_drag_that_began_elsewhere_misses_the_chip_and_does_nothing(self):
        volume, player = _volume()

        _drag(volume, OFF_CHIP)

        assert (player.volume, player.muted) == (100, True)

    def test_a_drag_across_the_speaker_does_not_flip_the_mute(self):
        # The mute is a press; a pointer on its way to the slider crosses the
        # speaker and must arrive with the sound in the state it started in.
        volume, player = _volume()
        _press(volume, HALFWAY)          # unmuted, level 50

        _drag(volume, SPEAKER)

        assert player.muted is False
