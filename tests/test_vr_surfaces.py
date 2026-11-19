from __future__ import annotations

import numpy as np
from funestra.flick_overlay import FlickFrame
from funestra.funestra import HudSurface, UsersFrames
from funestra.satellite_hud import MARGIN

from fun_time_vr.surfaces import HudBitmap, LatestFrame
from fun_time_vr.texture_rows import Rows


class TestTheHudBitmap:
    def test_it_is_what_a_funestra_draws_its_hud_on(self):
        assert isinstance(HudBitmap(width=380), HudSurface)

    def test_it_holds_the_bitmap_as_drawn_for_the_card_to_read_as_bgra(self):
        bitmap = HudBitmap()
        bgra = np.zeros((2, 3, 4), dtype=np.uint8)
        bgra[0, 0] = (10, 20, 30, 40)

        bitmap.overlay(10, MARGIN, MARGIN, bgra)

        assert bitmap.take().bgra is bgra
        assert bitmap.size == (3, 2)

    def test_a_repaint_hands_over_only_the_rows_it_changed_since_the_last_take(self):
        bitmap = HudBitmap()
        first = np.zeros((6, 3, 4), dtype=np.uint8)
        bitmap.overlay(10, MARGIN, MARGIN, first)
        bitmap.take()
        scrubber_moved, readout_moved = first.copy(), first.copy()
        scrubber_moved[4] = 9
        readout_moved[4] = 9
        readout_moved[1] = 9

        bitmap.overlay(10, MARGIN, MARGIN, scrubber_moved)
        bitmap.overlay(10, MARGIN, MARGIN, readout_moved)

        assert bitmap.take().rows == Rows(1, 5)
        assert bitmap.take().rows is None

    def test_a_repaint_that_changed_nothing_hands_over_no_rows(self):
        bitmap = HudBitmap()
        bitmap.overlay(10, MARGIN, MARGIN, np.zeros((6, 3, 4), dtype=np.uint8))
        bitmap.take()

        bitmap.overlay(10, MARGIN, MARGIN, np.zeros((6, 3, 4), dtype=np.uint8))

        assert bitmap.take().rows is None

    def test_a_bitmap_handed_over_again_after_a_removal_or_as_the_same_object_is_sent_whole(self):
        bitmap = HudBitmap()
        bgra = np.zeros((6, 3, 4), dtype=np.uint8)
        bitmap.overlay(10, MARGIN, MARGIN, bgra)
        bitmap.take()
        bitmap.overlay(10, MARGIN, MARGIN, bgra)
        drawn_again_in_place = bitmap.take().rows
        bitmap.remove_overlay(10)
        bitmap.overlay(10, MARGIN, MARGIN, bgra.copy())

        assert (drawn_again_in_place, bitmap.take().rows) == (Rows(0, 6), Rows(0, 6))

    def test_every_paint_and_removal_moves_the_version(self):
        bitmap = HudBitmap()
        assert bitmap.take() == (None, 0, None)

        bitmap.overlay(10, MARGIN, MARGIN, np.zeros((1, 1, 4), dtype=np.uint8))
        painted = bitmap.take().version
        bitmap.remove_overlay(10)
        removed = bitmap.take()

        assert painted == 1
        assert removed.version == 2
        assert removed.bgra is None
        assert bitmap.size is None

    def test_it_is_held_to_the_width_it_was_given_or_sized_to_its_contents(self):
        assert HudBitmap(width=380).width == 380
        assert HudBitmap().width is None

    def test_a_squeeze_on_the_hanging_screen_is_placed_where_the_hud_was_drawn(self):
        bitmap = HudBitmap()
        bitmap.overlay(10, 60, 373, np.zeros((1, 1, 4), dtype=np.uint8))

        assert bitmap.in_the_window(5, 7) == (65, 380)

    def test_before_anything_is_drawn_the_hud_is_at_the_windows_corner(self):
        assert HudBitmap().in_the_window(5, 7) == (5, 7)


class TestTheLatestFrame:
    def test_it_is_where_a_funestra_puts_a_users_own_frame(self):
        assert isinstance(LatestFrame(), UsersFrames)

    def test_the_frame_put_up_is_the_one_the_render_thread_finds(self):
        latest = LatestFrame()
        frame = np.zeros((8, 16, 3), dtype=np.uint8)

        latest.show(FlickFrame(frame=frame, played=3, count=8), (640, 480))

        assert latest.flick_frame.frame is frame
        assert latest.flick_frame.played == 3

    def test_there_is_none_before_one_is_up_and_none_again_once_it_is_down(self):
        latest = LatestFrame()
        assert latest.flick_frame is None

        latest.show(FlickFrame(frame=np.zeros((8, 16, 3), dtype=np.uint8)), (640, 480))
        latest.hide()

        assert latest.flick_frame is None
