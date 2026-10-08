"""The console hanging in the headset: what goes on it, and how it is composed."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
from player_core.console import ConsoleModel, _row_width
from player_core.console_hud import _PAD, ConsoleHud, ConsolePainter, ModeHud
from player_core.drive_layout import SPEED
from player_core.drive_readout import DriveHud
from player_core.hud_placement import HudEdge
from player_core.hud_status import F_MODE_LABEL
from player_core.modes import MainMode, Osr2State

from fun_time.console_buttons import MainSlot, console_rows, osr2_controls
from fun_time_vr.console_panel import (
    PANEL_WIDTH_PX,
    PanelPointer,
    paint_panel,
    panel_hangs_from,
    panel_hud,
    panel_painter,
)


def _drive(**over) -> DriveHud:
    fields = dict(speed=50, amplitude=60, center=50, shape="sine", position=1000,
                  advance_interval=10, waveform=tuple([0.5] * 80), trace_seconds=12.0)
    fields.update(over)
    return DriveHud(**fields)


def _published(main_mode, **fields) -> ConsoleModel:
    """The panel Fun Time publishes for *main_mode*, its buttons declared and
    the broker up."""
    return ConsoleModel(main_mode=main_mode, locked=False,
                        rows=console_rows(MainSlot(main_mode=main_mode, locked=False),
                                          in_vr=True),
                        osr2_controls=osr2_controls(broker=True), **fields)


def _engine_console(mode: str, osr2: str = "robot_hand") -> ConsoleHud:
    """What Genau's engine composes: the clip's name on top, the room and the
    drive under it."""
    return ConsoleHud(
        modes=ModeHud(video="scene one"),
        console=_published(mode, osr2=osr2),
        drive=_drive(),
    )


class FakeGate:
    """What the panel asks of the drive gate, answered with a readout that can
    be told from the engine's."""

    def __init__(self) -> None:
        self.asked: list[DriveHud | None] = []
        self.told_the_device_drives_itself: list[bool] = []

    def readout(self, published: DriveHud | None, *,
                device_drives_itself: bool = False) -> DriveHud:
        self.asked.append(published)
        self.told_the_device_drives_itself.append(device_drives_itself)
        return _drive(speed=99)


def _hud(engine, *, gate=None, video_title="feature", clip_title="scene one", loading=None,
         scripted_filter=False, playback_speed=1.0):
    return panel_hud(engine, video_title=video_title, clip_title=clip_title,
                     loading=loading, drive_gate=gate or FakeGate(), scripted_filter=scripted_filter,
                     playback_speed=playback_speed)


class TestWhatThePanelNames:
    def test_under_a_video_the_top_line_is_the_videos_name(self):
        assert _hud(_engine_console("kino")).modes.video == "feature"

    def test_in_genau_mode_it_is_the_clips(self):
        assert _hud(_engine_console("genau")).modes.video == "scene one"

    def test_a_clip_still_decoding_is_named_as_such(self):
        hud = _hud(_engine_console("genau"), loading="Loading scene two.mp4")

        assert hud.modes.video == "Loading scene two.mp4"

    def test_a_decode_does_not_rename_the_video(self):
        hud = _hud(_engine_console("kino"), loading="Loading scene two.mp4")

        assert hud.modes.video == "feature"

    def test_the_room_under_the_top_line_is_the_engines(self):
        engine = _engine_console("kino")

        assert _hud(engine).console == replace(engine.console, playback_speed=1.0)

    def test_the_rate_on_the_row_is_the_primarys_not_the_engines(self):
        """The row draws ConsoleModel.playback_speed, and the console the panel
        composes is Genau's engine's, where that field never leaves its default
        -- so the primary's rate has to be folded in or the readout says 1x
        however the video is actually playing."""
        hud = _hud(_engine_console("kino"), playback_speed=1.5)

        assert hud.console.playback_speed == 1.5

    def test_the_rate_is_folded_in_in_genau_mode_too(self):
        """The rate belongs to the video, which waits paused under the clip and
        resumes at it -- so it is carried whether or not Genau is covering it,
        even though the genau console draws the clip seconds row instead."""
        hud = _hud(_engine_console("genau"), playback_speed=0.5)

        assert hud.console.playback_speed == 0.5

    def test_the_rate_survives_having_no_engine_console_at_all(self):
        assert _hud(None, playback_speed=2.0).console.playback_speed == 2.0

    def test_with_no_engine_console_the_panel_still_names_what_is_playing(self):
        """The broker has the room: no console of Genau's own, but a panel."""
        assert _hud(None).modes.video == "feature"


class TestTheDeviceRunningItself:
    """Auto mode reaches the headset's panel exactly as it reaches the desktop's:
    the gate is told, so the picture is the device's own motion rather than a
    script folded over it."""

    def test_the_gate_is_told_under_a_video(self):
        gate = FakeGate()

        _hud(_engine_console("kino", osr2=Osr2State.AUTO), gate=gate)

        assert gate.told_the_device_drives_itself == [True]

    def test_the_gate_is_told_in_genau_mode_too(self):
        """The gate is asked in both modes -- told nothing was published in genau
        mode, so its forecasts are voided there rather than left standing."""
        gate = FakeGate()

        _hud(_engine_console("genau", osr2=Osr2State.AUTO), gate=gate)

        assert gate.told_the_device_drives_itself == [True]

    def test_every_other_state_composes_as_before(self):
        gate = FakeGate()

        _hud(_engine_console("kino", osr2=Osr2State.FUNSCRIPT), gate=gate)

        assert gate.told_the_device_drives_itself == [False]


class TestFModeOnTheStatusLine:
    """The word for the switch the F key throws.

    The console model Fun Time publishes lights the F button, but the status
    line above it is read off the player DRAWING the console — the main player's own copy
    on the desktop, and here the main role's.  Left out, F-mode was a button
    that lit with nothing said beside it, and the key read as doing nothing.
    """

    def test_the_line_says_it_under_a_video(self):
        assert F_MODE_LABEL in _hud(_engine_console("kino"), scripted_filter=True).status_line

    def test_the_line_leaves_it_out_when_it_is_off(self):
        assert F_MODE_LABEL not in _hud(_engine_console("kino"), scripted_filter=False).status_line

    def test_it_is_the_main_players_flag_and_so_not_said_over_a_clip(self):
        """In genau mode the main player's playlist is not what is on screen,
        and its narrowing is not what the line describes."""
        hud = _hud(_engine_console("genau"), scripted_filter=True)

        assert hud.modes.scripted_filter is False
        assert F_MODE_LABEL not in hud.status_line


class TestWhoseReadoutItDraws:
    def test_under_a_video_the_funscript_is_folded_into_the_engines_readout(self):
        """What the desktop's kino-mode console draws: the script's green over
        Genau's blue, by the gate that holds the picture's forecasts."""
        engine, gate = _engine_console("kino"), FakeGate()

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
        """The video waits paused under the clip while Genau's wave moves on,
        so every forecast the gate held for it is void by the time the video
        is watched again."""
        gate = FakeGate()

        _hud(_engine_console("genau"), gate=gate)

        assert gate.asked == [None]


def _paint(mode="kino", *, title="feature"):
    hud = _hud(_engine_console(mode), video_title=title, clip_title=title)
    return paint_panel(panel_painter(), hud)


class TestHowItIsComposed:
    def test_the_panel_is_the_console_itself(self):
        """Nothing rides above it: what the room heard is read on the dashboard,
        which is the one place in the headset that keeps the log."""
        painted = _paint()
        console_rgba, (console_w, console_h) = panel_painter().rgba(_hud(_engine_console("kino")))

        assert painted.size == (console_w, console_h)
        assert np.array_equal(
            np.asarray(painted),
            np.frombuffer(console_rgba, dtype=np.uint8).reshape(console_h, console_w, 4))


def test_the_held_width_covers_the_widest_row_the_console_can_build():
    """The painter widens past this constant for a row it cannot otherwise hold,
    so a constant short of the widest row makes the panel change size when the
    mode does — which is the one thing holding it fixed exists to prevent.  The
    kino-mode transport row is that row, and it grows every time the console
    grows a button."""
    # The rows the headset can show: its main player publishes no length mode,
    # so the length pair is never among them, while the shapes pair always is.
    widest = max(
        _row_width(console_rows(MainSlot(main_mode=mode, latest=False,
                                         plays_vr=True, plays_flat=True), in_vr=True))
        for mode in ("kino", "genau")
    )

    assert widest + 2 * _PAD <= PANEL_WIDTH_PX



class TestItKeepsItsSize:
    """He saw the panel change size between the modes: the console sized itself
    to its rows, and the genau-mode ones are narrower.  A screen in a scene that
    changes size is a screen that moves."""

    def test_the_two_modes_paint_the_same_size(self):
        kino, genau = _paint("kino"), _paint("genau")

        assert kino.size == genau.size == (PANEL_WIDTH_PX, kino.height)

    def test_a_long_title_does_not_widen_it(self):
        long_title = "Jane Doe - scene one - " + "a long descriptor " * 6

        assert _paint(title=long_title).size == _paint(title="short").size

    def test_the_held_width_clears_the_widest_mode(self):
        """Held narrower than its rows the painter widens the panel, and the
        two modes would differ again; the constant has to clear both."""
        for mode in ("kino", "genau"):
            assert ConsolePainter().rgba(_hud(_engine_console(mode)))[1][0] <= PANEL_WIDTH_PX


def _live_console() -> ConsoleHud:
    """Kino mode with the Robot Hand on the device, so the readout's bars take a press."""
    return ConsoleHud(
        modes=ModeHud(video="scene one"),
        console=_published(MainMode.KINO, osr2=Osr2State.ROBOT_HAND),
        drive=_drive(),
    )


class TestAPressOnThePanel:
    """The pointer's (u, v) on the hanging panel, turned into what the desktop's
    console does under a mouse: buttons post, bars are held and dragged, the
    chip sets the level, the scrubber seeks."""

    def _pointer(self):
        painter = panel_painter()
        panel = paint_panel(painter, _hud(_live_console(), clip_title=""))
        posted: list[str] = []
        pointer = PanelPointer(painter, post=posted.append)
        pointer.painted(panel.size)
        return SimpleNamespace(pointer=pointer, painter=painter, posted=posted, size=panel.size)

    @staticmethod
    def _uv(size, px: float, py: float) -> tuple[float, float]:
        width, height = size
        return (px + 0.5) / width, 1 - (py + 0.5) / height

    def _button_uv(self, p, action: str) -> tuple[float, float]:
        """A button's middle, in the panel's pixels -- the console's own, the
        panel being the console."""
        (x, y, w, h), _button = next(
            (rect, button) for rect, button in p.painter.buttons if button.command == action)
        return self._uv(p.size, x + w // 2, y + h // 2)

    def test_a_button_posts_its_command(self):
        p = self._pointer()

        p.pointer.press(*self._button_uv(p, "main_lock"))
        p.pointer.release()

        assert p.posted == ["main_lock"]

    def test_a_press_that_lands_on_no_button_asks_for_nothing(self):
        """Neither the scrubber nor the volume is here any more; each rides on the
        player it belongs to, so a press off the buttons reaches nothing."""
        p = self._pointer()
        width, height = p.size

        p.pointer.press(*self._uv(p.size, width - 2, height - 2))

        assert p.posted == []

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
