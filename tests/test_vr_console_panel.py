"""The console hung in the headset: what goes on it, and how it is composed."""
from __future__ import annotations

import numpy as np
from player_core.console import ConsoleModel
from player_core.console_hud import ConsoleHud, ConsolePainter, ModeHud
from player_core.drive_readout import DriveHud
from player_core.timeline import TIMELINE_HEIGHT
from player_core.volume import VolumeHud, VolumeHudPainter, chip_xy

from fun_time_vr.console_panel import (
    PANEL_ELEVATION_DEG,
    PANEL_WIDTH_DEG,
    PANEL_WIDTH_PX,
    paint_panel,
    panel_hud,
    panel_painter,
)
from fun_time_vr.scene import PRIMARY_WIDTH_DEG


def _drive(**over) -> DriveHud:
    fields = dict(speed=50, amplitude=60, center=50, shape="sine", position=1000,
                  advance_interval=10, waveform=tuple([0.5] * 80), trace_seconds=12.0)
    fields.update(over)
    return DriveHud(**fields)


def _engine_console(mode: str) -> ConsoleHud:
    """What Genau's engine composes: the clip's name on top, the room and the
    drive under it."""
    return ConsoleHud(
        modes=ModeHud(video="scene one"),
        console=ConsoleModel(mode=mode, broker=True, locked=False),
        drive=_drive(),
    )


class FakeGate:
    """What the panel asks of the drive gate, answered with a readout that can
    be told from the engine's."""

    def __init__(self) -> None:
        self.asked: list[DriveHud | None] = []

    def readout(self, published: DriveHud | None) -> DriveHud:
        self.asked.append(published)
        return _drive(speed=99)


def _hud(engine, *, gate=None, video_title="feature", clip_title="scene one", loading=None):
    return panel_hud(engine, video_title=video_title, clip_title=clip_title,
                     loading=loading, drive_gate=gate or FakeGate())


class TestWhatThePanelNames:
    def test_under_a_video_the_top_line_is_the_videos_name(self):
        assert _hud(_engine_console("video")).modes.video == "feature"

    def test_in_genau_mode_it_is_the_clips(self):
        assert _hud(_engine_console("genau")).modes.video == "scene one"

    def test_a_clip_still_decoding_is_named_as_such(self):
        hud = _hud(_engine_console("genau"), loading="Loading scene two.mp4")

        assert hud.modes.video == "Loading scene two.mp4"

    def test_a_decode_does_not_rename_the_video(self):
        hud = _hud(_engine_console("video"), loading="Loading scene two.mp4")

        assert hud.modes.video == "feature"

    def test_the_room_under_the_top_line_is_the_engines(self):
        engine = _engine_console("video")

        assert _hud(engine).console is engine.console

    def test_with_no_engine_console_the_panel_still_names_what_is_playing(self):
        """The broker has the room: no console of Genau's own, but a panel."""
        assert _hud(None).modes.video == "feature"


class TestWhoseReadoutItDraws:
    def test_under_a_video_the_funscript_is_folded_into_the_engines_readout(self):
        """What the desktop's video-mode console draws: the script's green over
        Genau's blue, by the gate that holds the picture's forecasts."""
        engine, gate = _engine_console("video"), FakeGate()

        hud = _hud(engine, gate=gate)

        assert gate.asked == [engine.drive]
        assert hud.drive == _drive(speed=99)

    def test_with_nothing_published_the_gate_still_draws_the_script(self):
        gate = FakeGate()

        hud = _hud(None, gate=gate)

        assert gate.asked == [None]
        assert hud.drive == _drive(speed=99)

    def test_in_genau_mode_the_readout_is_genaus_own_stroke(self):
        engine = _engine_console("genau")

        assert _hud(engine).drive is engine.drive

    def test_in_genau_mode_the_gate_is_told_nothing_was_published(self):
        """The video waits paused behind the clip while Genau's wave moves on,
        so every forecast the gate held for it is void by the time the video
        is watched again."""
        gate = FakeGate()

        _hud(_engine_console("genau"), gate=gate)

        assert gate.asked == [None]


class TestWhereItHangs:
    def test_it_hangs_above_the_primarys_top_edge(self):
        """A 16:9 primary spanning PRIMARY_WIDTH_DEG is this tall; the panel's
        center sits above its edge, so it covers neither the picture nor the
        action, which sits low in an immersive one."""
        primary_half_height_deg = PRIMARY_WIDTH_DEG / (16 / 9) / 2

        assert primary_half_height_deg < PANEL_ELEVATION_DEG

    def test_it_is_narrower_than_the_primary(self):
        assert PANEL_WIDTH_DEG < PRIMARY_WIDTH_DEG / 2


_A_LEVEL = VolumeHud(volume=70, muted=False)
_A_SCRUBBER = (1_000.0, 10_000.0)


def _paint(mode="video", *, scrubber=_A_SCRUBBER, chip=_A_LEVEL, title="feature"):
    hud = _hud(_engine_console(mode), video_title=title, clip_title=title)
    return paint_panel(panel_painter(), hud, scrubber=scrubber, chip=chip,
                       chip_painter=VolumeHudPainter())


def _chip_columns(painted) -> tuple[int, int]:
    """Where the chip sits across the furniture row."""
    x, _y = chip_xy(win_w=painted.width, win_h=painted.height, timeline_h=TIMELINE_HEIGHT)
    return x, x + VolumeHudPainter().bgra(_A_LEVEL).shape[1]


class TestHowItIsComposed:
    def test_the_console_sits_on_top_and_the_furniture_row_under_it(self):
        with_row = _paint()
        console_rgba, (console_w, console_h) = panel_painter().rgba(_hud(_engine_console("video")))

        assert with_row.width == console_w
        assert with_row.height > console_h + TIMELINE_HEIGHT
        top = np.asarray(with_row)[:console_h]
        assert np.array_equal(top, np.frombuffer(console_rgba, dtype=np.uint8).reshape(console_h, console_w, 4))

    def test_a_video_gets_its_scrubber_along_the_bottom(self):
        bottom = np.asarray(_paint())[-TIMELINE_HEIGHT:]

        assert bottom[:, :, 3].max() > 0

    def test_a_clip_gets_no_scrubber(self):
        """It loops; there is nothing to seek.  The row stays, empty but for
        the chip."""
        painted = _paint("genau", scrubber=None)
        left, _right = _chip_columns(painted)

        assert np.asarray(painted)[-TIMELINE_HEIGHT:, :left, 3].max() == 0

    def test_the_chip_is_at_the_right_end_of_the_row(self):
        painted = _paint("genau", scrubber=None)
        left, right = _chip_columns(painted)

        assert np.asarray(painted)[-TIMELINE_HEIGHT:, left:right, 3].max() > 0

    def test_the_chip_shows_the_level_it_was_given(self):
        loud = np.asarray(_paint(scrubber=None, chip=VolumeHud(volume=100, muted=False)))
        quiet = np.asarray(_paint(scrubber=None, chip=VolumeHud(volume=10, muted=False)))

        assert not np.array_equal(loud, quiet)


class TestItKeepsItsSize:
    """He saw the panel change size between the modes: the console sized itself
    to its rows, the genau-mode ones are narrower, and the scrubber row came and
    went.  A screen in a scene that changes size is a screen that moves."""

    def test_the_two_modes_paint_the_same_size(self):
        video = _paint("video")
        genau = _paint("genau", scrubber=None)

        assert video.size == genau.size == (PANEL_WIDTH_PX, video.height)

    def test_a_long_title_does_not_widen_it(self):
        long_title = "Jane Doe - scene one - " + "a long descriptor " * 6

        assert _paint(title=long_title).size == _paint(title="short").size

    def test_the_held_width_clears_the_widest_mode(self):
        """Held narrower than its rows the painter widens the panel, and the
        two modes would differ again; the constant has to clear both."""
        for mode in ("video", "genau"):
            assert ConsolePainter().rgba(_hud(_engine_console(mode)))[1][0] <= PANEL_WIDTH_PX
