"""The console hung in the headset: what goes on it, and how it is composed."""
from __future__ import annotations

import numpy as np
from player_core.console import ConsoleModel
from player_core.console_hud import ConsoleHud, ConsolePainter, ModeHud
from player_core.drive_readout import DriveHud
from player_core.timeline import TIMELINE_HEIGHT
from player_core.volume import VolumeHud, VolumeHudPainter

from fun_time_vr.console_panel import (
    PANEL_ELEVATION_DEG,
    PANEL_WIDTH_DEG,
    paint_panel,
    panel_hud,
)
from fun_time_vr.scene import PRIMARY_WIDTH_DEG


def _drive() -> DriveHud:
    return DriveHud(speed=50, amplitude=60, center=50, shape="sine", position=1000,
                    advance_interval=10, waveform=tuple([0.5] * 80), trace_seconds=12.0)


def _engine_console(mode: str) -> ConsoleHud:
    """What Genau's engine composes: the clip's name on top, the room and the
    drive under it."""
    return ConsoleHud(
        modes=ModeHud(video="scene one"),
        console=ConsoleModel(mode=mode, broker=True, locked=False),
        drive=_drive(),
    )


class TestWhatThePanelNames:
    def test_under_a_video_the_top_line_is_the_videos_name(self):
        hud = panel_hud(_engine_console("video"), video_title="feature", clip_title="scene one",
                        loading=None)

        assert hud.modes.video == "feature"

    def test_in_genau_mode_it_is_the_clips(self):
        hud = panel_hud(_engine_console("genau"), video_title="feature", clip_title="scene one",
                        loading=None)

        assert hud.modes.video == "scene one"

    def test_a_clip_still_decoding_is_named_as_such(self):
        hud = panel_hud(_engine_console("genau"), video_title="feature", clip_title="scene one",
                        loading="Loading scene two.mp4")

        assert hud.modes.video == "Loading scene two.mp4"

    def test_a_decode_does_not_rename_the_video(self):
        hud = panel_hud(_engine_console("video"), video_title="feature", clip_title="scene one",
                        loading="Loading scene two.mp4")

        assert hud.modes.video == "feature"

    def test_everything_under_the_top_line_is_the_engines(self):
        engine = _engine_console("video")

        hud = panel_hud(engine, video_title="feature", clip_title="scene one", loading=None)

        assert hud.console is engine.console
        assert hud.drive is engine.drive

    def test_with_no_engine_console_the_panel_still_names_what_is_playing(self):
        """The broker has the room: no drive of Genau's own, but a panel."""
        hud = panel_hud(None, video_title="feature", clip_title="scene one", loading=None)

        assert hud.modes.video == "feature"
        assert hud.drive is None


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


class TestHowItIsComposed:
    def _paint(self, *, scrubber, chip=_A_LEVEL):
        hud = panel_hud(_engine_console("video"), video_title="feature", clip_title="",
                        loading=None)
        return paint_panel(ConsolePainter(), hud, scrubber=scrubber, chip=chip,
                           chip_painter=VolumeHudPainter())

    def test_the_console_sits_on_top_and_the_furniture_row_under_it(self):
        with_row = self._paint(scrubber=(1_000.0, 10_000.0))
        console_rgba, (console_w, console_h) = ConsolePainter().rgba(
            panel_hud(_engine_console("video"), video_title="feature", clip_title="", loading=None))

        assert with_row.width == console_w
        assert with_row.height > console_h + TIMELINE_HEIGHT
        top = np.asarray(with_row)[:console_h]
        assert np.array_equal(top, np.frombuffer(console_rgba, dtype=np.uint8).reshape(console_h, console_w, 4))

    def test_a_video_gets_its_scrubber_along_the_bottom(self):
        with_row = self._paint(scrubber=(1_000.0, 10_000.0))
        bottom = np.asarray(with_row)[-TIMELINE_HEIGHT:]

        assert bottom[:, :, 3].max() > 0

    def test_a_clip_gets_no_scrubber(self):
        """It loops; there is nothing to seek.  The row is only as tall as the
        chip."""
        without = self._paint(scrubber=None)
        with_row = self._paint(scrubber=(1_000.0, 10_000.0))

        assert without.height < with_row.height

    def test_the_chip_is_at_the_right_end_of_the_row(self):
        painted = self._paint(scrubber=(1_000.0, 10_000.0))
        chip = VolumeHudPainter().bgra(VolumeHud(volume=70, muted=False))
        pixels = np.asarray(painted)
        console_h = painted.height - TIMELINE_HEIGHT - chip.shape[0]

        # Somewhere in the row under the console, on the right, is a chip pixel.
        row = pixels[console_h:, painted.width - chip.shape[1]:]
        assert row[:, :, 3].max() > 0

    def test_the_chip_shows_the_level_it_was_given(self):
        loud = np.asarray(self._paint(scrubber=None, chip=VolumeHud(volume=100, muted=False)))
        quiet = np.asarray(self._paint(scrubber=None, chip=VolumeHud(volume=10, muted=False)))

        assert not np.array_equal(loud, quiet)
