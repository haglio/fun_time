from __future__ import annotations

from player_core.volume import VolumeHud

from fun_time_vr.furniture import chip_state, scrubber_state


class TestScrubberState:
    def test_holds_still_while_the_cursor_stays_on_a_pixel(self):
        # An hour-long clip moves the playcursor one track pixel every ~2s;
        # between crossings the painted bar is identical, so the key must not
        # move with every millisecond of playback.
        before = scrubber_state(1920, 1080, 1_000.0, 3_600_000.0)
        after = scrubber_state(1920, 1080, 1_040.0, 3_600_000.0)
        assert before == after

    def test_moves_when_the_cursor_crosses_a_pixel(self):
        before = scrubber_state(1920, 1080, 1_000.0, 600_000.0)
        later = scrubber_state(1920, 1080, 60_000.0, 600_000.0)
        assert before != later

    def test_moves_when_the_target_resizes(self):
        # A new clip's size repositions the bar and rescales the track.
        assert scrubber_state(1920, 1080, 0.0, 60_000.0) != scrubber_state(
            1280, 720, 0.0, 60_000.0
        )

    def test_zero_duration_is_safe_and_stable(self):
        assert scrubber_state(1920, 1080, 0.0, 0.0) == scrubber_state(1920, 1080, 0.0, 0.0)


class TestChipState:
    def test_holds_still_while_the_level_does(self):
        assert chip_state(1920, 1080, VolumeHud(volume=70, muted=False)) == chip_state(
            1920, 1080, VolumeHud(volume=70, muted=False)
        )

    def test_moves_on_level_mute_or_resize(self):
        base = chip_state(1920, 1080, VolumeHud(volume=70, muted=False))
        assert chip_state(1920, 1080, VolumeHud(volume=80, muted=False)) != base
        assert chip_state(1920, 1080, VolumeHud(volume=70, muted=True)) != base
        assert chip_state(1280, 720, VolumeHud(volume=70, muted=False)) != base
