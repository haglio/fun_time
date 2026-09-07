"""The banner a notice flashes over the player it names."""
from __future__ import annotations

import logging

import numpy as np
from PIL import ImageFont

from fun_time.event_log import FAVORITE, NOTICE
from fun_time_vr.toast import (
    MIN_FONT_PX,
    fit_toast,
    font_px,
    paint_toast,
    toast_bgra,
    toast_placement,
)


def _banner(message="skip", level=NOTICE, *, max_width=1600, size=48):
    return paint_toast(message, level, max_width=max_width, size=size)


class TestTheBanner:
    def test_it_says_what_it_was_given(self):
        short = _banner("skip")
        longer = _banner("landscape shuffle")

        assert longer.width > short.width
        assert np.asarray(short)[:, :, 3].max() > 0

    def test_it_reads_the_level_the_log_panel_reads(self):
        """One color per level across every surface: a red line is red on the
        desktop's toast, in its log panel and here."""
        plain = np.asarray(_banner(level=NOTICE))
        loud = np.asarray(_banner(level=logging.ERROR))
        favorite = np.asarray(_banner(level=FAVORITE))

        assert not np.array_equal(plain, loud)
        assert not np.array_equal(plain, favorite)

    def test_a_long_line_is_cut_to_the_width_it_was_given(self):
        wide = _banner("word " * 60, max_width=400)

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


class TestItIsSizedToThePictureItGoesOn:
    """A player decodes to 2048 or 4096 pixels, not to the size of a window, so
    the desktop's own 19px type came out a tenth of the height it reads at
    there -- with a 1px border and a 4px corner nobody could see at all."""

    def test_the_type_grows_with_the_picture(self):
        assert font_px(4096) > font_px(2048) > font_px(720)

    def test_it_never_goes_under_the_desktops_own_size(self):
        assert font_px(100) == MIN_FONT_PX

    def test_the_whole_shape_grows_with_it_and_not_just_the_type(self):
        """The padding, the border and the corner are multiples of the type, so
        a banner on a big picture is the same shape rather than thin type in a
        box that stayed small."""
        small = _banner(size=20)
        large = _banner(size=80)

        assert large.height / small.height > 3.0

    def test_a_bigger_picture_gets_a_bigger_banner(self):
        _x, _y, small = toast_bgra("skip", NOTICE, width=1280, height=720)
        _x, _y, large = toast_bgra("skip", NOTICE, width=3840, height=2160)

        assert large.shape[0] > small.shape[0]


class TestWhereItSits:
    def test_it_is_centered_across_the_top(self):
        banner = _banner()

        x, y = toast_placement(banner, 1920, 1080)

        assert x == (1920 - banner.width) // 2
        assert 0 < y < 1080 // 10

    def test_it_sits_where_the_desktop_sits_it(self):
        """The desktop puts its banner 28px down a player's window; on a picture
        this tall that is the same place, and it was twice as far down."""
        _x, y = toast_placement(_banner(), 1920, 1000)

        assert 20 <= y <= 36

    def test_it_never_starts_off_the_left_of_a_narrow_picture(self):
        banner = _banner("landscape shuffle", max_width=4000, size=64)

        x, _y = toast_placement(banner, 40, 400)

        assert x == 0

    def test_the_margin_scales_with_the_picture(self):
        banner = _banner()

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

    def test_the_banner_stays_inside_the_picture(self):
        x, _y, bgra = toast_bgra("landscape shuffle please", NOTICE, width=1152, height=2048)

        assert x + bgra.shape[1] <= 1152

    def test_a_picture_with_no_size_yet_gets_nothing(self):
        """Before mpv reports its dimensions the target is 1x1; a banner drawn
        onto that is a banner mpv would scale over the whole screen."""
        assert toast_bgra("skip", NOTICE, width=1, height=1) is None
