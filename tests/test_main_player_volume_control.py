"""The main player's volume chip: what a press on it shows, and what it asks Fun Time for.

The chip floats at the right-hand end of the timeline row, so every case here
goes through real window coordinates rather than chip-local ones — the offset
from the window's lower-right corner is half of what a press has to get right,
and a test written in chip-local pixels cannot see the chip move.

800x600 with a 24px timeline row puts the chip at (678, 577); the speaker is its
left 26 columns and the slider the rest.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from main_player.dashboard import Dashboard
from main_player.volume_control import VolumeControl

WIN = {"win_w": 800, "win_h": 600, "timeline_h": 24}
SPEAKER = (690, 585)       # on the chip's left end
TRACK_HALFWAY = (744, 585)
TRACK_LEFT_END = (704, 585)
OFF_THE_CHIP = (400, 585)  # same row, out over the video


@pytest.fixture
def cmd_file(tmp_path: Path) -> Path:
    return tmp_path / "dashboard_cmd.txt"


@pytest.fixture
def control(cmd_file: Path) -> VolumeControl:
    return VolumeControl(Dashboard(cmd_file))


def asks(cmd_file: Path) -> list[str]:
    """What this control has asked Fun Time for, in order."""
    if not cmd_file.exists():
        return []
    return cmd_file.read_text(encoding="utf-8").split()


class TestPressingTheSpeaker:
    def test_it_mutes_and_asks_for_the_mute(self, control, cmd_file):
        assert control.press_at(*SPEAKER, **WIN) is True

        assert control.hud.muted is True
        assert asks(cmd_file) == ["audio_mute"]

    def test_pressing_it_again_unmutes(self, control, cmd_file):
        control.press_at(*SPEAKER, **WIN)

        control.press_at(*SPEAKER, **WIN)

        assert control.hud.muted is False
        assert asks(cmd_file) == ["audio_mute", "audio_unmute"]

    def test_the_level_underneath_is_left_where_it_was(self, control):
        """Unmuting has to come back to the level the speaker chose, so the
        mute rides alongside the level rather than replacing it with zero."""
        control.set(40, muted=False)

        control.press_at(*SPEAKER, **WIN)

        assert (control.hud.volume, control.hud.muted) == (40, True)


class TestPressingTheSlider:
    def test_it_asks_for_the_level_under_the_pointer(self, control, cmd_file):
        assert control.press_at(*TRACK_HALFWAY, **WIN) is True

        assert asks(cmd_file) == ["audio_set_volume|50"]

    def test_the_left_end_of_the_track_is_silence(self, control):
        control.press_at(*TRACK_LEFT_END, **WIN)

        assert control.hud.volume == 0

    def test_the_new_level_is_shown_before_fun_time_has_answered(self, control):
        """Fun Time holds the authority and its answer is a tick away; a slider
        that waited for it would trail the pointer by a frame."""
        control.press_at(*TRACK_HALFWAY, **WIN)

        assert control.hud.volume == 50

    def test_asking_for_a_level_is_asking_to_hear_it(self, control):
        control.press_at(*SPEAKER, **WIN)

        control.press_at(*TRACK_HALFWAY, **WIN)

        assert control.hud.muted is False


class TestAPressThatMissed:
    def test_it_is_not_taken_and_falls_through_to_the_video(self, control, cmd_file):
        assert control.press_at(*OFF_THE_CHIP, **WIN) is False

        assert asks(cmd_file) == []

    def test_the_chip_moves_with_the_window(self, control):
        """The same window point is on the chip in one window and out over the
        video in a wider one, because the chip is placed from the right edge."""
        on_a_narrow_window = control.press_at(*TRACK_HALFWAY, win_w=800, win_h=600,
                                              timeline_h=24)
        missed_a_wide_one = control.press_at(*TRACK_HALFWAY, win_w=1600, win_h=600,
                                             timeline_h=24)

        assert (on_a_narrow_window, missed_a_wide_one) == (True, False)

    def test_a_taller_timeline_row_lifts_the_chip_off_the_pointer(self, control):
        """A loop being recorded grows the strip to 48px and the chip rides up
        with it, so a press near the lower edge is on the video now."""
        near_the_bottom = (744, 595)

        assert control.press_at(*near_the_bottom, win_w=800, win_h=600, timeline_h=24)
        assert not control.press_at(*near_the_bottom, win_w=800, win_h=600, timeline_h=48)


class TestDraggingAlongTheTrack:
    def test_a_drag_keeps_setting_the_level(self, control, cmd_file):
        control.press_at(*TRACK_LEFT_END, **WIN)

        control.drag_at(*TRACK_HALFWAY, **WIN)

        assert control.hud.volume == 50
        assert asks(cmd_file) == ["audio_set_volume|0", "audio_set_volume|50"]

    def test_a_drag_that_began_elsewhere_does_nothing(self, control, cmd_file):
        control.drag_at(*OFF_THE_CHIP, **WIN)

        assert asks(cmd_file) == []

    def test_dragging_across_the_speaker_does_not_toggle_the_mute(self, control, cmd_file):
        """The mute is a press, not a drag: a pointer crossing the speaker on
        its way to the track would otherwise flip it on the way past."""
        control.drag_at(*SPEAKER, **WIN)

        assert control.hud.muted is False
        assert asks(cmd_file) == []


class TestWhatFunTimeSaysBack:
    def test_its_answer_is_what_the_chip_shows(self, control):
        """The level here is drawn and reported, never decided -- the main player's mpv is
        one of two sinks Fun Time drives, and the answer comes back down the
        same channel a press went out on."""
        control.press_at(*TRACK_HALFWAY, **WIN)

        control.set(80, muted=False)

        assert (control.hud.volume, control.hud.muted) == (80, False)

    def test_an_ignored_press_corrects_itself_rather_than_sticking(self, control):
        control.press_at(*SPEAKER, **WIN)

        control.set(60, muted=False)

        assert control.hud.muted is False

    def test_it_starts_at_full_and_unmuted_until_anything_says_otherwise(self, control):
        """A standalone main player is never told, and a chip that opened at silence
        would report a level the player is not at."""
        assert (control.hud.volume, control.hud.muted) == (100, False)
