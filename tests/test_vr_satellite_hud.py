"""A satellite's HUD hanging under its picture: the screen it is, and the size it hangs at."""
from __future__ import annotations

import pytest

from fun_time_vr.console_panel import DEG_PER_PX, PANEL_WIDTH_DEG, PANEL_WIDTH_PX
from fun_time_vr.satellite_hud import HUD, PICTURE, hud_screen_name, screen_kind


class TestScreenNames:
    def test_a_satellites_hud_screen_is_named_after_its_side(self):
        assert screen_kind(hud_screen_name("portrait")) == HUD
        assert screen_kind("portrait") == PICTURE


def test_the_hud_hangs_at_the_consoles_own_pixel_scale():
    """A HUD pixel subtends what a console pixel does, so the two read at one
    size whatever the video's resolution -- scaled to the picture's pixels a
    HUD under a 1080p satellite was a few degrees wide and unpressable."""
    assert pytest.approx(PANEL_WIDTH_DEG / PANEL_WIDTH_PX) == DEG_PER_PX
    assert 300 * DEG_PER_PX > 20.0
