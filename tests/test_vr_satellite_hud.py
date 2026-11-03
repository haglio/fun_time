from __future__ import annotations

from fun_time_vr.main_hud import DEG_PER_PX
from fun_time_vr.satellite_hud import HUD, ITEM, hud_screen_name, screen_kind


class TestScreenNames:
    def test_a_satellites_hud_screen_is_named_after_its_satellite(self):
        assert screen_kind(hud_screen_name("portrait")) == HUD
        assert screen_kind("portrait") == ITEM


def test_the_hud_hangs_at_the_main_huds_own_pixel_scale():
    assert 300 * DEG_PER_PX > 20.0
