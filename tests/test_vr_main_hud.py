"""The HUD hanging in the headset: the one width it is held to, and which side it hangs on."""
from __future__ import annotations

import logging

from funestra.hud_placement import HudEdge
from funestra.main_hud import MainHudModel, ModeHud, _row_width, osr2_controls
from funestra.main_hud_painter import _PAD, MainHud, MainHudPainter
from funestra.osr2_visualizer import DriveHud
from shared_ui.palette import GREEN, RED, TEXT_MUTED, TEXT_PRIMARY, YELLOW

from fun_time.event_log import FAVORITE, MESSAGE
from fun_time.main_hud_buttons import MainFunestraFacts, main_hud_rows
from fun_time_vr.main_hud import HUD_WIDTH_PX, hud_hangs_from, level_color


def _the_main_hud_in(mode: str) -> MainHud:
    """The HUD as the Funestra draws it for *mode*: the room's buttons
    declared and the broker up, with the OSR2's readout under them."""
    return MainHud(
        modes=ModeHud(video="scene one"),
        console=MainHudModel(main_mode=mode, locked=False,
                             rows=main_hud_rows(MainFunestraFacts(main_mode=mode, locked=False),
                                               in_vr=True),
                             osr2_controls=osr2_controls(broker_running=True)),
        drive=DriveHud(speed=50, amplitude=60, center=50, shape="sine", position=1000,
                       advance_interval=10, waveform=tuple([0.5] * 80), trace_seconds=12.0),
    )


class TestTheHeldWidth:
    """He saw the HUD change size between the modes: the HUD sized itself
    to its rows, and the genau-mode ones are narrower.  A screen in a scene that
    changes size is a screen that moves, so the Funestra holds it to one width."""

    def test_it_covers_the_widest_row_the_main_hud_can_build(self):
        """The painter widens past this constant for a row it cannot otherwise
        hold, so a constant short of the widest row makes the HUD change size
        when the mode does.  The kino-mode transport row is that row, and it
        grows every time the HUD grows a button."""
        widest = max(
            _row_width(main_hud_rows(MainFunestraFacts(main_mode=mode, latest=False,
                                             plays_vr=True, plays_flat=True), in_vr=True))
            for mode in ("kino", "genau")
        )

        assert widest + 2 * _PAD <= HUD_WIDTH_PX

    def test_it_clears_the_widest_mode(self):
        """Held narrower than its rows the painter widens the HUD, and the
        two modes would differ again; the constant has to clear both."""
        for mode in ("kino", "genau"):
            assert MainHudPainter().rgba(_the_main_hud_in(mode))[1][0] <= HUD_WIDTH_PX

    def test_the_two_modes_paint_the_same_size_at_it(self):
        painter = MainHudPainter(width=HUD_WIDTH_PX)

        kino = painter.rgba(_the_main_hud_in("kino"))[1]
        genau = painter.rgba(_the_main_hud_in("genau"))[1]

        assert kino[0] == genau[0] == HUD_WIDTH_PX


class TestWhichSideTheConsoleHangsOn:
    """The keys move this HUD round the sides of the Main Funestra.  A video that
    wraps the viewer leaves no edge to hang against, so there it hangs
    under the dashboard as it always has."""

    def test_it_takes_the_satellite_its_keys_named(self):
        for edge in HudEdge:
            assert hud_hangs_from(edge, wrapped=False) is edge

    def test_a_wrapped_video_leaves_it_under_the_dashboard(self):
        for edge in HudEdge:
            assert hud_hangs_from(edge, wrapped=True) is HudEdge.LOWER


class TestTheColorOfANotice:
    def test_each_level_has_the_desktops_own_color(self):
        assert level_color(MESSAGE) == TEXT_PRIMARY
        assert level_color(FAVORITE) == GREEN
        assert level_color(logging.WARNING) == YELLOW
        assert level_color(logging.ERROR) == RED

    def test_a_level_between_two_takes_the_loudest_it_reaches(self):
        assert level_color(logging.WARNING + 1) == YELLOW
        assert level_color(logging.DEBUG) == TEXT_MUTED
