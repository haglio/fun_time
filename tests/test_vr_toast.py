"""The banner a notice flashes over the player it names."""
from __future__ import annotations

import logging

import numpy as np
from PIL import ImageFont

from fun_time.event_log import FAVORITE, NOTICE
from fun_time_vr.toast import fit_toast, paint_toast, toast_bgra, toast_placement


class TestTheBanner:
    def test_it_says_what_it_was_given(self):
        short = paint_toast("skip", NOTICE, max_width=600)
        longer = paint_toast("landscape shuffle", NOTICE, max_width=600)

        assert longer.width > short.width
        assert np.asarray(short)[:, :, 3].max() > 0

    def test_it_reads_the_level_the_log_panel_reads(self):
        """One color per level across every surface: a red line is red on the
        desktop's toast, in its log panel and here."""
        plain = np.asarray(paint_toast("skip", NOTICE, max_width=600))
        loud = np.asarray(paint_toast("skip", logging.ERROR, max_width=600))
        favorite = np.asarray(paint_toast("skip", FAVORITE, max_width=600))

        assert not np.array_equal(plain, loud)
        assert not np.array_equal(plain, favorite)

    def test_a_long_line_is_cut_to_the_picture(self):
        wide = paint_toast("word " * 60, NOTICE, max_width=400)

        assert wide.width <= 400

    def test_a_line_that_fits_is_left_alone(self):
        font = ImageFont.load_default(19)

        assert fit_toast(font, "skip", 500) == "skip"

    def test_a_line_that_does_not_is_cut_at_its_tail(self):
        """A notice leads with what it is about, so the head survives the cut."""
        font = ImageFont.load_default(19)
        cut = fit_toast(font, "unrecognized voice command: " + "word " * 40, 220)

        assert cut.startswith("unrecognized")
        assert cut.endswith("…")
        assert font.getlength(cut) <= 220


class TestWhereItSits:
    def test_it_is_centered_across_the_top(self):
        banner = paint_toast("skip", NOTICE, max_width=1920)

        x, y = toast_placement(banner, 1920, 1080)

        assert x == (1920 - banner.width) // 2
        assert 0 < y < 1080 // 4

    def test_it_never_starts_off_the_left_of_a_narrow_picture(self):
        banner = paint_toast("landscape shuffle", NOTICE, max_width=2000)

        x, _y = toast_placement(banner, 40, 400)

        assert x == 0

    def test_the_margin_scales_with_the_picture(self):
        """A fixed pixel margin sits differently on a 4K master than on a 720p
        clip, and both hang in the same scene."""
        banner = paint_toast("skip", NOTICE, max_width=4096)

        _x, small = toast_placement(banner, 1280, 720)
        _x, large = toast_placement(banner, 3840, 2160)

        assert large > small


class TestWhatTheOverlayGets:
    def test_it_hands_over_a_placed_bgra_block(self):
        placed = toast_bgra("skip", NOTICE, width=1920, height=1080)

        assert placed is not None
        x, y, bgra = placed
        assert bgra.shape[2] == 4
        assert x >= 0 and y >= 0

    def test_a_picture_with_no_size_yet_gets_nothing(self):
        """Before mpv reports its dimensions the target is 1x1; a banner drawn
        onto that is a banner mpv would scale over the whole screen."""
        assert toast_bgra("skip", NOTICE, width=1, height=1) is None
