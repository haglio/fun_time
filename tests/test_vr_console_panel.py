"""The console hanging in the headset: the one width it is held to, and which side it hangs on."""
from __future__ import annotations

import logging

from player_core.console import ConsoleModel, _row_width
from player_core.console_hud import _PAD, ConsoleHud, ConsolePainter, ModeHud
from player_core.drive_readout import DriveHud
from player_core.hud_placement import HudEdge
from shared_ui.palette import AMBER, GREEN, RED, TEXT_MUTED, TEXT_PRIMARY

from fun_time.console_buttons import MainSlot, console_rows, osr2_controls
from fun_time.event_log import FAVORITE, NOTICE
from fun_time_vr.console_panel import PANEL_WIDTH_PX, level_color, panel_hangs_from


def _the_console_in(mode: str) -> ConsoleHud:
    """The console as the Funestra draws it for *mode*: the room's buttons
    declared and the broker up, with the OSR2's readout under them."""
    return ConsoleHud(
        modes=ModeHud(video="scene one"),
        console=ConsoleModel(main_mode=mode, locked=False,
                             rows=console_rows(MainSlot(main_mode=mode, locked=False),
                                               in_vr=True),
                             osr2_controls=osr2_controls(broker=True)),
        drive=DriveHud(speed=50, amplitude=60, center=50, shape="sine", position=1000,
                       advance_interval=10, waveform=tuple([0.5] * 80), trace_seconds=12.0),
    )


class TestTheHeldWidth:
    """He saw the panel change size between the modes: the console sized itself
    to its rows, and the genau-mode ones are narrower.  A screen in a scene that
    changes size is a screen that moves, so the Funestra holds it to one width."""

    def test_it_covers_the_widest_row_the_console_can_build(self):
        """The painter widens past this constant for a row it cannot otherwise
        hold, so a constant short of the widest row makes the panel change size
        when the mode does.  The kino-mode transport row is that row, and it
        grows every time the console grows a button."""
        widest = max(
            _row_width(console_rows(MainSlot(main_mode=mode, latest=False,
                                             plays_vr=True, plays_flat=True), in_vr=True))
            for mode in ("kino", "genau")
        )

        assert widest + 2 * _PAD <= PANEL_WIDTH_PX

    def test_it_clears_the_widest_mode(self):
        """Held narrower than its rows the painter widens the panel, and the
        two modes would differ again; the constant has to clear both."""
        for mode in ("kino", "genau"):
            assert ConsolePainter().rgba(_the_console_in(mode))[1][0] <= PANEL_WIDTH_PX

    def test_the_two_modes_paint_the_same_size_at_it(self):
        painter = ConsolePainter(width=PANEL_WIDTH_PX)

        kino = painter.rgba(_the_console_in("kino"))[1]
        genau = painter.rgba(_the_console_in("genau"))[1]

        assert kino[0] == genau[0] == PANEL_WIDTH_PX


class TestWhichSideTheConsoleHangsOn:
    """The keys move this panel round the sides of the main player.  A video that
    wraps the viewer leaves no picture edge to hang against, so there it hangs
    under the dashboard as it always has."""

    def test_it_takes_the_side_its_keys_named(self):
        for edge in HudEdge:
            assert panel_hangs_from(edge, wrapped=False) is edge

    def test_a_wrapped_video_leaves_it_under_the_dashboard(self):
        for edge in HudEdge:
            assert panel_hangs_from(edge, wrapped=True) is HudEdge.LOWER


class TestTheColorOfANotice:
    def test_each_level_has_the_desktops_own_color(self):
        assert level_color(NOTICE) == TEXT_PRIMARY
        assert level_color(FAVORITE) == GREEN
        assert level_color(logging.WARNING) == AMBER
        assert level_color(logging.ERROR) == RED

    def test_a_level_between_two_takes_the_loudest_it_reaches(self):
        assert level_color(logging.WARNING + 1) == AMBER
        assert level_color(logging.DEBUG) == TEXT_MUTED
