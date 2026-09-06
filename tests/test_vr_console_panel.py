"""The console hanging in the headset: what goes on it, and how it is composed."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from player_core.console import ConsoleModel
from player_core.console_hud import OSR2_ROBOT_HAND, ConsoleHud, ConsolePainter, ModeHud
from player_core.drive_layout import SPEED
from player_core.drive_readout import DriveHud
from player_core.timeline import TIMELINE_HEIGHT, bar_track_x
from player_core.volume import CHIP_H, CHIP_W, PAD, SPEAKER_W, VolumeHud, VolumeHudPainter, chip_xy

from fun_time_vr.console_panel import (
    PANEL_WIDTH_PX,
    PanelPointer,
    paint_panel,
    panel_hud,
    panel_painter,
)
from satellite.pointer import time_at


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

    def test_in_genau_mode_the_readout_is_genaus_own_motion(self):
        engine = _engine_console("genau")

        assert _hud(engine).drive is engine.drive

    def test_in_genau_mode_the_gate_is_told_nothing_was_published(self):
        """The video waits paused behind the clip while Genau's wave moves on,
        so every forecast the gate held for it is void by the time the video
        is watched again."""
        gate = FakeGate()

        _hud(_engine_console("genau"), gate=gate)

        assert gate.asked == [None]


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

    def test_a_video_gets_its_scrubber_along_the_lower_edge(self):
        lower = np.asarray(_paint())[-TIMELINE_HEIGHT:]

        assert lower[:, :, 3].max() > 0

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


def _live_console() -> ConsoleHud:
    """Video mode with the Robot Hand on the device, so the readout's bars take a press."""
    return ConsoleHud(
        modes=ModeHud(video="scene one"),
        console=ConsoleModel(mode="video", broker=True, locked=False, osr2=OSR2_ROBOT_HAND),
        drive=_drive(),
    )


class TestAPressOnThePanel:
    """The pointer's (u, v) on the hung panel, turned into what the desktop's
    console does under a mouse: buttons post, bars are held and dragged, the
    chip sets the level, the scrubber seeks."""

    _SCRUBBER = (1_000.0, 10_000.0)
    _CHIP = VolumeHud(volume=70, muted=False)

    def _pointer(self, *, chip=_CHIP, scrubber=_SCRUBBER):
        painter = panel_painter()
        hud = _hud(_live_console(), clip_title="")
        panel = paint_panel(painter, hud, scrubber=scrubber, chip=chip,
                            chip_painter=VolumeHudPainter())
        posted: list[str] = []
        seeks: list[float] = []
        pointer = PanelPointer(painter, post=posted.append, seek=seeks.append)
        pointer.painted(panel.size, scrubber=scrubber, chip=chip)
        return SimpleNamespace(pointer=pointer, painter=painter, posted=posted, seeks=seeks,
                               size=panel.size)

    @staticmethod
    def _uv(size, px: float, py: float) -> tuple[float, float]:
        width, height = size
        return (px + 0.5) / width, 1 - (py + 0.5) / height

    def _button_uv(self, p, action: str) -> tuple[float, float]:
        (x, y, w, h), _button = next(
            (rect, button) for rect, button in p.painter.buttons if button.action == action)
        return self._uv(p.size, x + w // 2, y + h // 2)

    def test_a_button_posts_its_command(self):
        p = self._pointer()

        p.pointer.press(*self._button_uv(p, "main_lock"))
        p.pointer.release()

        assert p.posted == ["main_lock"]
        assert p.seeks == []

    def test_a_press_on_the_chips_speaker_toggles_the_mute(self):
        p = self._pointer()
        x, y = chip_xy(win_w=p.size[0], win_h=p.size[1], timeline_h=TIMELINE_HEIGHT)

        p.pointer.press(*self._uv(p.size, x + 5, y + CHIP_H // 2))

        assert p.posted == ["audio_mute"]

        muted = self._pointer(chip=VolumeHud(volume=70, muted=True))
        muted.pointer.press(*self._uv(muted.size, x + 5, y + CHIP_H // 2))

        assert muted.posted == ["audio_unmute"]

    def test_a_press_on_the_chips_track_sets_the_level_and_a_drag_along_it_follows(self):
        p = self._pointer()
        x, y = chip_xy(win_w=p.size[0], win_h=p.size[1], timeline_h=TIMELINE_HEIGHT)
        track_x0, track_x1 = SPEAKER_W, CHIP_W - PAD
        halfway = x + (track_x0 + track_x1) / 2

        p.pointer.press(*self._uv(p.size, halfway, y + CHIP_H // 2))
        p.pointer.drag(*self._uv(p.size, halfway, y + CHIP_H // 2))
        p.pointer.drag(*self._uv(p.size, x + track_x1 + 40, y + CHIP_H // 2))
        p.pointer.release()
        p.pointer.drag(*self._uv(p.size, halfway, y + CHIP_H // 2))

        assert p.posted == ["audio_set_volume|50", "audio_set_volume|100"]

    def test_a_press_on_the_scrubber_seeks_the_video(self):
        p = self._pointer()
        width, height = p.size
        x0, x1 = bar_track_x(width)
        px = (x0 + x1) / 2

        p.pointer.press(*self._uv(p.size, px, height - TIMELINE_HEIGHT // 2))

        assert p.seeks == [pytest.approx(time_at(px, win_w=width, duration_ms=10_000.0))]
        assert 4_000 < p.seeks[0] < 6_000
        assert p.posted == []

    def test_a_clip_has_no_scrubber_to_seek(self):
        p = self._pointer(scrubber=None)
        width, height = p.size

        p.pointer.press(*self._uv(p.size, width // 2, height - 2))

        assert p.seeks == []

    def test_a_readouts_bar_is_held_and_dragged(self):
        p = self._pointer()
        speed = next(track for track in p.painter.tracks if track.axis == SPEED)
        x, y, w, h = speed.rect

        p.pointer.press(*self._uv(p.size, x + w * 0.25, y + h / 2))
        p.pointer.drag(*self._uv(p.size, x + w * 0.25, y + h / 2))
        p.pointer.drag(*self._uv(p.size, x + w * 0.75, y + h / 2))
        p.pointer.release()
        p.pointer.drag(*self._uv(p.size, x + w * 0.5, y + h / 2))

        assert len(p.posted) == 2
        assert all(command.startswith("robot_hand_speed_") for command in p.posted)
        assert p.posted[0] != p.posted[1]

    def test_the_tooltip_stays_put_while_the_pointer_wanders_over_one_button(self):
        p = self._pointer()
        u, v = self._button_uv(p, "main_lock")
        nudge = 2 / p.size[0]

        anchor = p.pointer.tooltip_anchor((u, v))

        assert anchor is not None
        assert p.pointer.tooltip_anchor((u + nudge, v)) == anchor
        assert p.pointer.tooltip_anchor(self._button_uv(p, "main_next")) != anchor
        assert p.pointer.tooltip_anchor((0.5, -0.5)) is None
        assert p.pointer.tooltip_anchor(None) is None
