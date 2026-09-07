from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from player_core.timeline import TIMELINE_HEIGHT, bar_track_x
from player_core.volume import CHIP_H, CHIP_W, PAD, SPEAKER_W, VolumeHud, chip_xy

from fun_time_vr.console_panel import PANEL_WIDTH_DEG, PANEL_WIDTH_PX
from fun_time_vr.furniture import (
    MUTE,
    SCRUBBER,
    VOLUME,
    FurniturePointer,
    chip_state,
    control_size,
    furniture_at,
    scrubber_state,
    with_furniture,
)
from fun_time_vr.layout import MIN_WIDTH_DEG


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


class TestHowBigTheControlsAre:
    """One angular size for every control in the scene.  Painted at the video's
    own resolution the scrubber was a fifth of a degree tall on a 28-degree
    satellite -- drawn, and far too small for a controller ray to land on."""

    def test_a_screen_is_measured_in_console_pixels_not_the_videos(self):
        assert control_size(PANEL_WIDTH_DEG, 1.0)[0] == PANEL_WIDTH_PX

    def test_the_same_screen_is_the_same_size_whatever_the_video_resolution(self):
        assert control_size(28.0, 16 / 9) == control_size(28.0, 16 / 9)

    def test_a_zoomed_screen_gets_proportionally_more_of_them(self):
        narrow, wide = control_size(28.0, 16 / 9), control_size(280.0, 16 / 9)

        assert wide[0] == pytest.approx(narrow[0] * 10, rel=0.01)

    def test_the_scrubber_lands_at_a_size_a_controller_can_hit(self):
        """TIMELINE_HEIGHT console pixels of it, on the smallest screen there is."""
        _width, height = control_size(MIN_WIDTH_DEG, 16 / 9)
        strip_deg = MIN_WIDTH_DEG / (16 / 9) * TIMELINE_HEIGHT / height

        assert strip_deg > 1.0

    def test_the_height_follows_the_pictures_shape(self):
        width, height = control_size(28.0, 16 / 9)

        assert height == pytest.approx(width / (16 / 9), abs=1)


_SIZE = control_size(72.0, 16 / 9)


def _uv(px: float, py: float, size: tuple[int, int] = _SIZE) -> tuple[float, float]:
    width, height = size
    return (px + 0.5) / width, 1 - (py + 0.5) / height


def _on_the_scrubber(size: tuple[int, int] = _SIZE) -> tuple[float, float]:
    x0, x1 = bar_track_x(size[0])
    return _uv((x0 + x1) / 2, size[1] - TIMELINE_HEIGHT // 2, size)


def _on_the_chip(part: str, size: tuple[int, int] = _SIZE) -> tuple[float, float]:
    x, y = chip_xy(win_w=size[0], win_h=size[1], timeline_h=TIMELINE_HEIGHT)
    across = SPEAKER_W // 2 if part == "mute" else (SPEAKER_W + CHIP_W - PAD) // 2
    return _uv(x + across, y + CHIP_H // 2, size)


class TestWhichControlAPressLandsOn:
    def test_the_lower_edge_is_the_scrubber(self):
        assert furniture_at(*_on_the_scrubber(), size=_SIZE) == SCRUBBER

    def test_the_speakers_end_of_the_chip_is_the_mute(self):
        assert furniture_at(*_on_the_chip("mute"), size=_SIZE) == MUTE

    def test_the_rest_of_the_chip_is_the_slider(self):
        assert furniture_at(*_on_the_chip("track"), size=_SIZE) == VOLUME

    def test_the_picture_itself_is_nothing(self):
        assert furniture_at(0.5, 0.5, size=_SIZE) is None


class TestASqueezeOnAVideosOwnControls:
    def _pointer(self, *, silent=False):
        seeks: list[float] = []
        posted: list[str] = []

        def mute(muted: bool) -> None:
            posted.append("audio_unmute" if muted else "audio_mute")

        pointer = FurniturePointer(
            seek=seeks.append,
            mute=None if silent else mute,
            set_volume=None if silent else (
                lambda level: posted.append(f"audio_set_volume|{level}")),
        )
        return SimpleNamespace(pointer=pointer, seeks=seeks, posted=posted)

    def _press(self, p, uv, *, muted=False):
        p.pointer.press(*uv, size=_SIZE, duration_ms=10_000.0, muted=muted)

    def test_a_press_on_the_scrubber_seeks_there(self):
        p = self._pointer()

        self._press(p, _on_the_scrubber())

        assert 4_000 < p.seeks[0] < 6_000
        assert p.posted == []

    def test_the_seek_follows_the_hand_until_it_lets_go(self):
        p = self._pointer()
        self._press(p, _on_the_scrubber())
        width, height = _SIZE
        x0, x1 = bar_track_x(width)

        p.pointer.drag(*_uv(x0 + (x1 - x0) * 0.8, height - 2), size=_SIZE, duration_ms=10_000.0)
        p.pointer.release()
        p.pointer.drag(*_on_the_scrubber(), size=_SIZE, duration_ms=10_000.0)

        assert len(p.seeks) == 2
        assert p.seeks[1] > 7_000

    def test_the_speaker_toggles_the_mute_the_way_it_is(self):
        loud, muted = self._pointer(), self._pointer()

        self._press(loud, _on_the_chip("mute"), muted=False)
        self._press(muted, _on_the_chip("mute"), muted=True)

        assert loud.posted == ["audio_mute"]
        assert muted.posted == ["audio_unmute"]

    def test_the_slider_sets_the_level_and_a_drag_along_it_follows(self):
        p = self._pointer()
        width, height = _SIZE
        x, y = chip_xy(win_w=width, win_h=height, timeline_h=TIMELINE_HEIGHT)

        self._press(p, _on_the_chip("track"))
        p.pointer.drag(*_on_the_chip("track"), size=_SIZE, duration_ms=10_000.0)
        p.pointer.drag(*_uv(x + CHIP_W + 40, y + CHIP_H // 2), size=_SIZE, duration_ms=10_000.0)
        p.pointer.release()
        p.pointer.drag(*_on_the_chip("track"), size=_SIZE, duration_ms=10_000.0)

        assert p.posted == ["audio_set_volume|50", "audio_set_volume|100"]
        assert p.seeks == []

    def test_the_picture_itself_asks_for_nothing(self):
        p = self._pointer()

        self._press(p, (0.5, 0.5))
        p.pointer.drag(0.6, 0.5, size=_SIZE, duration_ms=10_000.0)

        assert p.seeks == [] and p.posted == []

    def test_a_player_with_no_sink_still_scrubs_and_asks_for_no_level(self):
        """Nothing to set the level on -- a player whose sound has not been
        routed yet -- must still take a seek rather than swallowing the press."""
        p = self._pointer(silent=True)

        self._press(p, _on_the_chip("track"), muted=True)
        self._press(p, _on_the_chip("mute"), muted=True)
        self._press(p, _on_the_scrubber())

        assert p.posted == []
        assert len(p.seeks) == 1


class TestBlendingControlsIntoAPicture:
    """For a player handed finished pictures rather than decoding its own, this
    is where its controls go on -- there is no video player under them."""

    def test_the_picture_it_was_given_is_left_as_it_was(self):
        picture = np.zeros((10, 20, 3), dtype=np.uint8)
        opaque = np.full((4, 6, 4), 255, dtype=np.uint8)

        out = with_furniture(picture, [(opaque, 0, 0)])

        assert picture.max() == 0
        assert out[0, 0].tolist() == [255, 255, 255]

    def test_it_lands_where_it_is_put_and_the_bitmap_is_read_as_bgra(self):
        picture = np.zeros((10, 20, 3), dtype=np.uint8)
        blue = np.zeros((2, 2, 4), dtype=np.uint8)
        blue[:, :, 0] = 200
        blue[:, :, 3] = 255

        out = with_furniture(picture, [(blue, 5, 6)])

        assert out[6, 5].tolist() == [0, 0, 200]
        assert out[0, 0].max() == 0

    def test_what_is_transparent_leaves_the_picture_showing(self):
        picture = np.full((4, 4, 3), 100, dtype=np.uint8)
        clear = np.zeros((2, 2, 4), dtype=np.uint8)
        half = np.full((2, 2, 4), 255, dtype=np.uint8)
        half[:, :, 3] = 128

        assert with_furniture(picture, [(clear, 0, 0)])[0, 0].tolist() == [100, 100, 100]
        assert 150 < with_furniture(picture, [(half, 0, 0)])[0, 0][0] < 190

    def test_a_piece_hanging_off_the_edge_is_clipped_rather_than_wrapped(self):
        picture = np.zeros((4, 4, 3), dtype=np.uint8)
        opaque = np.full((3, 3, 4), 255, dtype=np.uint8)

        out = with_furniture(picture, [(opaque, 3, 3)])

        assert out[3, 3].tolist() == [255, 255, 255]
        assert out[0, 0].max() == 0
