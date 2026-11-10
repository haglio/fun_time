from __future__ import annotations

import pytest
from shared_ui.preview import Preview, window_title

from fun_time.origenerator_switches import RfbMode
from fun_time.window_names import (
    FIXED_TOPMOST_WINDOWS,
    MANAGED_WINDOWS,
    ORIGENERATOR_TITLE,
    ORIGENERATOR_WINDOW,
    RFB_MODE_WINDOWS,
    is_origenerators_caption,
    visible_windows,
    window_is_topmost,
)


class TestRoleTopmost:
    def test_every_window_with_its_own_rect_is_always_topmost(self):
        for window_name in FIXED_TOPMOST_WINDOWS:
            for rfb_mode in RfbMode:
                assert window_is_topmost(window_name, rfb_mode) is True, (window_name, rfb_mode)

    def test_window_groups_partition_the_managed_set(self):
        assert set(MANAGED_WINDOWS) == {
            "rfb", "portrait", "landscape", "main_funestra", "dashboard", "origenerator",
        }
        assert set(FIXED_TOPMOST_WINDOWS) == {"portrait", "landscape", "dashboard", "main_funestra"}
        assert set(RFB_MODE_WINDOWS) == {"rfb", ORIGENERATOR_WINDOW}
        groups = [set(FIXED_TOPMOST_WINDOWS), set(RFB_MODE_WINDOWS)]
        assert sum(len(group) for group in groups) == len(MANAGED_WINDOWS)
        assert set().union(*groups) == set(MANAGED_WINDOWS)


class TestVisibleRoles:
    def test_the_browser_in_its_place_shows_every_window_but_the_hosted_apps(self):
        assert set(visible_windows(RfbMode.RFB)) == {
            "portrait", "landscape", "dashboard", "main_funestra", "rfb"}

    def test_origenerator_in_the_browsers_place_counts_the_browser_under_it(self):
        assert set(visible_windows(RfbMode.ORIGENERATOR)) == set(MANAGED_WINDOWS)


class TestOrigeneratorRoles:
    def test_the_hosted_window_follows_what_holds_the_browsers_place(self):
        assert window_is_topmost(ORIGENERATOR_WINDOW, RfbMode.ORIGENERATOR) is True
        assert window_is_topmost(ORIGENERATOR_WINDOW, RfbMode.RFB) is False

    def test_a_late_promotion_finds_the_covered_browser_out_of_the_band(self):
        assert window_is_topmost("rfb", RfbMode.RFB) is True
        assert window_is_topmost("rfb", RfbMode.ORIGENERATOR) is False

    def test_the_hosted_window_is_promoted_after_the_one_it_covers(self):
        assert MANAGED_WINDOWS.index(ORIGENERATOR_WINDOW) > MANAGED_WINDOWS.index("rfb")


@pytest.mark.parametrize(("caption", "is_it"), [
    (ORIGENERATOR_TITLE, True),
    (window_title(ORIGENERATOR_TITLE, Preview(feature=None)), True),
    (window_title(ORIGENERATOR_TITLE, Preview(feature="a fabricated feature")), True),
    ("Origenerator Loading", False),
    ("Origenerator Portrait", False),
    ("Origenerator Landscape", False),
    ("Slideshow", False),
])
def test_the_hosted_window_is_known_by_its_caption_or_by_the_one_a_preview_wears(
        caption, is_it):
    assert is_origenerators_caption(caption) is is_it
