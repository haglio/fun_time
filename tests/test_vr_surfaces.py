"""The two surfaces a headset gives a Funestra: the panel as a bitmap to hang
beside the picture, and a User's own picture as a frame to hang in the slot."""
from __future__ import annotations

import numpy as np
from player_core.clip_picture import Picture
from player_core.funestra import PanelSurface, UsersPicture
from player_core.satellite_hud import MARGIN

from fun_time_vr.surfaces import LatestPicture, PanelBitmap


class TestThePanelBitmap:
    def test_it_is_what_a_funestra_draws_its_panel_on(self):
        assert isinstance(PanelBitmap(width=380), PanelSurface)

    def test_it_holds_the_bitmap_the_way_gl_reads_it(self):
        bitmap = PanelBitmap()
        bgra = np.zeros((2, 3, 4), dtype=np.uint8)
        bgra[0, 0] = (10, 20, 30, 40)  # blue, green, red, alpha

        bitmap.overlay(10, MARGIN, MARGIN, bgra)

        rgba, _version = bitmap.take()
        assert rgba.shape == (2, 3, 4)
        assert tuple(rgba[0, 0]) == (30, 20, 10, 40)
        assert bitmap.size == (3, 2)

    def test_every_paint_and_removal_moves_the_version(self):
        bitmap = PanelBitmap()
        assert bitmap.take() == (None, 0)

        bitmap.overlay(10, MARGIN, MARGIN, np.zeros((1, 1, 4), dtype=np.uint8))
        _rgba, painted = bitmap.take()
        bitmap.remove_overlay(10)
        rgba, removed = bitmap.take()

        assert painted == 1
        assert removed == 2
        assert rgba is None
        assert bitmap.size is None

    def test_it_is_held_to_the_width_it_was_given_or_sized_to_its_contents(self):
        assert PanelBitmap(width=380).width == 380
        assert PanelBitmap().width is None

    def test_a_squeeze_on_the_hanging_screen_is_placed_where_the_panel_was_drawn(self):
        """The Funestra places a press in its window's pixels, and the panel
        sits in that window wherever the overlay put it; a point in the
        panel's own pixels goes back with that offset on."""
        bitmap = PanelBitmap()
        bitmap.overlay(10, 60, 373, np.zeros((1, 1, 4), dtype=np.uint8))

        assert bitmap.in_the_window(5, 7) == (65, 380)

    def test_before_anything_is_drawn_the_panel_is_at_the_windows_corner(self):
        assert PanelBitmap().in_the_window(5, 7) == (5, 7)


class TestTheLatestPicture:
    def test_it_is_where_a_funestra_puts_a_users_own_picture(self):
        assert isinstance(LatestPicture(), UsersPicture)

    def test_the_picture_put_up_is_the_one_the_render_thread_finds(self):
        latest = LatestPicture()
        frame = np.zeros((8, 16, 3), dtype=np.uint8)

        latest.show(Picture(frame=frame, played=3, count=8), (640, 480))

        assert latest.picture.frame is frame
        assert latest.picture.played == 3

    def test_there_is_none_before_one_is_up_and_none_again_once_it_is_down(self):
        latest = LatestPicture()
        assert latest.picture is None

        latest.show(Picture(frame=np.zeros((8, 16, 3), dtype=np.uint8)), (640, 480))
        latest.hide()

        assert latest.picture is None
