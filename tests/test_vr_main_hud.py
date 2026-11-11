from __future__ import annotations

from funestra.hud_placement import HudEdge
from funestra.main_hud import MainHudModel, ModeHud, _row_width, osr2_controls
from funestra.main_hud_painter import _PAD, MainHud, MainHudPainter
from funestra.osr2_visualizer import DriveHud

from fun_time.main_hud_buttons import MainFunestraFacts, main_hud_rows
from fun_time_vr.main_hud import HUD_WIDTH_PX, hud_hangs_from


def _the_main_hud_in(mode: str) -> MainHud:
    return MainHud(
        modes=ModeHud(video="scene one"),
        main_hud_model=MainHudModel(main_mode=mode, locked=False,
                                    rows=main_hud_rows(MainFunestraFacts(main_mode=mode, locked=False),
                                                      in_vr=True),
                                    osr2_controls=osr2_controls(broker_running=True)),
        drive=DriveHud(speed=50, amplitude=60, center=50, shape="sine", position=1000,
                       advance_interval=10, waveform=tuple([0.5] * 80), trace_seconds=12.0),
    )


class TestTheHeldWidth:
    def test_no_row_is_wide_enough_to_make_the_painter_widen_past_it(self):
        widest_row_in = {
            mode: _row_width(main_hud_rows(MainFunestraFacts(main_mode=mode, latest=False,
                                                   plays_vr=True, plays_flat=True), in_vr=True))
            for mode in ("kino", "genau")
        }
        kinos_widest_row = widest_row_in["kino"]

        assert kinos_widest_row == max(widest_row_in.values())
        assert kinos_widest_row + 2 * _PAD <= HUD_WIDTH_PX

    def test_it_clears_the_widest_mode(self):
        for mode in ("kino", "genau"):
            assert MainHudPainter().rgba(_the_main_hud_in(mode))[1][0] <= HUD_WIDTH_PX

    def test_a_change_of_mode_never_resizes_it_in_the_scene(self):
        painter = MainHudPainter(width=HUD_WIDTH_PX)

        kino = painter.rgba(_the_main_hud_in("kino"))[1]
        genau = painter.rgba(_the_main_hud_in("genau"))[1]

        assert kino[0] == genau[0] == HUD_WIDTH_PX


class TestWhichSideTheConsoleHangsOn:
    def test_it_takes_the_satellite_its_keys_named(self):
        for edge in HudEdge:
            assert hud_hangs_from(edge, wrapped=False) is edge

    def test_a_wrapped_video_leaves_it_under_the_dashboard(self):
        for edge in HudEdge:
            assert hud_hangs_from(edge, wrapped=True) is HudEdge.LOWER

