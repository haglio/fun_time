"""A satellite's lock HUD hanging under its picture: the bitmap the overlay paints,
and what a press or a hover on either screen does."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from player_core.hud_overlay import HudOverlay
from player_core.satellite_hud import MARGIN, hud_text
from player_core.timeline import TIMELINE_HEIGHT

from fun_time.hud_transport import hud_model
from fun_time.lock_hud import HudPanel
from fun_time_vr.console_panel import DEG_PER_PX, PANEL_WIDTH_DEG, PANEL_WIDTH_PX
from fun_time_vr.satellite_hud import (
    HUD,
    PICTURE,
    HudSurface,
    SatellitePointer,
    hud_screen_name,
    screen_kind,
)


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
        self.drags: list[tuple[int, int]] = []
        self.released = 0

    def press(self, x, y):
        self.presses.append((x, y))

    def motion(self, x, y):
        self.motions.append((x, y))

    def drag_to(self, x, y):
        self.drags.append((x, y))
        return ""

    def release(self):
        self.released += 1


_PICTURE_SIZE = (640, 480)
_HUD_SIZE = (200, 100)


class TestAPressOnASatellite:
    """Two places a squeeze can land: the panel this screen hangs, and the
    picture under it.  The track, the time and the volume are a block of that
    panel and it places a squeeze on them itself, so the picture has nothing on
    it to hit (player_core's tests/test_hud_overlay.py)."""

    def _pointer(self):
        hud, asked = _FakeHud(), []
        pointer = SatellitePointer(hud=hud, picture=lambda: asked.append("omnipause_toggle"))
        return SimpleNamespace(pointer=pointer, hud=hud, asked=asked)

    def test_a_squeeze_on_the_panel_reaches_its_map_at_the_inset_the_desktop_draws_it_at(self):
        p = self._pointer()

        p.pointer.press(HUD, 0.25, 0.5, size=_HUD_SIZE)

        assert p.hud.presses == [(50 + MARGIN, 50 + MARGIN)]
        assert p.asked == []

    def test_a_squeeze_on_the_picture_asks_the_room_to_pause(self):
        p = self._pointer()

        p.pointer.press(PICTURE, 0.5, 0.5, size=_PICTURE_SIZE)

        assert p.hud.presses == []
        assert p.asked == ["omnipause_toggle"]

    def test_the_lower_edge_of_the_picture_is_the_picture_too(self):
        """The track used to span it; nothing is drawn there now."""
        p = self._pointer()
        v = 1 - (_PICTURE_SIZE[1] - TIMELINE_HEIGHT // 2) / _PICTURE_SIZE[1]

        p.pointer.press(PICTURE, 0.5, v, size=_PICTURE_SIZE)

        assert p.asked == ["omnipause_toggle"]

    def test_hovering_the_panel_names_the_button_under_the_pointer(self):
        p = self._pointer()

        p.pointer.hover(HUD, (0.25, 0.5), size=_HUD_SIZE)

        assert p.hud.motions == [(50 + MARGIN, 50 + MARGIN)]

    def test_a_drag_on_the_panel_reaches_it_at_the_inset_the_desktop_draws_it_at(self):
        p = self._pointer()

        p.pointer.drag(HUD, 0.25, 0.5, size=_HUD_SIZE)

        assert p.hud.drags == [(50 + MARGIN, 50 + MARGIN)]

    def test_letting_go_lets_go_of_whatever_the_panel_held(self):
        p = self._pointer()

        p.pointer.release()

        assert p.hud.released == 1

    def test_a_pointer_off_the_panel_leaves_no_tooltip(self):
        p = self._pointer()

        p.pointer.hover(HUD, None, size=_HUD_SIZE)
        p.pointer.hover(PICTURE, (0.5, 0.5), size=_PICTURE_SIZE)

        assert p.hud.motions == [(-1, -1), (-1, -1)]


def _a_panel() -> str:
    """A published panel with the mode row and the side's own band, as Fun Time
    publishes one for a headset session hosting an Origenerator."""
    return hud_text(hud_model(HudPanel(
        player="portrait", locked=False, lock_label="Shuffle", current="",
        seed_siblings=[], action_siblings=[], active=True, latest=False,
        satellites_mode="kino", in_vr=True,
    ), Path("C:/t")))


class TestThePressReachesTheDesktopsOwnMap:
    """The whole chain a squeeze on the hanging HUD travels: the overlay paints
    into the surface, the pointer turns the screen's (u, v) into the pixel
    under it, and the desktop's own click map posts the command."""

    def _hud(self, tmp_path):
        hud_file, command_file = tmp_path / "portrait_hud.json", tmp_path / "dashboard_cmd.txt"
        hud_file.write_text(_a_panel(), encoding="utf-8")
        surface = HudSurface()
        hud = HudOverlay(hud_file=hud_file, command_file=command_file, player=surface)
        hud.tick(video="scene one")
        pointer = SatellitePointer(hud=hud)
        return hud, surface, pointer, command_file

    @staticmethod
    def _uv_of(rect, size):
        x, y, w, h = rect
        width, height = size
        return (x + w / 2) / width, 1 - (y + h / 2) / height

    @staticmethod
    def _rect_of(hud, action: str):
        return next(rect for rect, button in hud.targets.buttons if button.command == action)

    def test_a_squeeze_on_the_lock_posts_the_lock(self, tmp_path):
        hud, surface, pointer, command_file = self._hud(tmp_path)
        rect = self._rect_of(hud, "portrait_lock")

        pointer.press(HUD, *self._uv_of(rect, surface.size), size=surface.size)

        assert command_file.read_text(encoding="utf-8").split() == ["portrait_lock"]

    def test_a_squeeze_on_a_mode_button_posts_that_mode(self, tmp_path):
        hud, surface, pointer, command_file = self._hud(tmp_path)
        rect = self._rect_of(hud, "origenerator_activate")

        pointer.press(HUD, *self._uv_of(rect, surface.size), size=surface.size)

        assert command_file.read_text(encoding="utf-8").split() == ["origenerator_activate"]

    def test_a_squeeze_beside_the_buttons_posts_nothing(self, tmp_path):
        _hud, surface, pointer, command_file = self._hud(tmp_path)

        pointer.press(HUD, 0.999, 0.001, size=surface.size)

        assert not command_file.exists()


def test_the_hud_hangs_at_the_consoles_own_pixel_scale():
    """A HUD pixel subtends what a console pixel does, so the two read at one
    size whatever the video's resolution -- scaled to the picture's pixels a
    HUD under a 1080p satellite was a few degrees wide and unpressable."""
    assert pytest.approx(PANEL_WIDTH_DEG / PANEL_WIDTH_PX) == DEG_PER_PX
    assert 300 * DEG_PER_PX > 20.0
