"""A satellite's lock HUD hung under its picture: the bitmap the overlay paints,
and what a press or a hover on either screen does."""
from __future__ import annotations

import numpy as np
import pytest
from player_core.satellite_hud import MARGIN
from player_core.timeline import TIMELINE_HEIGHT

from fun_time_vr.satellite_hud import (
    HUD,
    PICTURE,
    HudSurface,
    SatellitePointer,
    hud_screen_name,
    screen_kind,
)
from satellite.pointer import time_at


class TestTheSurfaceTheOverlayPaintsInto:
    def test_it_holds_the_bitmap_the_way_gl_reads_it(self):
        surface = HudSurface()
        bgra = np.zeros((2, 3, 4), dtype=np.uint8)
        bgra[0, 0] = (10, 20, 30, 40)  # blue, green, red, alpha

        surface.overlay(10, MARGIN, MARGIN, bgra)

        rgba, _version = surface.take()
        assert rgba.shape == (2, 3, 4)
        assert tuple(rgba[0, 0]) == (30, 20, 10, 40)
        assert surface.size == (3, 2)

    def test_every_paint_and_removal_moves_the_version(self):
        surface = HudSurface()
        assert surface.take() == (None, 0)

        surface.overlay(10, MARGIN, MARGIN, np.zeros((1, 1, 4), dtype=np.uint8))
        _rgba, painted = surface.take()
        surface.remove_overlay(10)
        rgba, removed = surface.take()

        assert painted == 1
        assert removed == 2
        assert rgba is None
        assert surface.size is None


class TestScreenNames:
    def test_a_satellites_hud_screen_is_named_after_its_side(self):
        assert screen_kind(hud_screen_name("portrait")) == HUD
        assert screen_kind("portrait") == PICTURE


class _FakeHud:
    def __init__(self):
        self.presses: list[tuple[int, int]] = []
        self.motions: list[tuple[int, int]] = []

    def press(self, x, y):
        self.presses.append((x, y))

    def motion(self, x, y):
        self.motions.append((x, y))


_PICTURE_SIZE = (640, 480)
_HUD_SIZE = (200, 100)


class TestAPressOnASatellite:
    def _pointer(self):
        hud, seeks = _FakeHud(), []
        pointer = SatellitePointer(hud=hud, seek=seeks.append, duration_ms=lambda: 10_000.0)
        return pointer, hud, seeks

    def test_a_press_on_the_hud_reaches_its_map_at_the_inset_the_desktop_draws_it_at(self):
        pointer, hud, seeks = self._pointer()

        pointer.press(HUD, 0.25, 0.5, size=_HUD_SIZE)

        assert hud.presses == [(50 + MARGIN, 50 + MARGIN)]
        assert seeks == []

    def test_a_press_on_the_pictures_scrubber_seeks_the_clip(self):
        pointer, hud, seeks = self._pointer()
        width, height = _PICTURE_SIZE
        v = 1 - (height - TIMELINE_HEIGHT // 2) / height

        pointer.press(PICTURE, 0.5, v, size=_PICTURE_SIZE)

        assert seeks == [pytest.approx(time_at(320, win_w=width, duration_ms=10_000.0))]
        assert hud.presses == []

    def test_a_press_on_the_picture_itself_does_nothing(self):
        pointer, hud, seeks = self._pointer()

        pointer.press(PICTURE, 0.5, 0.5, size=_PICTURE_SIZE)

        assert seeks == [] and hud.presses == []

    def test_hovering_the_hud_names_the_button_under_the_pointer(self):
        pointer, hud, _seeks = self._pointer()

        pointer.hover(HUD, (0.25, 0.5), size=_HUD_SIZE)

        assert hud.motions == [(50 + MARGIN, 50 + MARGIN)]

    def test_a_pointer_off_the_hud_leaves_no_tooltip_behind(self):
        pointer, hud, _seeks = self._pointer()

        pointer.hover(HUD, None, size=_HUD_SIZE)
        pointer.hover(PICTURE, (0.5, 0.5), size=_PICTURE_SIZE)

        assert hud.motions == [(-1, -1), (-1, -1)]
