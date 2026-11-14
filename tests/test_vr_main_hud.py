from __future__ import annotations

import pytest
from funestra.hud_kinds import HudFacts, HudKind
from funestra.hud_placement import HudEdge
from funestra.modes import MainMode
from funestra.satellite_hud import HudModel
from funestra.satellite_hud_paint import DrawnWith, HudRenderer

from fun_time_vr.main_hud import HUD_WIDTH_PX, hud_hangs_from


def _width_of_the_main_hud_in(mode: MainMode) -> int:
    model = HudModel(funestra="main", kind=HudKind.MAIN, title="Unlocked",
                     facts=HudFacts(main_mode=mode, locked=False, latest=False, plays_vr=True,
                                    plays_flat=True, in_vr=True, flick_one="pip",
                                    flick_many="pips"))
    return HudRenderer("main").render(model, drawn_with=DrawnWith(width=HUD_WIDTH_PX)).bgra.shape[1]


class TestTheHeldWidth:
    @pytest.mark.parametrize("mode", list(MainMode))
    def test_no_row_is_wide_enough_to_widen_the_hud_past_the_headsets_width(self, mode):
        assert _width_of_the_main_hud_in(mode) == HUD_WIDTH_PX


class TestWhichSideTheConsoleHangsOn:
    def test_it_takes_the_satellite_its_keys_named(self):
        for edge in HudEdge:
            assert hud_hangs_from(edge, wrapped=False) is edge

    def test_a_wrapped_video_leaves_it_under_the_dashboard(self):
        for edge in HudEdge:
            assert hud_hangs_from(edge, wrapped=True) is HudEdge.LOWER

